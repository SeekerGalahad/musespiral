"""官方示例插件三件套（插件 v2 DSL 实战验证批：duration-switch / refine-segments / word-count）。

「DSL 存在」与「DSL 能用」是两件事——本批是验证批（每个插件最小实现但真实可用）：

  a) 发现面：三插件住包内 plugins_builtin/，root=None 生产路径可见、零 skipped、
     三道门（parse/目录名/入口存在性）全过；
  b) duration-switch（kind=control·switch 实战）：单测红绿（>300→long / ≤300→short /
     空转写→short / threshold_s config 覆盖 / 坏段·坏配置可读失败）+ 引擎级真实链
     subtitle→switch→分支（short=article-writer 快速成稿 / long=key-segment 完整
     切片）：steps 只含选中分支、未走分支零 workspace、route 落 workspace；
  c) refine-segments（loop 实战 body 工人）：单测（AI 未配置→原段透传 refined=false /
     AI 路径→text 替换+start/end/质量信号保留 / AIError→透传 / 空回复→透传 /
     坏段→PluginError）+ 引擎级 loop DSL（3 段字幕 → 3 次子流水线 → 合并 3 段
     顺序保持 → article-writer 消费合并产物）；AI 路径引擎级断言 3 次调用、
     每段一次（task=plugin_refine_segment 一插件一键）；
  d) word-count（exec runtime 实战）：裸协议（echo JSON → count.sh → stdout JSON，
     含坏 JSON 退出码 2）、run_exec_plugin 直调（权限门+shlex+子进程+JSON 解析
     全链）、引擎级单节点（字面值直供 → 子进程 → 产物收集 output/*.json）。

全部在 MUSESPIRAL_HOME 沙箱跑（AI 未配置=降级路径，确定性；count.sh 子进程真跑
——macOS bash 3.2 兼容由该脚本自证；零网络）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from musespiral.core.plugins.manifest import parse_manifest
from musespiral.infra.ai import gateway as ai_gateway
from musespiral.infra.ai import profiles as ai_profiles
from musespiral.infra.ai.profiles import AIError, TASKS
from musespiral.infra import plugins_loader as loader
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services import plugins_service as svc
from musespiral.services.pipeline import exec_runtime as er
from musespiral.core.plugins.context import PluginError

BUILTIN_DIR = loader.builtin_plugins_dir()
WC_DIR = BUILTIN_DIR / "word-count"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUSESPIRAL_HOME 沙箱：用户插件区不存在、AI 未配置（降级路径确定性）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "pipeline_runs"


# ── 工具 ───────────────────────────────────────────────────────────


def _manifest_of(dirname: str) -> dict[str, Any]:
    return json.loads((BUILTIN_DIR / dirname / "manifest.json").read_text(encoding="utf-8"))


def _load_module(dirname: str) -> ModuleType:
    """按路径加载插件模块（与引擎 _load_python_entry 同款——目录名带连字符
    不可直接 import）。"""
    d = BUILTIN_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        f"test_examples_{dirname.replace('-', '_')}", d / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCtx:
    """直调 run() 的最小 ctx（log/progress 收集；refine-segments/duration-switch
    不触碰 artifacts_dir/tmpdir/cancelled）。"""

    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        self.logs.append(f"progress {done}/{total}")


def _write_srt(path: Path, cues: list[tuple[float, float, str]]) -> Path:
    """ cues=[(start_s, end_s, 文本)] → 合法 .srt（序号行+时间行+文本+空行）。"""

    def _ts(t: float) -> str:
        ms = round(t * 1000)
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    blocks = [
        f"{i}\n{_ts(a)} --> {_ts(b)}\n{text}\n"
        for i, (a, b, text) in enumerate(cues, start=1)
    ]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _write_srt_with_media(
    tmp_path: Path, cues: list[tuple[float, float, int | str]], name: str = "clip.srt"
) -> tuple[Path, Path]:
    """字幕 + 配套哑媒体文件（本批引擎级链要走到抽帧断言，故给真实媒体路径；
    2026-11-23 扫尾后 subtitle 无媒体输入也恒产出 media_path=None 不再判失败，
    但 None 时 frame-grab 守卫会拒——需要抽帧产物的用例仍须哑媒体；哑文件
    只需存在，subtitle 只查 is_file）。"""
    srt = _write_srt(tmp_path / name, cues)
    media = tmp_path / (name.rsplit(".", 1)[0] + ".mp4")
    media.write_bytes(b"\x00\x00")
    return srt, media


def _inline(runs_root: Path, run_id: str, node_id: str) -> dict[str, Any]:
    """读节点 workspace 内联产物（outputs.json 的 inline 列）。"""
    p = runs_root / run_id / "workspace" / node_id / "outputs.json"
    return json.loads(p.read_text(encoding="utf-8"))["inline"]


# ── a) 发现面 ──────────────────────────────────────────────────────

EXAMPLE_IDS = {"duration-switch", "refine-segments", "word-count"}


def test_examples_discovered_in_production_path() -> None:
    """root=None 生产路径三插件可见（内置区免安装）、零 skipped（三道门全过）。"""
    out = svc.list_plugins()
    ids = {p["manifest"]["id"] for p in out["plugins"]}
    assert EXAMPLE_IDS <= ids
    assert out["skipped"] == []
    for item in out["plugins"]:
        if item["manifest"]["id"] in EXAMPLE_IDS:
            assert "plugins_builtin" in item["dir"]


def test_examples_manifest_contracts() -> None:
    """三插件 manifest 关键契约位（control 规则/uses_ai/exec 权限与入口）。"""
    ds, errs = parse_manifest(_manifest_of("duration-switch"))
    assert errs == [] and ds is not None
    assert ds.kind == "control"
    assert ds.outputs["route"].type == "string"  # kind=control ⇒ route 输出（安装期红线的绿面）

    rs, errs = parse_manifest(_manifest_of("refine-segments"))
    assert errs == [] and rs is not None
    assert rs.kind == "process" and rs.uses_ai is True
    assert rs.inputs["segment"].type == "ext/segment"  # body 单项工人：$item 注入端口

    wc, errs = parse_manifest(_manifest_of("word-count"))
    assert errs == [] and wc is not None
    assert wc.kind == "sink" and wc.runtime.type == "exec"
    assert wc.runtime.entry == "./count.sh"
    assert "exec:subprocess" in wc.permissions  # exec 二道门的声明半边
    assert "plugin_refine_segment" in TASKS  # 一插件一键：任务键已入白名单
    for dirname in EXAMPLE_IDS:
        assert loader.entry_file_ok(_manifest_of(dirname), BUILTIN_DIR / dirname), dirname


# ── b) duration-switch（switch 实战）───────────────────────────────


@pytest.fixture
def ds_mod() -> ModuleType:
    return _load_module("duration-switch")


def _seg(start: float, end: float, text: str) -> dict[str, Any]:
    return {"start": start, "end": end, "text": text}


def test_duration_switch_routes_long_short(ds_mod: ModuleType) -> None:
    ctx = _FakeCtx()
    assert ds_mod.run({"transcript": [_seg(0, 100, "a"), _seg(100, 400.5, "b")]}, {}, ctx) == {
        "route": "long"
    }  # 总时长=max(end)=400.5 > 300
    assert ds_mod.run({"transcript": [_seg(0, 120, "a")]}, {}, ctx) == {"route": "short"}
    assert ds_mod.run({"transcript": [_seg(0, 300, "a")]}, {}, ctx) == {"route": "short"}  # ≤ 阈值
    assert ds_mod.run({"transcript": []}, {}, ctx) == {"route": "short"}  # 空转写=0s → 快速路径
    assert any("route=short" in m for m in ctx.logs)


def test_duration_switch_threshold_config(ds_mod: ModuleType) -> None:
    ctx = _FakeCtx()
    out = ds_mod.run({"transcript": [_seg(0, 450, "a")]}, {"threshold_s": 500}, ctx)
    assert out == {"route": "short"}  # 450 ≤ 500
    out = ds_mod.run({"transcript": [_seg(0, 450, "a")]}, {"threshold_s": 60}, ctx)
    assert out == {"route": "long"}


def test_duration_switch_red_paths(ds_mod: ModuleType) -> None:
    ctx = _FakeCtx()
    with pytest.raises(PluginError, match="段落列表"):
        ds_mod.run({"transcript": "不是列表"}, {}, ctx)
    with pytest.raises(PluginError, match="end"):
        ds_mod.run({"transcript": [{"start": 0}]}, {}, ctx)  # 缺 end
    with pytest.raises(PluginError, match="threshold_s"):
        ds_mod.run({"transcript": [_seg(0, 10, "a")]}, {"threshold_s": "十分钟"}, ctx)


def _switch_nodes(srt: Path, media: Path) -> list[dict[str, Any]]:
    """实战 DSL：字幕解析 → 时长路由 →（long=切片 / short=直接成稿）。
    正典形不写 type（branch 键推断 switch）。"""
    return [
        {"id": "sub", "plugin": "subtitle",
         "inputs": {"subtitle_path": str(srt), "media_path": str(media)}},
        {
            "id": "route",
            "plugin": "duration-switch",
            "branch": {
                "long": [{"id": "segs", "plugin": "key-segment"}],
                "short": [{"id": "write", "plugin": "article-writer"}],
            },
        },
    ]


def test_switch_engine_short_branch(runs_root: Path, tmp_path: Path) -> None:
    """硬性验证 8（<300s）：路由 short → 只执行快速成稿分支。"""
    srt, media = _write_srt_with_media(tmp_path, [
        (1.0, 60.0, "大家好，欢迎来到本频道，今天聊聊短视频的快速成稿。"),
        (60.0, 120.0, "这是第二段，短视频直接全文成稿就够了。"),
    ])
    result = psp.run_pipeline(
        _switch_nodes(srt, media), run_root=runs_root, run_id="ex-sw-short-0001"
    )
    assert result["status"] == "done"
    # steps 只含选中分支：未选的 long 分支（key-segment）零 step 零 workspace
    assert [s["step"] for s in result["steps"]] == ["sub", "route", "write"]
    assert all(s["status"] == "ok" for s in result["steps"])
    assert "route=short" in result["steps"][1]["detail"]
    assert (runs_root / "ex-sw-short-0001" / "workspace" / "segs").exists() is False
    # route 落 workspace（下游可消费路由值）+ 分支产物被收集（terminal=分支尾）
    assert _inline(runs_root, "ex-sw-short-0001", "route") == {"route": "short"}
    article = json.loads(
        (runs_root / "ex-sw-short-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert isinstance(article, dict) and article  # 成稿非空（模板降级路径）


def test_switch_engine_long_branch(runs_root: Path, tmp_path: Path) -> None:
    """硬性验证 8（>300s）：路由 long → 走完整切片分支。"""
    srt, media = _write_srt_with_media(tmp_path, [
        (1.0, 120.0, "第一个话题：这一段讲的是长视频的完整处理流水线。"),
        (120.0, 250.0, "第二个话题：切片之后还要抽帧和成稿，内容比较长。"),
        (250.0, 400.0, "第三个话题：最后总结一下完整链路的关键点。"),
    ])
    result = psp.run_pipeline(
        _switch_nodes(srt, media), run_root=runs_root, run_id="ex-sw-long-0001"
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["sub", "route", "segs"]
    assert "route=long" in result["steps"][1]["detail"]
    ranges = _inline(runs_root, "ex-sw-long-0001", "segs")["ranges"]
    assert isinstance(ranges, list) and ranges  # 规则切片产出非空（AI 未配置沙箱）


def test_switch_engine_threshold_node_config(runs_root: Path, tmp_path: Path) -> None:
    """节点 config 覆盖 manifest 缺省：120s 素材配 threshold=60 → long 分支。"""
    srt, media = _write_srt_with_media(tmp_path, [
        (1.0, 60.0, "第一段。"),
        (60.0, 120.0, "第二段。"),
    ])
    nodes = _switch_nodes(srt, media)
    nodes[1]["config"] = {"threshold_s": 60}
    result = psp.run_pipeline(nodes, run_root=runs_root, run_id="ex-sw-cfg-0001")
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["sub", "route", "segs"]


# ── c) refine-segments（loop 实战）─────────────────────────────────


@pytest.fixture
def rs_mod() -> ModuleType:
    return _load_module("refine-segments")


def test_refine_segment_passthrough_without_ai(rs_mod: ModuleType) -> None:
    """AI 未配置（沙箱缺省）→ 原段透传：text 不动、refined=false、质量信号保留。"""
    seg = {"start": 1.0, "end": 5.0, "text": "嗯 然后然后我们来讲这个重点",
           "avg_logprob": 1.0, "no_speech_prob": 0.0}
    ctx = _FakeCtx()
    out = rs_mod.run({"segment": seg}, {}, ctx)
    body = out["refined_segments"]
    assert body["text"] == seg["text"] and body["refined"] is False
    assert body["start"] == 1.0 and body["end"] == 5.0
    assert body["avg_logprob"] == 1.0  # 上游质量信号透传
    assert any("AI 未配置" in m for m in ctx.logs)


def test_refine_segment_ai_path(rs_mod: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """AI 路径：chat_policed 返回精炼文本 → text 替换、refined=true、其余字段保留。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None, **kw: True)
    calls: list[dict[str, Any]] = []

    def fake_chat_policed(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return {"text": "我们来讲这个重点", "cache_hit": False}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)
    seg = {"start": 2.0, "end": 6.5, "text": "嗯 然后然后我们来讲这个重点", "avg_logprob": 0.9}
    out = rs_mod.run({"segment": seg}, {}, _FakeCtx())["refined_segments"]
    assert out["text"] == "我们来讲这个重点" and out["refined"] is True
    assert out["start"] == 2.0 and out["end"] == 6.5 and out["avg_logprob"] == 0.9
    assert calls[0]["task"] == "plugin_refine_segment"  # 一插件一键（治理层任务键）
    assert calls[0]["messages"][0]["role"] == "system"


def test_refine_segment_ai_failure_degrades(
    rs_mod: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 调用失败（治理层 AIError 族）/空回复 → 原段透传，不炸流水线。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None, **kw: True)
    ctx = _FakeCtx()
    monkeypatch.setattr(
        ai_gateway, "chat_policed",
        lambda *a, **kw: (_ for _ in ()).throw(AIError("预算超限")),
    )
    seg = {"start": 0.0, "end": 3.0, "text": "原始内容"}
    out = rs_mod.run({"segment": seg}, {}, ctx)["refined_segments"]
    assert out["text"] == "原始内容" and out["refined"] is False
    assert any("AI 精炼不可用" in m for m in ctx.logs)

    monkeypatch.setattr(ai_gateway, "chat_policed", lambda *a, **kw: {"text": "  "})
    out = rs_mod.run({"segment": seg}, {}, _FakeCtx())["refined_segments"]
    assert out["refined"] is False  # 空回复同样透传


def test_refine_segment_red_paths(rs_mod: ModuleType) -> None:
    ctx = _FakeCtx()
    with pytest.raises(PluginError, match="单段 dict"):
        rs_mod.run({"segment": "不是 dict"}, {}, ctx)
    with pytest.raises(PluginError, match="start"):
        rs_mod.run({"segment": {"start": 5.0, "end": 2.0, "text": "x"}}, {}, ctx)


def _loop_nodes(srt: Path, media: Path) -> list[dict[str, Any]]:
    """实战 DSL：字幕 → loop（逐段精炼）→ 成稿消费合并产物。"""
    return [
        {"id": "sub", "plugin": "subtitle",
         "inputs": {"subtitle_path": str(srt), "media_path": str(media)}},
        {
            "id": "loop1", "type": "loop", "loop_over": "sub.transcript",
            "body": [{
                "id": "refine", "plugin": "refine-segments",
                "inputs": {"segment": {"$item": True}},
            }],
            "max_iters": 10,
        },
        {"id": "write", "plugin": "article-writer"},
    ]


_THREE_CUES = [
    (1.0, 40.0, "第一段：先讲背景。"),
    (40.0, 80.0, "第二段：然后讲方法。"),
    (80.0, 120.0, "第三段：最后讲结论。"),
]


def test_loop_engine_three_iterations_passthrough(runs_root: Path, tmp_path: Path) -> None:
    """硬性验证 9：3 段输入 → 3 次子流水线 → 合并 3 段（顺序保持）→ 下游消费。"""
    srt, media = _write_srt_with_media(tmp_path, _THREE_CUES, name="three.srt")
    result = psp.run_pipeline(
        _loop_nodes(srt, media), run_root=runs_root, run_id="ex-loop-0001"
    )
    assert result["status"] == "done"
    # sub + refine×3（每迭代一次 body）+ loop1 收尾 + write
    assert [s["step"] for s in result["steps"]] == [
        "sub", "refine", "refine", "refine", "loop1", "write",
    ]
    assert all(s["status"] == "ok" for s in result["steps"])
    assert "3 项 × body 1 节点" in result["steps"][4]["detail"]

    merged = _inline(runs_root, "ex-loop-0001", "loop1")["refined_segments"]
    assert isinstance(merged, list) and len(merged) == 3
    assert [m["start"] for m in merged] == [1.0, 40.0, 80.0]  # 顺序保持
    assert [m["text"] for m in merged] == [c[2] for c in _THREE_CUES]  # AI 未配置=原段透传
    assert all(m["refined"] is False for m in merged)

    logs = (runs_root / "ex-loop-0001" / "log.jsonl").read_text("utf-8")
    for k in (1, 2, 3):
        assert f"第 {k}/3 项完成" in logs

    # 下游消费合并产物：成稿（article-writer 降级模板）已收集
    article = json.loads(
        (runs_root / "ex-loop-0001" / "output" / "article.json").read_text("utf-8")
    )
    assert isinstance(article, dict) and article


def test_loop_engine_ai_path_refines_each_item(
    runs_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 路径引擎级：逐段各调一次（3 段=3 次调用），合并产物是精炼后文本。"""
    # 只给 refine 任务开 AI（article-writer 保持降级模板路径——本测试聚焦 loop 逐段调用）
    monkeypatch.setattr(
        ai_profiles, "ai_configured",
        lambda task=None, **kw: task == "plugin_refine_segment",
    )
    calls: list[str] = []

    def fake_chat_policed(messages, **kwargs):
        user = messages[-1]["content"]
        calls.append(kwargs["task"])
        return {"text": f"【精】{user}", "cache_hit": False}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)
    srt, media = _write_srt_with_media(tmp_path, _THREE_CUES, name="three.srt")
    result = psp.run_pipeline(
        _loop_nodes(srt, media), run_root=runs_root, run_id="ex-loop-ai-0001"
    )
    assert result["status"] == "done"
    assert calls == ["plugin_refine_segment"] * 3  # 逐段处理（不是全篇一次）

    merged = _inline(runs_root, "ex-loop-ai-0001", "loop1")["refined_segments"]
    assert [m["text"] for m in merged] == [f"【精】{c[2]}" for c in _THREE_CUES]
    assert all(m["refined"] is True for m in merged)


# ── d) word-count（exec runtime 实战）──────────────────────────────


def test_word_count_raw_protocol() -> None:
    """硬性验证 10：echo JSON → count.sh → stdout JSON 正确返回（bash 3.2 自证）。"""
    payload = json.dumps(
        {"text": "大家好，欢迎来到本频道\nThis is the second line"}, ensure_ascii=False
    )
    proc = subprocess.run(
        [str(WC_DIR / "count.sh")], input=payload, capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"chars": 35, "words": 6, "lines": 2}


def test_word_count_raw_bad_json_rejected() -> None:
    """stdin 非 JSON → 退出码 2 + stderr 可读（引擎收敛为可读节点失败的原料）。"""
    proc = subprocess.run(
        [str(WC_DIR / "count.sh")], input="not json", capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "非合法 JSON" in proc.stderr


def test_word_count_exec_runtime_direct() -> None:
    """run_exec_plugin 直调：权限门+entry 解析+子进程+JSON 解析全链。"""
    plugin, errs = parse_manifest(_manifest_of("word-count"))
    assert plugin is not None and errs == []
    out = er.run_exec_plugin(plugin, WC_DIR, {"text": "你好世界 hello world"})
    assert out == {"chars": 16, "words": 3, "lines": 1}
    out = er.run_exec_plugin(plugin, WC_DIR, {"text": ""})
    assert out == {"chars": 0, "words": 0, "lines": 0}  # 空文本=全零（合法结果非失败）


def test_word_count_missing_text_rejected() -> None:
    """inputs 缺 text / text 非字符串 → 子进程退出码 2 → ExecRuntimeError 可读。"""
    plugin, _ = parse_manifest(_manifest_of("word-count"))
    assert plugin is not None
    with pytest.raises(er.ExecRuntimeError, match="退出码 2"):
        er.run_exec_plugin(plugin, WC_DIR, {"nope": 1})


def test_word_count_via_engine(runs_root: Path) -> None:
    """引擎级单节点：字面值直供 → exec 子进程 → 产物收集 output/*.json。"""
    result = psp.run_pipeline(
        [{"id": "wc", "plugin": "word-count", "inputs": {"text": "一行\n两行\n三行"}}],
        run_root=runs_root, run_id="ex-wc-0001",
    )
    assert result["status"] == "done"
    assert result["steps"][0]["status"] == "ok"
    outdir = runs_root / "ex-wc-0001" / "output"
    assert json.loads((outdir / "chars.json").read_text("utf-8")) == 8
    assert json.loads((outdir / "words.json").read_text("utf-8")) == 3  # 空白分词（\n 亦空白）
    assert json.loads((outdir / "lines.json").read_text("utf-8")) == 3
