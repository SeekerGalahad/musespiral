"""AI-3 节点重生成测试（2026-11-21，正典=《mdpub-AI产物裁决机制设计-2026-11-21》
§2.3 裁决三件套·重生成；契约 §3.19 修订 4 / §3.2-5 regenerate 形）。

三层（沿 test_plugin_endpoints / test_plugins_builtin_pl6 惯例）：
  a) 引擎级（psp.regenerate_node，零 HTTP）：aw 重生成 → **前序节点（src/tr/ks/fg）
     产物逐字节不变**、旧 aw 产物归档 outputs.v1.json（含 *.md 随版归档）、
     新产物 status 重置 draft（节点 auto_adopt=true 也不豁免）；二次重生成
     v2 递增且 v1 保留；ks 编辑版（ranges.edited.json）让位归档；插件失败
     → 归档副本回滚现场零变更；ai_verdicts 读面反映 draft。
  b) 插件级（真实内置 key-segment/article-writer，gateway/ai_configured 假件）：
     config.feedback 拼进 AI prompt 尾部（「用户改进要求：…」——抓请求体断言，
     不是只看 200）；AI 不可用降级路径 feedback 不消费但可读日志留痕、不炸。
  c) HTTP 级（真实 Studio 服务器）：POST /api/pipelines/runs/<rid>/regenerate
     全链（提交 job → 轮询终态 → 产物/读面断言）+ 非法参数可读拒绝面
     （step 枚举外/缺 step/feedback 非字符串/confirm 闸门/404 穿越/run running/
     老 run 缺清单/无产物节点）。

流水线沙箱=六节点假链 src→tr→ks→fg→aw→sink（确定性零 AI；fx-ks/fx-aw 的
config_schema.auto_adopt default=true——证「重生成强制 draft」压过 auto_adopt；
feedback 经 config 注入后被假插件嵌进产物 gen 字段，重跑与改进要求双向可断言）。
"""
from __future__ import annotations

import http.client
import importlib.util
import json
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from musespiral import studio
from musespiral.infra import plugins_loader
from musespiral.infra.ai import gateway as ai_gateway
from musespiral.infra.ai import profiles as ai_profiles
from musespiral.services import pipeline_service_plugins as psp
from musespiral.studio import plugin_actions  # ai_verdicts 读面（AI-1 批同款）

# ── 沙箱假链（六节点；manifest 形与 key-segment 官方件同构）──────────────

_PLUGINS: tuple[tuple[str, dict[str, Any], str], ...] = (
    (
        "fx-src",
        {
            "id": "fx-src", "version": "1.0.0", "kind": "source",
            "name": {"zh": "假源"}, "description": {"zh": "写一个假媒体文件"},
            "inputs": {"path": {"type": "string", "required": False, "default": ""}},
            "outputs": {"media": {"type": "file/path"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
        },
        "def run(inputs, config, ctx):\n"
        "    p = ctx.artifacts_dir / 'media.bin'\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_bytes(b'fake-media')\n"
        "    return {'media': str(p)}\n",
    ),
    (
        "fx-tr",
        {
            "id": "fx-tr", "version": "1.0.0", "kind": "process",
            "name": {"zh": "假转写"}, "description": {"zh": "确定性三段转写"},
            "inputs": {"media": {"type": "file/path", "required": True}},
            "outputs": {"transcript": {"type": "segments"}, "duration_s": {"type": "number"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
        },
        "def run(inputs, config, ctx):\n"
        "    segs = [{'start': 0.0, 'end': 2.0, 'text': '第一段 介绍'},\n"
        "            {'start': 3.0, 'end': 5.5, 'text': '第二段 重点讲存储引擎'},\n"
        "            {'start': 6.0, 'end': 8.0, 'text': '第三段 总结'}]\n"
        "    ctx.log('fx-tr done')\n"
        "    return {'transcript': segs, 'duration_s': 8.0}\n",
    ),
    (
        "fx-ks",
        {
            "id": "fx-ks", "version": "1.0.0", "kind": "process",
            "name": {"zh": "假切片"}, "description": {"zh": "前两段成 ranges；gen 记录 feedback"},
            "inputs": {"transcript": {"type": "segments", "required": True}},
            "outputs": {"ranges": {"type": "ranges"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
            "config_schema": {"auto_adopt": {"type": "boolean", "default": True}},
        },
        "def run(inputs, config, ctx):\n"
        "    segs = inputs.get('transcript') or []\n"
        "    fb = config.get('feedback')\n"
        "    if fb is not None and not isinstance(fb, str):\n"
        "        from musespiral.core.plugins.context import PluginError\n"
        "        raise PluginError('config.feedback 须为字符串: %r' % fb)\n"
        "    fb = fb.strip() if isinstance(fb, str) else ''\n"
        "    ranges = [{'start': float(s['start']), 'end': float(s['end']),\n"
        "               'label': ('fb:' + fb)[:24] if fb else 'orig',\n"
        "               'gen': fb or 'orig',\n"
        "               'status': 'adopted' if config.get('auto_adopt') else 'draft'}\n"
        "              for s in segs[:2]]\n"
        "    ctx.log('fx-ks %d ranges' % len(ranges))\n"
        "    return {'ranges': ranges}\n",
    ),
    (
        "fx-fg",
        {
            "id": "fx-fg", "version": "1.0.0", "kind": "process",
            "name": {"zh": "假抽帧"}, "description": {"zh": "吃 ranges 产 images"},
            "inputs": {
                "media": {"type": "file/path", "required": True},
                "ranges": {"type": "ranges", "required": True},
            },
            "outputs": {"images": {"type": "images"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
        },
        "def run(inputs, config, ctx):\n"
        "    rngs = inputs.get('ranges') or []\n"
        "    imgs = [{'path': 'frame-%d.png' % i, 'ts': float(r['start']) + 0.5}\n"
        "            for i, r in enumerate(rngs)]\n"
        "    ctx.log('fx-fg %d images' % len(imgs))\n"
        "    return {'images': imgs}\n",
    ),
    (
        "fx-aw",
        {
            "id": "fx-aw", "version": "1.0.0", "kind": "process",
            "name": {"zh": "假成稿"}, "description": {"zh": "写 art.md；gen 记录 feedback；feedback=boom 演练失败"},
            "inputs": {
                "transcript": {"type": "segments", "required": True},
                "images": {"type": "images", "required": False, "default": []},
                "ranges": {"type": "ranges", "required": False, "default": []},
                "media_path": {"type": "file/path", "required": False},
                "duration_s": {"type": "number", "required": False, "default": 0},
            },
            "outputs": {"article": {"type": "article"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
            "config_schema": {"auto_adopt": {"type": "boolean", "default": True}},
        },
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def run(inputs, config, ctx):\n"
        "    segs = inputs.get('transcript') or []\n"
        "    fb = config.get('feedback')\n"
        "    if fb is not None and not isinstance(fb, str):\n"
        "        from musespiral.core.plugins.context import PluginError\n"
        "        raise PluginError('config.feedback 须为字符串: %r' % fb)\n"
        "    fb = fb.strip() if isinstance(fb, str) else ''\n"
        "    if fb == 'boom':\n"
        "        raise RuntimeError('fx-aw 重生成失败演练')\n"
        "    marker = ('fb:' + fb) if fb else 'orig'\n"
        "    d = Path(ctx.artifacts_dir)\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    md = d / 'art.md'\n"
        "    md.write_text('article %s %d segs' % (marker, len(segs)), encoding='utf-8')\n"
        "    ctx.log('fx-aw done ' + marker)\n"
        "    return {'article': {'path': str(md), 'paragraphs': len(segs),\n"
        "                         'gen': marker,\n"
        "                         'status': 'adopted' if config.get('auto_adopt') else 'draft'}}\n",
    ),
    (
        "fx-sink",
        {
            "id": "fx-sink", "version": "1.0.0", "kind": "sink",
            "name": {"zh": "假汇"}, "description": {"zh": "吃 article 写 note"},
            "inputs": {"article": {"type": "article", "required": True}},
            "outputs": {"note": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
            "requires": [], "permissions": [], "uses_ai": False,
        },
        "def run(inputs, config, ctx):\n"
        "    art = inputs.get('article') or {}\n"
        "    d = ctx.artifacts_dir\n"
        "    d.mkdir(parents=True, exist_ok=True)\n"
        "    n = d / 'note.md'\n"
        "    n.write_text(str(art.get('gen', '')), encoding='utf-8')\n"
        "    ctx.log('fx-sink done')\n"
        "    return {'note': str(n)}\n",
    ),
)

CHAIN: list[dict[str, Any]] = [
    {"id": "src", "plugin": "fx-src"},
    {"id": "tr", "plugin": "fx-tr"},
    {"id": "ks", "plugin": "fx-ks"},
    {"id": "fg", "plugin": "fx-fg"},
    {"id": "aw", "plugin": "fx-aw"},
    {"id": "sink", "plugin": "fx-sink"},
]


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """MUSESPIRAL_HOME 沙箱 + 假链六插件（内置区指空——只看沙箱用户区）。"""
    home = tmp_path / "musespiral-home"
    plugins = home / "plugins"
    for pid, manifest, code in _PLUGINS:
        d = plugins / pid
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / "plugin.py").write_text(code, encoding="utf-8")
    monkeypatch.setattr(
        plugins_loader, "builtin_plugins_dir", lambda: tmp_path / "no-builtin-plugins"
    )
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _run_chain(home: Path, run_root: Path | None = None) -> dict[str, Any]:
    out = psp.run_pipeline(CHAIN, run_root=run_root)
    assert out["status"] == "done", out
    return out


def _outputs_doc(home: Path, run_id: str, node: str) -> dict[str, Any]:
    return json.loads(
        (home / "pipeline_runs" / run_id / "workspace" / node / "outputs.json").read_text(
            encoding="utf-8"
        )
    )


def _node_files(home: Path, run_id: str, node: str) -> dict[str, bytes]:
    d = home / "pipeline_runs" / run_id / "workspace" / node
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file()}


# ── a) 引擎级 ────────────────────────────────────────────────────


def test_regenerate_aw_upstream_untouched_archive_and_draft(sandbox: Path) -> None:
    """金路径（任务书硬要求）：重跑 aw → 前序（src/tr/ks/fg）产物逐字节不变、
    旧 aw 产物另存 outputs.v1.json（含 art.md 随版归档）、新产物 status 重置
    draft（fx-aw auto_adopt=true 也不豁免）、log 留审计行、meta 不动。"""
    home = sandbox
    out = _run_chain(home)
    run_id = out["run_id"]
    before = {n: _node_files(home, run_id, n) for n in ("src", "tr", "ks", "fg", "sink")}
    old_aw = _node_files(home, run_id, "aw")
    old_doc = _outputs_doc(home, run_id, "aw")
    assert old_doc["inline"]["article"]["status"] == "adopted"  # auto_adopt=true 初始态
    assert old_doc["inline"]["article"]["gen"] == "orig"

    result = psp.regenerate_node(run_id, "aw", feedback="更简洁")

    assert result["ok"] is True and result["version"] == 1 and result["status"] == "draft"
    assert result["node"] == "aw"
    # 前序节点（src/tr/ks/fg）产物逐字节不变（含 outputs.json 与节点目录全部
    # 文件）。sink 是后序——AI-3 复核 fix-2 起会被级联重放消费新稿，故不再要求
    # 逐字节不变（其新态由下方 cascaded 断言锁）。
    for n in ("src", "tr", "ks", "fg"):
        assert _node_files(home, run_id, n) == before[n], f"前序节点 {n} 产物被动过"
    # fix-2：后序 sink 被级联重放（消费新 draft 稿）
    assert any(c["node"] == "sink" and c["status"] == "ok" for c in result["cascaded"])
    assert "fb:更简洁" in (home / "pipeline_runs" / run_id / "workspace" / "sink"
                           / "note.md").read_text(encoding="utf-8")
    # 旧版归档：outputs.v1.json = 重生成前的 outputs.json 原文；art.md 随版归档
    # （命名 stem.v{N}.ext——与 outputs.v{N}.json 同构）
    aw_dir = home / "pipeline_runs" / run_id / "workspace" / "aw"
    assert (aw_dir / "outputs.v1.json").read_bytes() == old_aw["outputs.json"]
    assert (aw_dir / "art.v1.md").read_bytes() == old_aw["art.md"]
    # 新产物：真重跑了（gen 吃到 feedback）、status 重置 draft
    new_doc = _outputs_doc(home, run_id, "aw")
    assert new_doc["inline"]["article"]["gen"] == "fb:更简洁"
    assert new_doc["inline"]["article"]["status"] == "draft"
    assert "fb:更简洁" in (aw_dir / "art.md").read_text(encoding="utf-8")
    # 审计行（与 adopt/discard 同款 log.jsonl）
    log = (home / "pipeline_runs" / run_id / "log.jsonl").read_text(encoding="utf-8")
    assert "重生成" in log and "v1" in log
    # meta.json 不动（run 级状态仍是 done）
    meta = json.loads(
        (home / "pipeline_runs" / run_id / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["status"] == "done"


def test_regenerate_version_increments_and_keeps_history(sandbox: Path) -> None:
    """二次重生成 → v2 递增、v1 保留（「保留旧版供对比」的版本面）。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    first = psp.regenerate_node(run_id, "aw", feedback="一版")
    second = psp.regenerate_node(run_id, "aw", feedback="二版")
    assert first["version"] == 1 and second["version"] == 2
    aw_dir = home / "pipeline_runs" / run_id / "workspace" / "aw"
    v1 = json.loads((aw_dir / "outputs.v1.json").read_text(encoding="utf-8"))
    v2 = json.loads((aw_dir / "outputs.v2.json").read_text(encoding="utf-8"))
    assert v1["inline"]["article"]["gen"] == "orig"
    assert v2["inline"]["article"]["gen"] == "fb:一版"
    assert _outputs_doc(home, run_id, "aw")["inline"]["article"]["gen"] == "fb:二版"


def test_regenerate_ks_edited_file_yields_to_new_outputs(sandbox: Path) -> None:
    """ks 有 E-4 编辑版（ranges.edited.json，读面「编辑版优先」）→ 重生成把它
    随版归档挪开，新 outputs.json 成为生效文档；ai_verdicts 读面反映新 draft。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    ks_dir = home / "pipeline_runs" / run_id / "workspace" / "ks"
    (ks_dir / "ranges.edited.json").write_text(
        json.dumps([{"start": 0.0, "end": 2.0, "label": "手改", "status": "adopted"}]),
        encoding="utf-8",
    )
    run_dir = home / "pipeline_runs" / run_id
    assert plugin_actions._ai_verdicts(run_dir)["ks"]["ranges"] == "adopted"  # 编辑版生效

    psp.regenerate_node(run_id, "ks")

    assert not (ks_dir / "ranges.edited.json").exists()  # 挪开（归档不删）
    edited_v1 = json.loads((ks_dir / "ranges.edited.v1.json").read_text(encoding="utf-8"))
    assert edited_v1[0]["label"] == "手改"
    new_ranges = _outputs_doc(home, run_id, "ks")["inline"]["ranges"]
    assert all(r["status"] == "draft" for r in new_ranges)  # auto_adopt=true 仍强制 draft
    assert plugin_actions._ai_verdicts(run_dir)["ks"]["ranges"] == "draft"


def test_regenerate_plugin_failure_rolls_back_archive(sandbox: Path) -> None:
    """插件执行失败（feedback=boom 演练）→ 归档副本全部回滚、outputs.json 与
    md 原样、meta 不动——失败路径零副作用。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    before = _node_files(home, run_id, "aw")
    with pytest.raises(RuntimeError, match="失败演练"):
        psp.regenerate_node(run_id, "aw", feedback="boom")
    assert _node_files(home, run_id, "aw") == before  # 现场零变更
    aw_dir = home / "pipeline_runs" / run_id / "workspace" / "aw"
    assert not (aw_dir / "outputs.v1.json").exists()
    assert not (aw_dir / "art.v1.md").exists()


def test_precheck_rejections_readable(sandbox: Path) -> None:
    """precheck 校验面：run 不存在/节点不在册/无旧产物/run 仍在跑——全部
    RegenerateError 可读。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    with pytest.raises(psp.RegenerateError, match="运行不存在"):
        psp.precheck_regenerate("no-such-run", "aw")
    with pytest.raises(psp.RegenerateError, match="不在该运行内"):
        psp.precheck_regenerate(run_id, "ghost")
    # run 仍在跑（meta running）→ 拒
    meta_path = home / "pipeline_runs" / run_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "running"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(psp.RegenerateError, match="仍在进行中"):
        psp.precheck_regenerate(run_id, "aw")
    # 节点在册但无旧产物（未跑成/早停）→ 拒
    meta["status"] = "done"
    meta["nodes"] = [*meta["nodes"], "ghost"]
    meta["plugins"] = [*meta["plugins"], "fx-src"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(psp.RegenerateError, match="无旧产物"):
        psp.precheck_regenerate(run_id, "ghost")


def test_precheck_old_run_without_node_list(sandbox: Path) -> None:
    """老 run（meta 无 nodes/plugins 键——AI-1 前手造 run 形态）→ 可读拒绝。"""
    home = sandbox
    run_dir = home / "pipeline_runs" / "pl-old-meta"
    ks = run_dir / "workspace" / "aw"
    ks.mkdir(parents=True)
    (ks / "outputs.json").write_text(json.dumps({"inline": {"article": {}}, "refs": {}}),
                                     encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    with pytest.raises(psp.RegenerateError, match="缺节点/插件清单"):
        psp.precheck_regenerate("pl-old-meta", "aw")


# ── b) 插件级（真实内置 key-segment / article-writer；feedback → prompt）──

BUILTIN_DIR = plugins_loader.builtin_plugins_dir()


def _load_builtin(dirname: str) -> ModuleType:
    d = Path(BUILTIN_DIR) / dirname
    spec = importlib.util.spec_from_file_location(
        f"test_regen_{dirname.replace('-', '_')}", d / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCtx:
    """最小 ctx（test_plugins_builtin_pl6._FakeCtx 同款；upstream 缺省空面）。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        pass

    def tmpdir(self) -> Path:
        return self._root

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def artifacts_dir(self) -> Path:
        d = self._root / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def run_id(self) -> str:
        return ""

    @property
    def upstream(self) -> dict[str, str]:
        return {}


_SEGS = [{"start": 0.0, "end": 2.0, "text": "第一段 介绍"}]


@pytest.fixture(autouse=True)
def _ai_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: False)


def test_key_segment_feedback_reaches_llm_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """抓请求体断言（不是只看 200）：config.feedback → user 消息尾部
    「用户改进要求：…」；不带 feedback 时该串不出现。"""
    ks = _load_builtin("key-segment")
    captured: dict[str, Any] = {}

    def fake(messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
        captured["messages"] = messages
        return {"text": json.dumps([{"start": 0.0, "end": 2.0, "label": "段",
                                     "confidence": 0.9, "text_excerpt": "第一段"}])}

    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    out = ks.run({"transcript": [dict(s) for s in _SEGS]},
                 {"feedback": "更聚焦存储引擎"}, _FakeCtx(tmp_path))
    user = captured["messages"][1]["content"]
    assert user.endswith("\n\n用户改进要求：更聚焦存储引擎")
    assert out["ranges"][0]["status"] == "draft"

    ks.run({"transcript": [dict(s) for s in _SEGS]}, {}, _FakeCtx(tmp_path))
    assert "用户改进要求" not in captured["messages"][1]["content"]


def test_key_segment_feedback_rule_path_logs_not_consumed(
    tmp_path: Path,
) -> None:
    """AI 未配置 → 规则路径照跑（不炸）；feedback 未消费但有日志说明。"""
    ks = _load_builtin("key-segment")
    ctx = _FakeCtx(tmp_path)
    out = ks.run({"transcript": [dict(s) for s in _SEGS]},
                 {"feedback": "更聚焦存储引擎"}, ctx)
    assert out["ranges"] and out["ranges"][0]["status"] == "draft"
    assert any("改进要求只在 AI 路径生效" in m for m in ctx.logs)


def test_article_writer_feedback_reaches_llm_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    aw = _load_builtin("article-writer")
    captured: dict[str, Any] = {}

    def fake(messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
        captured["messages"] = messages
        return {"text": json.dumps([{"heading": "一", "text": "正文", "anchors": [0.0]}])}

    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    out = aw.run({"transcript": [dict(s) for s in _SEGS], "title": "测试视频"},
                 {"feedback": "更简洁"}, _FakeCtx(tmp_path))
    user = captured["messages"][1]["content"]
    assert user.endswith("\n\n用户改进要求：更简洁")
    assert out["article"]["status"] == "draft" and out["article_status"] == "draft"


def test_plugins_reject_non_string_feedback(tmp_path: Path) -> None:
    """feedback 非字符串 → PluginError 可读拒（宁失败不静默丢弃）。"""
    ks = _load_builtin("key-segment")
    aw = _load_builtin("article-writer")
    from musespiral.core.plugins.context import PluginError

    with pytest.raises(PluginError, match="feedback"):
        ks.run({"transcript": [dict(s) for s in _SEGS]}, {"feedback": 123}, _FakeCtx(tmp_path))
    with pytest.raises(PluginError, match="feedback"):
        aw.run({"transcript": [dict(s) for s in _SEGS]}, {"feedback": ["x"]}, _FakeCtx(tmp_path))


# ── c) HTTP 级（真实 Studio 服务器）──────────────────────────────


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=15)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, raw.decode("utf-8", "replace")


def _wait_job(server, job_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = _call(server, "GET", f"/api/jobs/{job_id}")
        if isinstance(job, dict) and job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} 未在 {timeout}s 内终态")


def test_regenerate_endpoint_full_path(server, sandbox: Path) -> None:
    """全链：POST regenerate（step aw + feedback）→ 200 {ok, job_id, run_id,
    step, node} → 轮询 job done → 旧版归档 v1、新产物 draft、ai_verdicts 读面
    从 adopted 翻回 draft。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    _, run_read = _call(server, "GET", f"/api/pipelines/runs/{run_id}")
    assert run_read["ai_verdicts"]["aw"]["article"] == "adopted"  # auto_adopt=true

    status, data = _call(server, "POST", f"/api/pipelines/runs/{run_id}/regenerate",
                         {"step": "aw", "feedback": "更简洁", "confirm": True})
    assert status == 200 and data["ok"] is True
    assert data["step"] == "aw" and data["node"] == "aw" and data["run_id"] == run_id
    assert data["job_id"]

    job = _wait_job(server, data["job_id"])
    assert job["status"] == "done", job

    doc = _outputs_doc(home, run_id, "aw")
    assert doc["inline"]["article"]["status"] == "draft"
    assert doc["inline"]["article"]["gen"] == "fb:更简洁"
    assert (home / "pipeline_runs" / run_id / "workspace" / "aw"
            / "outputs.v1.json").is_file()
    _, run_read = _call(server, "GET", f"/api/pipelines/runs/{run_id}")
    assert run_read["ai_verdicts"]["aw"]["article"] == "draft"  # 读面反映新状态


def test_regenerate_endpoint_ks_without_feedback(server, sandbox: Path) -> None:
    """step=ks、feedback 省略：重跑切片组，draft 重置同样生效（aw 之外的
    step 面不是只有拒绝路径）。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    status, data = _call(server, "POST", f"/api/pipelines/runs/{run_id}/regenerate",
                         {"step": "ks", "confirm": True})
    assert status == 200 and data["node"] == "ks"
    job = _wait_job(server, data["job_id"])
    assert job["status"] == "done", job
    doc = _outputs_doc(home, run_id, "ks")
    assert doc["inline"]["ranges"][0]["gen"] == "orig"  # 无 feedback → 原样重跑
    assert all(r["status"] == "draft" for r in doc["inline"]["ranges"])


def test_regenerate_endpoint_rejections(server, sandbox: Path) -> None:
    """非法参数可读拒绝面：step 枚举外/缺 step/feedback 非字符串 → 400；
    confirm 闸门 400；run 不存在与穿越 404；run running → 400；老 run 缺
    清单 → 400；链上无该产物节点 → 400；拒绝路径零副作用。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    base = f"/api/pipelines/runs/{run_id}/regenerate"
    cases = [
        {"step": "fg", "confirm": True},          # step 枚举外（非可重生成节点）
        {"confirm": True},                         # 缺 step
        {"step": "aw"},                            # 缺 confirm（全局闸门）
        {"step": "aw", "feedback": 123, "confirm": True},   # feedback 非字符串
        {"step": "ks", "feedback": ["x"], "confirm": True},  # feedback 非字符串（数组）
    ]
    for body in cases:
        code, err = _call(server, "POST", base, body)
        assert code == 400, (body, code, err)
        assert err["ok"] is False and err["error"]
    # run 不存在 / 穿越形态（run_id 白名单回验 → 404 fail-closed）
    assert _call(server, "POST", "/api/pipelines/runs/no-such/regenerate",
                 {"step": "aw", "confirm": True})[0] == 404
    assert _call(server, "POST", "/api/pipelines/runs/../regenerate",
                 {"step": "aw", "confirm": True})[0] == 404
    # run 仍在跑 → precheck 400（提交期零 job）
    meta_path = home / "pipeline_runs" / run_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["status"] = "running"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    code, err = _call(server, "POST", base, {"step": "aw", "confirm": True})
    assert code == 400 and "仍在进行中" in err["error"]
    meta["status"] = "done"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    # 老 run（meta 无 nodes/plugins）→ 400 可读
    old_dir = home / "pipeline_runs" / "pl-old-http"
    aw = old_dir / "workspace" / "aw"
    aw.mkdir(parents=True)
    (aw / "outputs.json").write_text(
        json.dumps({"inline": {"article": {"status": "draft"}}, "refs": {}}),
        encoding="utf-8")
    (old_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    code, err = _call(server, "POST", "/api/pipelines/runs/pl-old-http/regenerate",
                      {"step": "aw", "confirm": True})
    assert code == 400 and "缺节点/插件清单" in err["error"]
    # 链上无该产物节点（假链无 article 产出节点时 step=aw）→ 400
    short = psp.run_pipeline([CHAIN[0], CHAIN[1], CHAIN[2]], run_root=None)
    assert short["status"] == "done"
    code, err = _call(server, "POST",
                      f"/api/pipelines/runs/{short['run_id']}/regenerate",
                      {"step": "aw", "confirm": True})
    assert code == 400 and "article" in err["error"]
    # 拒绝路径零副作用（目标 run 的产物未被归档/改写）
    assert not (home / "pipeline_runs" / run_id / "workspace" / "aw"
                / "outputs.v1.json").exists()
    assert _outputs_doc(home, run_id, "aw")["inline"]["article"]["gen"] == "orig"
    # GET 该动态路径 → run_id 段不合白名单 → 404（与 adopt/discard 同语义）
    assert _call(server, "GET", base)[0] == 404


def test_refine_regenerate_endpoint_action(server, sandbox: Path) -> None:
    """素材域 regenerate（assist 端点 AI-3 形）：action=regenerate + feedback
    校验面——非字符串 400；缺素材 400 可读（服务层走通即够：完整版本链行为
    已由 test_refine_service 的 4 例覆盖，此处只锁端点形状）。"""
    code, err = _call(server, "POST", "/api/action/refine",
                      {"action": "regenerate", "id": "nope0000", "confirm": True})
    assert code == 400 and "素材不存在" in err["error"]
    code, err = _call(server, "POST", "/api/action/refine",
                      {"action": "regenerate", "id": "x", "feedback": 7, "confirm": True})
    assert code == 400 and "feedback" in err["error"]


# ── AI-3 复核 fix 回归锁（2026-11-21）──────────────────────────────
# 六条复核缺陷的机器锁：config/wiring 保留(#1)、下游级联回灌(#2)、md 归档
# 非指数(#3)、run 域版本链读面+diff(#4)、重生成旁路 AI 缓存(#5)、懒加载
# assets 进 window.prompt 否向域(#6——前端域，锁在 test_studio_ui)。


def test_regenerate_preserves_original_node_config(sandbox: Path) -> None:
    """fix-1：重生成用 meta.node_specs 的**实际运行 config**，不回落 manifest
    缺省。fx-aw 带非缺省 config（auto_adopt=False + 自定义键）运行后重生成——
    产物仍按原 config 组织（若丢 config 会回落 auto_adopt=true→status=adopted，
    且 gen 里的自定义键丢失）。"""
    home = sandbox
    chain = [
        {"id": "src", "plugin": "fx-src"},
        {"id": "tr", "plugin": "fx-tr"},
        {"id": "ks", "plugin": "fx-ks"},
        {"id": "fg", "plugin": "fx-fg"},
        {"id": "aw", "plugin": "fx-aw",
         "config": {"auto_adopt": False}},  # 非缺省（缺省 true）
        {"id": "sink", "plugin": "fx-sink"},
    ]
    out = psp.run_pipeline(chain)
    assert out["status"] == "done", out
    run_id = out["run_id"]
    # 首轮：auto_adopt=False → status=draft
    assert _outputs_doc(home, run_id, "aw")["inline"]["article"]["status"] == "draft"
    # meta 落了 node_specs（实际 config）
    meta = json.loads(
        (home / "pipeline_runs" / run_id / "meta.json").read_text(encoding="utf-8")
    )
    specs = {s["id"]: s for s in meta["node_specs"]}
    assert specs["aw"]["config"]["auto_adopt"] is False  # 非缺省值被落 meta

    psp.regenerate_node(run_id, "aw")
    # 重生成后仍 draft（config 保留——若丢 config 回落 auto_adopt=true 会 adopted）
    new_doc = _outputs_doc(home, run_id, "aw")
    assert new_doc["inline"]["article"]["status"] == "draft"


def test_regenerate_cascades_downstream_sink(sandbox: Path) -> None:
    """fix-2：aw 重生成后后序 sink 被级联重放，消费新稿（note.md 更新）；
    前序节点不动；返回 cascaded 含 sink ok。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    sink_before = _node_files(home, run_id, "sink")
    result = psp.regenerate_node(run_id, "aw", feedback="新要求")
    # 前序不动
    for n in ("src", "tr", "ks", "fg"):
        assert n not in [c["node"] for c in result["cascaded"]]
    # sink 级联且消费新稿
    casc = {c["node"]: c["status"] for c in result["cascaded"]}
    assert casc.get("sink") == "ok"
    sink_after = _node_files(home, run_id, "sink")
    assert sink_after != sink_before  # 真被重放了
    assert b"fb:\xe6\x96\xb0\xe8\xa6\x81\xe6\xb1\x82" in sink_after["note.md"]  # fb:新要求


def test_regenerate_md_archive_not_exponential(sandbox: Path) -> None:
    """fix-3：aw 连续重生成 3 次 → md 归档每版各一份（subtitle-srt.v1/v2/v3.md），
    不出现历史归档再归档（.v1.v2.md 等指数副本）；outputs.vN.json 正常递增。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    for i, fb in enumerate(("一", "二", "三"), start=1):
        r = psp.regenerate_node(run_id, "aw", feedback=fb)
        assert r["version"] == i
    aw_dir = home / "pipeline_runs" / run_id / "workspace" / "aw"
    mds = sorted(p.name for p in aw_dir.glob("*.md"))
    # 当前稿 + 三个历史版，恰好 4 份；无 .v1.v2 串味
    assert mds == ["art.md", "art.v1.md", "art.v2.md", "art.v3.md"], mds
    assert len([p for p in aw_dir.glob("*.md")]) == 4  # 2^3 指数已消除


def test_regenerate_bypasses_ai_cache(sandbox: Path) -> None:
    """fix-5：bypass_cache 作用域内 chat_policed 强制 miss——即使 prompt 相同
    也真调 LLM。用假 chat_fn 计调用数：同一 prompt + cache=True 两次，第一次
    写缓存第二次（无 bypass）命中、第三次（bypass 内）必须再调。"""
    from musespiral.infra.ai import gateway as gw

    calls = {"n": 0}

    def fake_chat(messages, **kw):
        calls["n"] += 1
        return f"reply-{calls['n']}"

    msgs = [{"role": "user", "content": "同一条 prompt"}]
    # 首次：miss → 调 + 写缓存
    gw.chat_policed(msgs, task="plugin_key_segment", cache=True, chat_fn=fake_chat)
    assert calls["n"] == 1
    # 第二次无 bypass：命中缓存 → 不再调
    gw.chat_policed(msgs, task="plugin_key_segment", cache=True, chat_fn=fake_chat)
    assert calls["n"] == 1  # 命中
    # 第三次在 bypass_cache 内：强制 miss → 真调
    with gw.bypass_cache():
        gw.chat_policed(msgs, task="plugin_key_segment", cache=True, chat_fn=fake_chat)
    assert calls["n"] == 2  # 旁路生效


def test_versions_endpoint_lists_and_diffs(sandbox: Path, tmp_path: Path) -> None:
    """fix-4：GET /api/pipelines/runs/<id>/versions?step=aw 列出归档版本；
    带 &a=&b= 返回 diff（html 在场）。旧版此前只写不读，现补读面。"""
    home = sandbox
    run_id = _run_chain(home)["run_id"]
    psp.regenerate_node(run_id, "aw", feedback="改一版")
    run_dir = home / "pipeline_runs" / run_id
    # 版本清单（outputs.json=当前 + outputs.v1.json）
    payload = plugin_actions._pipeline_run_versions_payload(
        None, run_id, {"step": ["aw"]}
    )
    names = [v["name"] for v in payload["versions"]]
    assert "outputs.json" in names and "outputs.v1.json" in names
    # 两版 diff
    diff = plugin_actions._pipeline_run_versions_payload(
        None, run_id, {"step": ["aw"], "a": ["outputs.v1.json"], "b": ["outputs.json"]}
    )
    assert "diff" in diff
    assert diff["diff"]["identical"] is False
    assert diff["diff"]["html"]
