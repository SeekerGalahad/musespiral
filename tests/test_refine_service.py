"""加工动作 M0 服务测试（第二十期 §五 refine 行，F57）。

覆盖：mock chat 预览（零落盘）/采纳落 derived（provenance.chain 血统 +
license/license_note 继承 + origin 指源）/拒绝零落盘（预览即全部中间态，
不采纳=库内零痕迹）/无正文面与缺素材的可读拒绝/AI 未配置引导。MUSESPIRAL_HOME
monkeypatch 到 tmp 隔离（materials.jsonl 与 ai.json 同隔离）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.materials import model
from musespiral.infra.ai import AIError
from musespiral.services import materials_service as ms
from musespiral.services import refine_service as svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(env: Path):
    """配置一个本地无 key 档案（refine 任务路由→默认即命中）。"""
    (env / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return env


@pytest.fixture
def kb_doc(env: Path) -> Path:
    root = env / "kb"
    root.mkdir()
    (env / "sources.json").write_text(
        json.dumps({"sources": [{
            "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
            "config": {"root": str(root), "sources": []},
            "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
        }]}),
        encoding="utf-8",
    )
    doc = root / "doc.md"
    doc.write_text("---\ntitle: 原文\n---\n\n这是一篇足够长的知识库文档正文。\n" * 3, encoding="utf-8")
    return doc


def _kb_material(env) -> dict:
    row = model.make_material(
        mtype="text", source_id="kb", uri="doc.md", title="原文",
        license="CC-BY",
        payload={"local_path": "doc.md", "meta": {"license_note": "CC-BY 4.0 声明原文"}},
        fetched_at="2026-11-16T09:00:00",
    )
    ms.add_material(row)
    return row


def _lines(env: Path) -> list[dict]:
    path = env / "materials" / "materials.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _mock_chat(monkeypatch, reply: str = "· 摘要草稿一\n· 摘要草稿二"):
    calls: list[dict] = []

    def fake_chat(messages, *, task=None, **kwargs):
        calls.append({"messages": messages, "task": task})
        return reply

    monkeypatch.setattr(svc, "_chat", fake_chat)
    return calls


# ── 预览：AI 起草，零落盘 ───────────────────────────────────────────


def test_preview_drafts_without_persisting(env, ai_on, kb_doc, monkeypatch) -> None:
    row = _kb_material(env)
    calls = _mock_chat(monkeypatch)
    out = svc.preview(row["id"])
    assert out == {"id": row["id"], "title": "原文", "op": "summarize", "text": "· 摘要草稿一\n· 摘要草稿二"}
    # 任务键走 refine；prompt 注入原文正文
    assert calls and calls[0]["task"] == "refine"
    assert "这是一篇足够长的知识库文档正文" in calls[0]["messages"][0]["content"]
    # 零落盘：素材库行数不变（仍只有源素材一行）
    assert len(_lines(env)) == 1


def test_preview_reads_payload_text_material(env, ai_on, monkeypatch) -> None:
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/a", title="粘贴素材",
        payload={"text": "手动粘贴的正文内容"},
        fetched_at="2026-11-16T09:30:00",
    )
    ms.add_material(row)
    _mock_chat(monkeypatch)
    out = svc.preview(row["id"])
    assert out["text"] == "· 摘要草稿一\n· 摘要草稿二"
    assert len(_lines(env)) == 1


def test_preview_missing_or_deleted_material_rejected(env, ai_on, monkeypatch) -> None:
    _mock_chat(monkeypatch)
    with pytest.raises(svc.RefineError, match="不存在"):
        svc.preview("nope00000000")


def test_preview_material_without_body_rejected(env, ai_on, monkeypatch) -> None:
    row = model.make_material(
        mtype="radar_signal", source_id="radarhub", uri="https://github.com/a/b", title="a/b",
        payload={"url": "https://github.com/a/b", "meta": {"grade": "S", "score": 90}},
        fetched_at="2026-11-16T09:40:00",
    )
    ms.add_material(row)
    _mock_chat(monkeypatch)
    with pytest.raises(svc.RefineError, match="无可摘正文"):
        svc.preview(row["id"])
    assert len(_lines(env)) == 1  # 拒绝零落盘


def test_preview_local_path_missing_file_rejected(env, ai_on, monkeypatch) -> None:
    row = model.make_material(
        mtype="text", source_id="kb", uri="gone.md", title="已移走的文档",
        payload={"local_path": "gone.md"}, fetched_at="2026-11-16T09:50:00",
    )
    ms.add_material(row)
    _mock_chat(monkeypatch)
    with pytest.raises(svc.RefineError, match="原文不存在"):
        svc.preview(row["id"])


def test_preview_ai_unconfigured_guides_user(env, kb_doc, monkeypatch) -> None:
    row = _kb_material(env)
    _mock_chat(monkeypatch)
    with pytest.raises(AIError, match="AI 未配置"):
        svc.preview(row["id"])


def test_preview_prompt_override_slot_injection(env, ai_on, kb_doc, monkeypatch) -> None:
    """prompts.d/refine.txt 存在即整体覆盖；{{material}} 插槽原位注入。"""
    row = _kb_material(env)
    (env / "prompts.d").mkdir()
    (env / "prompts.d" / "refine.txt").write_text(
        "自定义底座开头\n{{material}}\n自定义底座结尾\n", encoding="utf-8"
    )
    calls = _mock_chat(monkeypatch)
    svc.preview(row["id"])
    content = calls[0]["messages"][0]["content"]
    assert content.startswith("自定义底座开头\n【素材正文】")
    assert content.rstrip().endswith("自定义底座结尾")


def test_preview_input_truncated_to_budget(env, ai_on, kb_doc, monkeypatch) -> None:
    (kb_doc.parent / "big.md").write_text("字" * (svc.REFINE_INPUT_MAX_CHARS + 500), encoding="utf-8")
    row = model.make_material(
        mtype="text", source_id="kb", uri="big.md", title="超长文",
        payload={"local_path": "big.md"}, fetched_at="2026-11-16T10:00:00",
    )
    ms.add_material(row)
    calls = _mock_chat(monkeypatch)
    svc.preview(row["id"])
    body = calls[0]["messages"][0]["content"]
    assert "字" * 100 in body
    assert "字" * (svc.REFINE_INPUT_MAX_CHARS + 1) not in body  # 截断生效


# ── 采纳：人工「存为素材」才落 derived ──────────────────────────────


def test_adopt_writes_derived_lineage(env, kb_doc) -> None:
    src = _kb_material(env)
    out = svc.adopt(src["id"], "· 采纳的摘要草稿")
    assert out["ok"] is True
    rows = _lines(env)
    assert len(rows) == 2  # 源 + derived
    derived = ms.get_material(out["id"])
    assert derived is not None
    assert derived["type"] == "derived"
    assert derived["license"] == "CC-BY"  # license 继承源素材
    assert derived["payload"]["meta"]["license_note"] == "CC-BY 4.0 声明原文"  # 声明原文随血统
    assert derived["provenance"] == {
        "chain": [{"from_id": src["id"], "op": "summarize"}],
        "derived_from": [],
    }
    assert derived["origin"]["source_id"] == "kb"  # origin 信息源指回
    assert derived["origin"]["uri"].startswith(f"refine://{src['id']}/")
    assert derived["payload"]["text"] == "· 采纳的摘要草稿"
    # 血统引用状态：详情面引用未删
    detail = ms.get_detail(out["id"])
    assert detail["references"] == [{"from_id": src["id"], "op": "summarize", "deleted": False}]


def test_adopt_twice_distinct_rows(env, kb_doc) -> None:
    src = _kb_material(env)
    first = svc.adopt(src["id"], "第一版摘要")
    second = svc.adopt(src["id"], "第二版摘要")
    assert first["id"] != second["id"]  # uri 含采纳时戳，同源多次采纳不撞 id
    assert len(_lines(env)) == 3


def test_adopt_rejects_empty_text_and_missing_material(env, kb_doc) -> None:
    src = _kb_material(env)
    with pytest.raises(svc.RefineError, match="不能为空"):
        svc.adopt(src["id"], "   ")
    with pytest.raises(svc.RefineError, match="不存在"):
        svc.adopt("nope00000000", "草稿")
    assert len(_lines(env)) == 1  # 拒绝零落盘


def test_rejection_leaves_zero_trace(env, ai_on, kb_doc, monkeypatch) -> None:
    """拒绝即消失：预览后不采纳 → 素材库/jobs/任何文件零新增（§四-Q3）。"""
    row = _kb_material(env)
    _mock_chat(monkeypatch)
    before = sorted(p.name for p in env.rglob("*") if p.is_file())
    svc.preview(row["id"])
    after = sorted(p.name for p in env.rglob("*") if p.is_file())
    assert before == after  # 预览不产生任何新文件/新行
