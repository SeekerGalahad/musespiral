"""控制流节点测试（ARCH-4 §一 switch / §二 loop——本批测试硬要求 15-17/21）。

覆盖：
  a) switch 插件形式：duration>300 / ≤300 两场景走不同分支（硬要求 15），
     steps 只含选中分支节点，未选分支零执行（log 留痕）；
  b) switch 条件形式：引擎求值 ``节点.端口 > 字面值`` → true/false 分支；
     路由值 put 进 workspace，switch 后顶层续跑（分支汇聚）；
  c) switch 红路径：route 无对应分支（运行期可读）/ condition+plugin 二选一/
     非 control 插件/branch 缺失/未知 type/条件语法/$item 作用域/max_iters
     （plan 期零 run 目录）；
  d) loop：3 段输入 → 3 次 body → 合并列表顺序保持 → 下游 sink 消费
     （硬要求 16）；显式/隐式 loop_over 两种引用形；空列表 0 次迭代；
  e) loop 上限：超 max_iters 判失败可读（硬要求 17）；默认 100 生效；
     body 中途失败即停；
  f) 向后兼容：既有线性流水线零改动正常跑（硬要求 21）；meta node_specs
     控制节点落完整 spec；控制流节点重生成拒（可读）；
  g) manifest：kind=control 必须声明 route 输出（parse_manifest 红绿）。

全部用例在 MUSESPIRAL_HOME 沙箱跑（test_pipeline_plugins 同款 autouse 纪律，
不触真实 ~/.musespiral）；假插件写进 tmp 插件区，python runtime 进程内加载，
零外部网络、零子进程。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.plugins.manifest import parse_manifest
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services.pipeline_service_plugins import (
    PipelinePlanError,
    RegenerateError,
    precheck_regenerate,
    regenerate_node,
)

# ── 沙箱与夹具 ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "pipeline_runs"


def _write_plugin(root: Path, dirname: str, manifest: dict, code: str) -> Path:
    d = root / dirname
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / "plugin.py").write_text(code, encoding="utf-8")
    return d


# ── switch 夹具族：seg-source → duration-switch → long/short sink ──

_SEG_SOURCE_CODE = """
DURATION = {duration!r}

def run(inputs, config, ctx):
    return {{
        "segments": [{{"index": 0, "text": "alpha"}}, {{"index": 1, "text": "beta"}}],
        "duration_s": DURATION,
    }}
"""

_DURATION_SWITCH_CODE = """
BAD_ROUTE = {bad_route!r}

def run(inputs, config, ctx):
    threshold = config.get("threshold", 300)
    if BAD_ROUTE:
        return {{"route": "medium"}}  # 无对应分支的红路径
    return {{"route": "long" if inputs["duration_s"] > threshold else "short"}}
"""

_SIDE_SINK_CODE = """
SIDE = {side!r}

def run(inputs, config, ctx):
    return {{"article": f"# {{SIDE}} 分支产物 route={{inputs['route']}}"}}
"""


def _install_switch_plugins(
    root: Path, *, duration: float = 450.0, bad_route: bool = False
) -> None:
    _write_plugin(
        root, "seg-source",
        {
            "id": "seg-source", "version": "0.1.0", "kind": "source",
            "inputs": {},
            "outputs": {"segments": {"type": "segments"}, "duration_s": {"type": "number"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _SEG_SOURCE_CODE.format(duration=duration),
    )
    _write_plugin(
        root, "duration-switch",
        {
            "id": "duration-switch", "version": "0.1.0", "kind": "control",
            "name": {"zh": "时长路由"},
            "inputs": {"duration_s": {"type": "number", "required": True}},
            "outputs": {"route": {"type": "string"}},
            "config_schema": {"threshold": {"type": "number", "default": 300}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _DURATION_SWITCH_CODE.format(bad_route=bad_route),
    )
    for side in ("long", "short", "true", "false"):
        _write_plugin(
            root, f"{side}-sink",
            {
                "id": f"{side}-sink", "version": "0.1.0", "kind": "sink",
                "inputs": {"route": {"type": "string", "required": True}},
                "outputs": {"article": {"type": "text/markdown"}},
                "runtime": {"type": "python", "entry": "plugin.py:run"},
            },
            _SIDE_SINK_CODE.format(side=side),
        )


def _switch_pipeline(plugin_form: bool = True) -> list[dict]:
    """switch 节点 DSL 形：插件形式 {id, plugin, branch:{long,short}}（正典形，
    不写 type 由 branch 键推断）；条件形式 {id, type, condition, branch:{true,false}}。"""
    if plugin_form:
        switch: dict = {
            "id": "switch1",
            "plugin": "duration-switch",
            "branch": {"long": [{"id": "out-long", "plugin": "long-sink"}],
                       "short": [{"id": "out-short", "plugin": "short-sink"}]},
        }
    else:
        switch = {
            "id": "switch1", "type": "switch", "condition": "src.duration_s > 300",
            "branch": {"true": [{"id": "out-true", "plugin": "true-sink"}],
                       "false": [{"id": "out-false", "plugin": "false-sink"}]},
        }
    return [
        {"id": "src", "plugin": "seg-source"},
        switch,
    ]


# ── loop 夹具族：list-source → loop(body: str-refine) → str-sink ──

_STR_SOURCE_CODE = """
ITEMS = {items!r}

def run(inputs, config, ctx):
    return {{"items": list(ITEMS)}}
"""

_STR_REFINE_CODE = """
BOOM = {boom!r}

def run(inputs, config, ctx):
    item = inputs["item"]
    if BOOM and item == "boom":
        raise RuntimeError("该项炸了")
    return {{"out": item.upper()}}
"""

_STR_SINK_CODE = """
def run(inputs, config, ctx):
    outs = inputs["out"]
    return {"article": f"{len(outs)}:" + ",".join(outs)}
"""


def _install_loop_plugins(root: Path, *, items: list[str], boom: bool = False) -> None:
    _write_plugin(
        root, "list-source",
        {
            "id": "list-source", "version": "0.1.0", "kind": "source",
            "inputs": {},
            "outputs": {"items": {"type": "string[]"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _STR_SOURCE_CODE.format(items=items),
    )
    _write_plugin(
        root, "str-refine",
        {
            "id": "str-refine", "version": "0.1.0", "kind": "process",
            "inputs": {"item": {"type": "string", "required": True}},
            "outputs": {"out": {"type": "string"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _STR_REFINE_CODE.format(boom=boom),
    )
    _write_plugin(
        root, "str-sink",
        {
            "id": "str-sink", "version": "0.1.0", "kind": "sink",
            "inputs": {"out": {"type": "string", "required": True}},
            "outputs": {"article": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _STR_SINK_CODE,
    )


# ── a) switch 插件形式（硬要求 15：>300 / ≤300 两场景走不同分支）──


def test_switch_plugin_form_routes_long(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root, duration=450.0)
    result = psp.run_pipeline(
        _switch_pipeline(), plugins_root=plugins_root, run_root=runs_root, run_id="sw-long-0001"
    )
    assert result["status"] == "done"
    # steps 只含选中分支：src → switch1 → out-long（short 分支零执行）
    assert [s["step"] for s in result["steps"]] == ["src", "switch1", "out-long"]
    assert all(s["status"] == "ok" for s in result["steps"])
    assert "route=long" in result["steps"][1]["detail"]
    # 分支产物收集（terminal=分支尾）+ log 留痕未执行分支
    article = json.loads(
        (runs_root / "sw-long-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert article == "# long 分支产物 route=long"
    logs = (runs_root / "sw-long-0001" / "log.jsonl").read_text("utf-8")
    assert "未执行分支" in logs and "'short'" in logs


def test_switch_plugin_form_routes_short(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root, duration=120.0)  # ≤300 → short 分支
    result = psp.run_pipeline(
        _switch_pipeline(), plugins_root=plugins_root, run_root=runs_root, run_id="sw-short-0001"
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["src", "switch1", "out-short"]
    assert "route=short" in result["steps"][1]["detail"]
    article = json.loads(
        (runs_root / "sw-short-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert "short" in article
    # 未走的 long 分支节点不落 workspace
    assert not (runs_root / "sw-short-0001" / "workspace" / "out-long").exists()


def test_switch_threshold_config_overrides_default(plugins_root: Path, runs_root: Path) -> None:
    """threshold 走节点 config 覆盖 manifest 缺省（450>200 → long）。"""
    _install_switch_plugins(plugins_root, duration=450.0)
    nodes = _switch_pipeline()
    nodes[1]["config"] = {"threshold": 500}
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="sw-cfg-0001"
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]][-1] == "out-short"  # 450 ≤ 500 → short


# ── b) switch 条件形式（引擎求值；路由值可被下游汇聚消费）──────────


def test_switch_condition_form_true_branch(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root, duration=450.0)
    result = psp.run_pipeline(
        _switch_pipeline(plugin_form=False),
        plugins_root=plugins_root, run_root=runs_root, run_id="sw-cond-t-0001",
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["src", "switch1", "out-true"]
    assert "condition 'src.duration_s > 300'" in result["steps"][1]["detail"]
    assert "route=true" in result["steps"][1]["detail"]


def test_switch_condition_form_false_branch_and_convergence(
    plugins_root: Path, runs_root: Path
) -> None:
    """false 分支 + switch 后顶层续跑（汇聚）：下游消费 condition 形式 put 的 route。"""
    _install_switch_plugins(plugins_root, duration=120.0)
    nodes = [
        {"id": "src", "plugin": "seg-source"},
        {
            "id": "switch1", "type": "switch", "condition": "src.duration_s > 300",
            "branch": {"true": [{"id": "out-true", "plugin": "true-sink"}],
                       "false": [{"id": "out-false", "plugin": "false-sink"}]},
        },
        {"id": "after", "plugin": "false-sink"},  # 汇聚节点：route ← switch 产物
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="sw-cond-f-0001"
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["src", "switch1", "out-false", "after"]
    article = json.loads(
        (runs_root / "sw-cond-f-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert article == "# false 分支产物 route=false"  # 汇聚节点吃到 condition put 的路由值


# ── c) switch 红路径 ───────────────────────────────────────────────


def test_switch_unknown_route_fails_readably(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root, duration=450.0, bad_route=True)
    result = psp.run_pipeline(
        _switch_pipeline(), plugins_root=plugins_root, run_root=runs_root, run_id="sw-bad-0001"
    )
    assert result["status"] == "failed" and result["failed_at"] == "switch1"
    detail = result["steps"][1]["detail"]
    assert "无对应分支" in detail and "medium" in detail and "long" in detail


def test_switch_plan_red_paths(plugins_root: Path) -> None:
    """plan 期红：二选一/非 control 插件/branch 形状/未知 type/条件语法/
    $item 作用域/loop 键位/max_iters/经典节点控制键/跨分支 id 重复。
    全部构造期抛、零 run 目录。"""
    _install_switch_plugins(plugins_root)
    _install_loop_plugins(plugins_root, items=["a"])

    def _expect(bad: list[dict], match: str) -> None:
        with pytest.raises(PipelinePlanError, match=match):
            psp.plan_pipeline(bad, plugins_root=plugins_root)

    # condition 与 plugin 同时给
    _expect(
        [{"id": "s", "type": "switch", "plugin": "duration-switch",
          "condition": "a.b > 1", "branch": {"x": [{"id": "n", "plugin": "str-sink"}]}}],
        "二选一",
    )
    # 两者都不给
    _expect([{"id": "s", "type": "switch", "branch": {"x": []}}], "二选一|缺 condition")
    # branch 缺失
    _expect([{"id": "s", "type": "switch", "condition": "a.b > 1"}], "branch 映射")
    # 未知节点类型
    _expect([{"id": "s", "type": "parallel", "branch": {}}], "未知节点类型")
    # 插件不是 control kind
    _expect(
        [{"id": "s", "type": "switch", "plugin": "str-sink", "branch": {"x": []}}],
        "须为 control",
    )
    # 条件语法不识别（裸路径无比较符）
    _expect(
        [{"id": "s", "type": "switch", "condition": "duration",
          "branch": {"x": [{"id": "n", "plugin": "str-sink"}]}}],
        "语法不识别",
    )
    # 条件字面值非 JSON（字符串未带引号）
    _expect(
        [{"id": "s", "type": "switch", "condition": "a.b == long",
          "branch": {"x": [{"id": "n", "plugin": "str-sink"}]}}],
        "字面值须为 JSON",
    )
    # $item 用在 body 之外
    _expect(
        [{"id": "s", "plugin": "str-refine", "inputs": {"item": {"$item": True}}}],
        r"\$item 注入只在",
    )
    # loop 带 plugin 键
    _expect(
        [{"id": "l", "type": "loop", "plugin": "str-refine",
          "loop_over": "s.items", "body": [{"id": "r", "plugin": "str-refine"}]}],
        "不接受 plugin",
    )
    # loop_over 解析不到更早节点
    _expect(
        [{"id": "l", "type": "loop", "loop_over": "ghost.items",
          "body": [{"id": "r", "plugin": "str-refine"}]}],
        "解析不到更早节点",
    )
    # loop_over 多输出端口未显式指名（switch 的分支节点在前占位省略——直接用双输出源）
    _expect(
        [{"id": "src2", "plugin": "seg-source"},
         {"id": "l", "type": "loop", "loop_over": "src2",
          "body": [{"id": "r", "plugin": "str-refine"}]}],
        "多个输出端口",
    )
    # max_iters 非法
    _expect(
        [{"id": "l", "type": "loop", "loop_over": "x.y", "max_iters": 0,
          "body": [{"id": "r", "plugin": "str-refine"}]}],
        "max_iters 须为",
    )
    # 经典节点携带控制流键（max_iters 无法推断归属 → 红并提示显式 type）
    _expect([{"id": "n", "plugin": "str-sink", "max_iters": 5}], "不接受控制流键")
    # switch 键与 loop 键并存无法推断类型
    _expect(
        [{"id": "n", "branch": {"x": []}, "loop_over": "a.b",
          "body": [{"id": "m", "plugin": "str-sink"}]}],
        "无法推断类型",
    )
    # 跨分支节点 id 重复（workspace 按 node_id 存取会互相覆盖）
    _expect(
        [{"id": "s", "type": "switch", "condition": "a.b > 1",
          "branch": {"x": [{"id": "dup", "plugin": "str-sink"}],
                     "y": [{"id": "dup", "plugin": "str-sink"}]}}],
        "id 重复",
    )


def test_plan_red_leaves_no_run_dir(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root)
    with pytest.raises(PipelinePlanError):
        psp.run_pipeline(
            [{"id": "s", "type": "switch", "condition": "a.b > 1"}],
            plugins_root=plugins_root, run_root=runs_root,
        )
    assert not runs_root.exists() or not any(runs_root.iterdir())  # 零 run 目录


# ── d) loop（硬要求 16：3 段 → 3 次 body → 合并正确）──────────────


def test_loop_three_items_refine_merge(plugins_root: Path, runs_root: Path) -> None:
    _install_loop_plugins(plugins_root, items=["alpha", "beta", "gamma"])
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {
            "id": "refine-all", "type": "loop", "loop_over": "src.items",
            "body": [{"id": "refine", "plugin": "str-refine",
                      "inputs": {"item": {"$item": True}}}],
        },
        {"id": "sink", "plugin": "str-sink"},
    ]
    phases: list[str] = []
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-3-0001",
        on_phase=phases.append,
    )
    assert result["status"] == "done"
    # body 每项一步 + loop 自身一步 + 下游 sink
    assert [s["step"] for s in result["steps"]] == [
        "src", "refine", "refine", "refine", "refine-all", "sink",
    ]
    assert all(s["status"] == "ok" for s in result["steps"])
    # 合并输出：顺序保持、逐项大写
    loop_out = json.loads(
        (runs_root / "lp-3-0001" / "workspace" / "refine-all" / "outputs.json").read_text("utf-8")
    )
    assert loop_out["inline"]["out"] == ["ALPHA", "BETA", "GAMMA"]
    # 下游 sink 消费合并列表；阶段三收集 loop 自身产物（terminal=loop）
    article = json.loads((runs_root / "lp-3-0001" / "output" / "article.json").read_text("utf-8"))
    assert article == "3:ALPHA,BETA,GAMMA"
    # 迭代相位 + 逐项日志留痕
    assert "refine-all 1/3" in phases and "refine-all 3/3" in phases
    logs = (runs_root / "lp-3-0001" / "log.jsonl").read_text("utf-8")
    assert "第 1/3 项完成" in logs and "第 3/3 项完成" in logs


# ── d2) loop bytes 产物合并（2026-11-22 复核发现 2 红转绿锚）──────────
#
# v1 缺陷判例：body 尾节点 bytes 产物落固定名 <port>.bin（覆盖式 put），
# _exec_loop 逐轮 get 回的 Path 全指向同一文件 → merged 每个元素都是末轮
# 内容（'A,B,C' 读回变 'C,C,C'），而 docstring 声称「进程内取数正确」。
# 修复=body 文件产物按迭代号落盘（<port>.iter<k>.bin），每个 Path 各指其轮。


_BYTES_REFINE_CODE = """
def run(inputs, config, ctx):
    return {"blob": inputs["item"].encode("utf-8")}
"""

_BYTES_SINK_CODE = """
def run(inputs, config, ctx):
    blobs = inputs["blob"]
    return {"article": ",".join(p.read_text("utf-8") for p in blobs)}
"""


def _install_bytes_loop_plugins(root: Path, items: list[str]) -> None:
    _write_plugin(
        root, "list-source",
        {
            "id": "list-source", "version": "0.1.0", "kind": "source",
            "inputs": {},
            "outputs": {"items": {"type": "string[]"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _STR_SOURCE_CODE.format(items=items),
    )
    _write_plugin(
        root, "bytes-refine",
        {
            "id": "bytes-refine", "version": "0.1.0", "kind": "process",
            "inputs": {"item": {"type": "string", "required": True}},
            "outputs": {"blob": {"type": "ext/blob"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _BYTES_REFINE_CODE,
    )
    _write_plugin(
        root, "bytes-sink",
        {
            "id": "bytes-sink", "version": "0.1.0", "kind": "sink",
            "inputs": {"blob": {"type": "ext/blob", "required": True}},
            "outputs": {"article": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _BYTES_SINK_CODE,
    )


def test_loop_bytes_merge_keeps_every_iteration(
    plugins_root: Path, runs_root: Path
) -> None:
    """body 逐迭代产 bytes → 合并列表每个 Path 必须各指其迭代内容。"""
    _install_bytes_loop_plugins(plugins_root, items=["A", "B", "C"])
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {
            "id": "bytes-all", "type": "loop", "loop_over": "src.items",
            "body": [{"id": "enc", "plugin": "bytes-refine",
                      "inputs": {"item": {"$item": True}}}],
        },
        {"id": "sink", "plugin": "bytes-sink"},
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-bytes-0001"
    )
    assert result["status"] == "done", result
    # 三个 Path 三份内容（v1 缺陷下这里读回 'C,C,C'）
    article = json.loads(
        (runs_root / "lp-bytes-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert article == "A,B,C"


def test_loop_empty_list_zero_iterations(plugins_root: Path, runs_root: Path) -> None:
    _install_loop_plugins(plugins_root, items=[])
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {"id": "refine-all", "type": "loop", "loop_over": "src.items",
         "body": [{"id": "refine", "plugin": "str-refine",
                   "inputs": {"item": {"$item": True}}}]},
        {"id": "sink", "plugin": "str-sink"},
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-0-0001"
    )
    assert result["status"] == "done"
    assert "0 项" in result["steps"][1]["detail"]
    article = json.loads((runs_root / "lp-0-0001" / "output" / "article.json").read_text("utf-8"))
    assert article == "0:"


# ── e) loop 上限（硬要求 17：超限可读报错）────────────────────────


def test_loop_over_max_iters_fails_readably(plugins_root: Path, runs_root: Path) -> None:
    _install_loop_plugins(plugins_root, items=["a", "b", "c", "d"])
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {"id": "refine-all", "type": "loop", "loop_over": "src.items", "max_iters": 3,
         "body": [{"id": "refine", "plugin": "str-refine",
                   "inputs": {"item": {"$item": True}}}]},
        {"id": "sink", "plugin": "str-sink"},
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-cap-0001"
    )
    assert result["status"] == "failed" and result["failed_at"] == "refine-all"
    detail = result["steps"][1]["detail"]
    assert "最大循环数" in detail and "4" in detail and "3" in detail  # 项数 4 > 上限 3
    assert [s["step"] for s in result["steps"]] == ["src", "refine-all"]  # body 零执行


def test_loop_default_max_iters_is_100(plugins_root: Path, runs_root: Path) -> None:
    _install_loop_plugins(plugins_root, items=[f"s{i}" for i in range(101)])
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {"id": "refine-all", "type": "loop", "loop_over": "src.items",
         "body": [{"id": "refine", "plugin": "str-refine",
                   "inputs": {"item": {"$item": True}}}]},
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-def-0001"
    )
    assert result["status"] == "failed" and result["failed_at"] == "refine-all"
    assert "101" in result["steps"][1]["detail"] and "100" in result["steps"][1]["detail"]


def test_loop_body_failure_stops_run(plugins_root: Path, runs_root: Path) -> None:
    _install_loop_plugins(plugins_root, items=["ok1", "boom", "ok3"], boom=True)
    nodes = [
        {"id": "src", "plugin": "list-source"},
        {"id": "refine-all", "type": "loop", "loop_over": "src.items",
         "body": [{"id": "refine", "plugin": "str-refine",
                   "inputs": {"item": {"$item": True}}}]},
        {"id": "sink", "plugin": "str-sink"},
    ]
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-boom-0001"
    )
    assert result["status"] == "failed"
    assert [s["step"] for s in result["steps"]] == ["src", "refine", "refine"]
    assert result["steps"][2]["status"] == "failed" and "该项炸了" in result["steps"][2]["detail"]
    assert not (runs_root / "lp-boom-0001" / "output").exists()  # 失败不收集


def test_loop_nested_control_flow(plugins_root: Path, runs_root: Path) -> None:
    """loop 套 loop（body 内嵌控制节点）：外层 2 组 → 各组内逐项大写 → 合并
    顺序正确。锁死 _dispatch_node 递归派发（嵌套控制节点不得走经典加载路径）。"""
    _write_plugin(
        plugins_root, "nest-source",
        {
            "id": "nest-source", "version": "0.1.0", "kind": "source",
            "inputs": {}, "outputs": {"groups": {"type": "string[]"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        'def run(inputs, config, ctx):\n    return {"groups": [["a", "b"], ["c"]]}\n',
    )
    _write_plugin(
        plugins_root, "passthru",
        {
            "id": "passthru", "version": "0.1.0", "kind": "process",
            "inputs": {"item": {"type": "string", "required": True}},
            "outputs": {"group": {"type": "string[]"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        'def run(inputs, config, ctx):\n    return {"group": inputs["item"]}\n',
    )
    _install_loop_plugins(plugins_root, items=[])  # 复用 str-refine / str-sink
    nodes = [
        {"id": "src", "plugin": "nest-source"},
        {"id": "outer", "type": "loop", "loop_over": "src.groups",
         "body": [
             {"id": "asgroup", "plugin": "passthru", "inputs": {"item": {"$item": True}}},
             {"id": "inner", "type": "loop", "loop_over": "asgroup.group",
              "body": [{"id": "up", "plugin": "str-refine",
                        "inputs": {"item": {"$item": True}}}]},
         ]},
        {"id": "sink", "plugin": "json-sink2"},
    ]
    _write_plugin(
        plugins_root, "json-sink2",
        {
            "id": "json-sink2", "version": "0.1.0", "kind": "sink",
            "inputs": {"out": {"type": "string", "required": True}},
            "outputs": {"article": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        'import json as _j\ndef run(inputs, config, ctx):\n'
        '    return {"article": _j.dumps(inputs["out"], ensure_ascii=False)}\n',
    )
    result = psp.run_pipeline(
        nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-nest-0001"
    )
    assert result["status"] == "done"
    # 执行序：外层第 1 组（asgroup → up ×2 → inner 合并）→ 第 2 组（up ×1）→ outer → sink
    assert [s["step"] for s in result["steps"]] == [
        "src", "asgroup", "up", "up", "inner", "asgroup", "up", "inner", "outer", "sink",
    ]
    article = json.loads(
        (runs_root / "lp-nest-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert article == '[["A", "B"], ["C"]]'


# ── f) 向后兼容 + meta/regenerate 面（硬要求 21）──────────────────

_ECHO_SRC_CODE = """
def run(inputs, config, ctx):
    return {"value": inputs.get("seed", "x")}
"""

_ECHO_SINK_CODE = """
def run(inputs, config, ctx):
    return {"article": f"echo:{inputs['value']}"}
"""


def _install_echo_chain(root: Path) -> None:
    _write_plugin(
        root, "echo-src",
        {
            "id": "echo-src", "version": "0.1.0", "kind": "source",
            "inputs": {"seed": {"type": "string", "required": False, "default": "x"}},
            "outputs": {"value": {"type": "string"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _ECHO_SRC_CODE,
    )
    _write_plugin(
        root, "echo-sink",
        {
            "id": "echo-sink", "version": "0.1.0", "kind": "sink",
            "inputs": {"value": {"type": "string", "required": True}},
            "outputs": {"article": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _ECHO_SINK_CODE,
    )


def test_backward_compat_linear_chain_unchanged(plugins_root: Path, runs_root: Path) -> None:
    """既有线性流水线零改动正常跑（硬要求 21）：经典三节点形状/契约/收集全不变。"""
    _install_echo_chain(plugins_root)
    result = psp.run_pipeline(
        [{"id": "src", "plugin": "echo-src", "inputs": {"seed": "hi"}},
         {"id": "out", "plugin": "echo-sink"}],
        plugins_root=plugins_root, run_root=runs_root, run_id="lin-0001",
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["src", "out"]
    article = json.loads((runs_root / "lin-0001" / "output" / "article.json").read_text("utf-8"))
    assert article == "echo:hi"
    meta = json.loads((runs_root / "lin-0001" / "meta.json").read_text("utf-8"))
    # node_specs 经典节点保持既有四键形状（零回归；config=合并缺省、inputs=接线）
    assert meta["node_specs"] == [
        {"id": "src", "plugin": "echo-src", "config": {}, "inputs": {"seed": "hi"}},
        {"id": "out", "plugin": "echo-sink", "config": {}, "inputs": {}},
    ]


def test_meta_specs_control_nodes_and_regenerate_reject(
    plugins_root: Path, runs_root: Path
) -> None:
    _install_loop_plugins(plugins_root, items=["a", "b"])
    _install_switch_plugins(plugins_root)
    loop_nodes = [
        {"id": "src", "plugin": "list-source"},
        {"id": "refine-all", "type": "loop", "loop_over": "src.items",
         "body": [{"id": "refine", "plugin": "str-refine",
                   "inputs": {"item": {"$item": True}}}]},
    ]
    result = psp.run_pipeline(
        loop_nodes, plugins_root=plugins_root, run_root=runs_root, run_id="lp-meta-0001"
    )
    assert result["status"] == "done"
    meta = json.loads((runs_root / "lp-meta-0001" / "meta.json").read_text("utf-8"))
    assert meta["nodes"] == ["src", "refine-all"]  # 顶层清单；body 节点在 steps 可见
    loop_spec = meta["node_specs"][1]
    assert loop_spec["type"] == "loop"
    assert loop_spec["loop_over"] == "src.items"
    assert loop_spec["max_iters"] == 100
    assert loop_spec["body"][0]["plugin"] == "str-refine"
    assert meta["plugins"][1] == "builtin/loop"  # 合成契约标记

    # 重生成面：控制流节点拒（可读），precheck 本身可通过
    assert precheck_regenerate("lp-meta-0001", "refine-all", run_root=runs_root).is_dir()
    with pytest.raises(RegenerateError, match="控制流节点"):
        regenerate_node("lp-meta-0001", "refine-all", run_root=runs_root)
    # 分支/body 内的嵌套节点不在顶层清单——precheck 即拒
    with pytest.raises(RegenerateError, match="节点不在该运行内"):
        precheck_regenerate("lp-meta-0001", "refine", run_root=runs_root)


def test_meta_specs_switch_node_spec(plugins_root: Path, runs_root: Path) -> None:
    _install_switch_plugins(plugins_root)
    result = psp.run_pipeline(
        _switch_pipeline(), plugins_root=plugins_root, run_root=runs_root, run_id="sw-meta-0001"
    )
    assert result["status"] == "done"
    meta = json.loads((runs_root / "sw-meta-0001" / "meta.json").read_text("utf-8"))
    spec = meta["node_specs"][1]
    assert spec["type"] == "switch" and spec["plugin"] == "duration-switch"
    assert set(spec["branch"]) == {"long", "short"}
    assert meta["plugins"][1] == "duration-switch"


# ── g) manifest：kind=control 必须声明 route（core 契约红绿）──────


def _control_manifest(outputs: dict) -> dict:
    return {
        "id": "some-switch", "version": "0.1.0", "kind": "control",
        "inputs": {}, "outputs": outputs,
        "runtime": {"type": "python", "entry": "plugin.py:run"},
    }


def test_control_manifest_requires_route_output() -> None:
    plugin, errors = parse_manifest(_control_manifest({}))
    assert plugin is None
    assert any("route" in e for e in errors)


def test_control_manifest_route_must_be_string_type() -> None:
    plugin, errors = parse_manifest(_control_manifest({"route": {"type": "number"}}))
    assert plugin is None
    assert any("string" in e for e in errors)


def test_control_manifest_green_with_route() -> None:
    plugin, errors = parse_manifest(_control_manifest({"route": {"type": "string"}}))
    assert plugin is not None and errors == []
    assert plugin.kind == "control"
