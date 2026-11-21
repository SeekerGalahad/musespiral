"""夹具插件端到端回归（PL-2 集成轮，2026-11-20）。

与 test_pipeline_plugins.py 的分工：那个文件用 tmp 动态生成假插件验证**引擎
语义逐条**（阈值/失败/取消/job），本文件用 `tests/fixtures/plugins/` 下的
**落盘夹具插件**验证**真实可跑链路**——五节点 fake-local-file → fake-transcribe
→ fake-key-segment → fake-article-writer → fake-material-sink 走完正典 §9.1
首链的形态（时间戳锚一路活到成稿），且夹具本身是「正典 §4.2 manifest 契约」
的可执行样本：plugins_service 扫出 0 skipped 即证明五份 manifest 合法。

夹具插件只 import core（`musespiral.core.plugins.context`）——插件作者不需要
知道引擎在哪一层（正典 §2.2「契约优先于实现」的落点）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.plugins.context import PluginCancelled, PluginContext
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services import plugins_service

FIXTURE_PLUGINS_ROOT = Path(__file__).resolve().parent / "fixtures" / "plugins"

FIXTURE_IDS = {
    "fake-local-file",
    "fake-transcribe",
    "fake-key-segment",
    "fake-article-writer",
    "fake-material-sink",
}

LECTURE_LINES = [
    "第一节 课程介绍",
    "这是重点：为什么要做数据管道",
    "普通内容：随便聊聊",
    "关键结论：契约优先于实现",
    "总结：三个概念一条纪律",
]


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def lecture(tmp_path: Path) -> Path:
    p = tmp_path / "lecture.txt"
    p.write_text("\n".join(LECTURE_LINES) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "pipeline_runs"


def _chain(lecture: Path) -> list[dict]:
    return [
        {"id": "src", "plugin": "fake-local-file", "inputs": {"path": str(lecture)}},
        {"id": "tr", "plugin": "fake-transcribe", "config": {"seg_seconds": 1.5}},
        {"id": "key", "plugin": "fake-key-segment"},
        {"id": "writer", "plugin": "fake-article-writer", "config": {"title": "数据管道第一课"}},
        {"id": "sink", "plugin": "fake-material-sink"},
    ]


# ── 夹具契约合法性（五份 manifest 是正典 §4.2 的可执行样本）────────


def test_fixture_plugins_all_contract_valid() -> None:
    """五个夹具插件全部通过三道门（parse_manifest/目录名/id 一致/入口存在）。"""
    out = plugins_service.list_plugins(FIXTURE_PLUGINS_ROOT)
    ids = {item["manifest"]["id"] for item in out["plugins"]}
    assert ids == FIXTURE_IDS
    assert out["skipped"] == []  # 零坏插件：夹具即契约样本


def test_fixture_kind_positions() -> None:
    """首链形态：source 打头、sink 收尾、中间 process（正典 §9.1）。"""
    out = plugins_service.list_plugins(FIXTURE_PLUGINS_ROOT)
    kinds = {i["manifest"]["id"]: i["manifest"]["kind"] for i in out["plugins"]}
    assert kinds["fake-local-file"] == "source"
    assert kinds["fake-material-sink"] == "sink"
    assert {kinds[i] for i in ("fake-transcribe", "fake-key-segment", "fake-article-writer")} == {"process"}


# ── 端到端：五节点全绿 + 产物形态 ──────────────────────────────────


def test_fixture_chain_end_to_end_done(lecture: Path, runs_root: Path) -> None:
    result = psp.run_pipeline(
        _chain(lecture),
        plugins_root=FIXTURE_PLUGINS_ROOT,
        run_root=runs_root,
        run_id="pl-fixtures-0001",
    )
    assert result["status"] == "done"
    assert [s["step"] for s in result["steps"]] == ["src", "tr", "key", "writer", "sink"]
    assert [s["status"] for s in result["steps"]] == ["ok"] * 5
    assert result["steps_done"] == 5

    run_dir = runs_root / "pl-fixtures-0001"
    # 时间戳锚（正典 §9.3 核心增量）：segments.start → ranges.start → 文章锚
    writer = json.loads((run_dir / "workspace" / "writer" / "outputs.json").read_text("utf-8"))
    article = writer["inline"]["article"]
    assert article.startswith("# 数据管道第一课")
    assert "[00:00:01](#t=1)" in article  # 第 2 行「这是重点」是第 1 段（start=1.5→floor 1）
    assert "**重点**" in article and "**关键**" in article and "**总结**" in article
    assert "普通内容" not in article  # 未命中关键词的段不进成稿

    # path 引用不复制（正典 §5.2）：src 节点只登记原路径
    src = json.loads((run_dir / "workspace" / "src" / "outputs.json").read_text("utf-8"))
    assert src["refs"]["media"]["kind"] == "path"
    assert src["refs"]["media"]["file"] == str(lecture)

    # 阶段三：末节点（sink）产物收集到 output/
    material = json.loads((run_dir / "output" / "material.json").read_text("utf-8"))
    assert material["preview"] is True and material["chars"] == len(article)

    # 阶段四：tmp 受管区已清；workspace 保留（保留策略归 ops gc）
    assert not (run_dir / "tmp").exists()
    assert (run_dir / "workspace" / "sink" / "outputs.json").is_file()

    # 运行日志留痕（ctx.log → log.jsonl，节点级）
    logs = (run_dir / "log.jsonl").read_text("utf-8").strip().splitlines()
    assert any('"node": "key"' in ln and "命中" in ln for ln in logs)
    assert json.loads((run_dir / "meta.json").read_text("utf-8"))["status"] == "done"


def test_fixture_chain_artifacts_reported(lecture: Path, runs_root: Path) -> None:
    """result.artifacts 是 run_dir 相对路径清单（job result「计数无正文」纪律）。"""
    result = psp.run_pipeline(
        _chain(lecture),
        plugins_root=FIXTURE_PLUGINS_ROOT,
        run_root=runs_root,
        run_id="pl-fixtures-0002",
    )
    arts = result["artifacts"]
    assert "workspace/src/outputs.json" in arts
    assert "output/material.json" in arts
    assert all((runs_root / "pl-fixtures-0002" / a).exists() for a in arts)


# ── 夹具失败路径：source 拒收 → 失败即停且原因可读 ────────────────


def test_fixture_source_rejects_missing_file(runs_root: Path) -> None:
    """fake-local-file 对不存在路径抛 PluginError → 该步 failed、后序不执行。"""
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "fake-local-file", "inputs": {"path": "/no/such/lecture.txt"}},
            {"id": "tr", "plugin": "fake-transcribe"},
        ],
        plugins_root=FIXTURE_PLUGINS_ROOT,
        run_root=runs_root,
        run_id="pl-fixtures-fail",
    )
    assert result["status"] == "failed" and result["failed_at"] == "src"
    assert "文件不存在" in result["steps"][0]["detail"]
    assert [s["step"] for s in result["steps"]] == ["src"]  # 失败即停


def test_fixture_suffix_whitelist(runs_root: Path, tmp_path: Path) -> None:
    """后缀白名单（正典 §9.1 local-file 实现要点）：非白名单后缀可读拒收。"""
    bad = tmp_path / "lecture.exe"
    bad.write_bytes(b"\x00")
    result = psp.run_pipeline(
        [{"id": "src", "plugin": "fake-local-file", "inputs": {"path": str(bad)}}],
        plugins_root=FIXTURE_PLUGINS_ROOT,
        run_root=runs_root,
        run_id="pl-fixtures-suffix",
    )
    assert result["status"] == "failed" and "后缀不在白名单" in result["steps"][0]["detail"]


# ── 夹具取消路径：协作式取消判 cancelled 而非 failed ─────────────


def test_fixture_chain_cancelled_not_failed(lecture: Path, runs_root: Path) -> None:
    """transcribe 段间自查点收到取消 → result.status=cancelled（正典 §4.4）。"""
    result = psp.run_pipeline(
        _chain(lecture),
        plugins_root=FIXTURE_PLUGINS_ROOT,
        run_root=runs_root,
        run_id="pl-fixtures-cxl",
        cancelled=lambda: True,  # 进入 transcribe 首段即置位
    )
    assert result["status"] == "cancelled"
    assert result["cancelled_at"] == "tr"
    assert "failed_at" not in result
    assert result["steps"][0]["status"] == "ok"  # 前序步保留
    assert result["steps"][1]["status"] == "cancelled"


# ── 架构门（PL-2 具名锚）：context.py 不得 import services ─────────


def test_pipeline_context_module_does_not_import_services() -> None:
    """`core/plugins/context.py` 是 L2 纯接口：零 services/infra/studio import。

    通用门 `test_architecture.py::test_rule_p1_core_plugins_pure_no_infra_or_io`
    已覆盖整个 core/plugins 树（含 I/O 库）；本条是 PL-2 的**具名锚**——把
    「引擎实现（services/pipeline_service_plugins）依赖接口，接口绝不反向
    依赖引擎」这条方向锁在具体文件上，反向 import（插件作者的 `except
    PluginCancelled` 就得反向 import services）发生时红在这一条。
    """
    from musespiral.core.plugins.context import PluginContext as Imported

    assert Imported is PluginContext  # 可导入即为第一道门

    # 复用架构门的 import 解析权威（tests/test_architecture.resolve_imports）：
    # 它把相对导入按 level 语义还原成绝对包名，`from ...services import x`
    # （level=3）→ musespiral.services.x 才能被认出。另写一份文本匹配会漏这类写法。
    from tests.test_architecture import MUSESPIRAL_ROOT, resolve_imports

    target = MUSESPIRAL_ROOT / "core" / "plugins" / "context.py"
    assert target.is_file(), f"PL-2 接口落点缺失: {target}"
    upward = (
        "musespiral.services",
        "musespiral.infra",
        "musespiral.studio",
    )
    violations = [
        f"context.py:{lineno} -> {mod}"
        for mod, lineno in resolve_imports(target)
        if mod.startswith(upward)
    ]
    assert not violations, "context.py 上探违反 L2 纯接口（PL-2 架构门）:\n" + "\n".join(violations)


def test_plugin_exception_family_defined_in_core() -> None:
    """异常族必须在 core（正典 §2.2）：插件只 import core 即可 except。"""
    assert issubclass(PluginCancelled, Exception)
    assert PluginCancelled.__module__ == "musespiral.core.plugins.context"
