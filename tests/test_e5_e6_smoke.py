"""E-5 / E-6 集成接线冒烟（本轮 GroupReport 验收物）。

两条金路径，走真实代码面（HTTP 回环服务器 + 服务层生成）：
  A) 小说会话 → 人物卡 → 生成注入（前情/人物卡进 prompt）→ 冲突提示；
  B) 版本历史（清单）→ 两版 diff（hunks + 红删绿增 HTML）→ 恢复（当前版先入池）。

不新增产品功能，只把 E-5/E-6 既有能力串起来验证接线未断。
"""
from __future__ import annotations

import contextlib
import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral.services import compose_service, materials_service, version_service

CHARACTER = {
    "name": "林远",
    "role": "研究员",
    "appearance": "瘦高 · 左眼蓝色",
    "speech_style": "短句 · 不用感叹号 · 偶尔自嘲",
    "key_events": ["第3章 初次出场", "第7章 借钱不还"],
}
NOVEL_AI = "陈默左眼异变；林远第2章递来一份文件（伏笔：内容未交代）。"
TWO_CHAPTERS = (
    "<<<block kind:heading>>># 第一章 异变\n\n"
    "<<<block kind:para>>>陈默睁开眼，左眼发烫。\n\n"
    "<<<block kind:heading>>># 第二章 借据\n\n"
    "<<<block kind:para>>>林远递来一份文件。\n"
)
CONFLICT_REPLY = "<<<block kind:para>>>林远：「这件事不能拖！」\n"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


def _post(server, path: str, body: dict):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(server, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


@contextlib.contextmanager
def _server(root: Path):
    from musespiral import studio
    from musespiral.studio.actions import _reset_runner

    _reset_runner()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        _reset_runner()


def test_smoke_e5_novel_session_card_injection_conflict(root: Path, monkeypatch) -> None:
    """小说会话→建卡挂卡→前情注入→生成注入人物卡→冲突提示，全链接线。"""
    # 1) 建小说会话并生成两章（末章为当前章，前 N-1 章供摘要）
    compose_service.new_session("smoke", "长篇", root=root, kind="novel")
    compose_service.generate("smoke", root=root, chat_fn=lambda m, **k: TWO_CHAPTERS)

    # 2) 建人物卡（素材库 sub_type=character）+ 挂到会话
    created = materials_service.add_character(**CHARACTER)
    mid = created["material"]["id"]
    compose_service.add_character("smoke", mid, root=root)
    detail = compose_service.session_detail("smoke", root=root)
    assert detail["kind"] == "novel"
    assert any(card["id"] == mid for card in detail["characters"])

    # 3) 刷新前情提要（chat(task=novel_summary) → 落 session.json）
    def _fake_summary(messages, *, task=None, **kw):
        assert task == "novel_summary"
        return "```\n" + NOVEL_AI + "\n```"

    out = compose_service.refresh_novel_summary(
        "smoke", root=root, chat_fn=_fake_summary
    )
    # chapters=总章数(2)，covered_chapters=已摘要的前 N-1 章(1)
    assert out["summary"] == NOVEL_AI
    assert out["chapters"] == 2 and out["covered_chapters"] == 1

    # 4) 生成一章：捕获 prompt，断言前情 + 人物卡都注入；同时命中冲突规则
    captured: dict = {}

    def _gen(messages, *, task=None, **kw):
        captured["prompt"] = messages[0]["content"]
        return CONFLICT_REPLY

    result = compose_service.generate("smoke", root=root, chat_fn=_gen)
    prompt = captured["prompt"]
    assert NOVEL_AI in prompt, "前情提要未注入生成 prompt"
    assert "林远" in prompt and "不用感叹号" in prompt, "人物卡未注入生成 prompt"
    # 冲突：说话风格含「不用感叹号」+ 对话行含「！」→ ⚠️ 提示，且不改写文本
    assert len(result["conflicts"]) >= 1
    conflict = next(c for c in result["conflicts"] if c["character"] == "林远")
    assert conflict["rule"] == "no_exclamation" or "感叹号" in conflict["message"]
    assert any("不能拖" in b["text"] for b in result["blocks"]), "生成文本被改写"


def test_smoke_e5_endpoint_roundtrip(root: Path, monkeypatch) -> None:
    """人物卡建卡 + 会话挂卡 + 前情手改走真实 POST 端点回环。"""
    compose_service.new_session("http", "长篇", root=root, kind="novel")
    with _server(root) as server:
        code, payload = _post(server, "/api/compose/novel-summary", {
            "confirm": True, "action": "character_create", **CHARACTER,
        })
        assert code == 200 and payload["ok"] is True
        mid = payload["material"]["id"]

        code, payload = _post(server, "/api/compose/novel-summary", {
            "confirm": True, "action": "character_add", "slug": "http", "id": mid,
        })
        assert code == 200 and payload["ok"] is True

        code, payload = _post(server, "/api/compose/novel-summary", {
            "confirm": True, "slug": "http", "summary": "人工前情（端点手改）。",
        })
        assert code == 200 and payload["edited"] is True
        assert payload["summary"] == "人工前情（端点手改）。"

        code, detail = _get(server, "/api/compose/session?slug=http")
        assert code == 200 and detail["novel_summary"] == "人工前情（端点手改）。"


def test_smoke_e6_version_history_diff_restore(root: Path, monkeypatch) -> None:
    """版本清单→两版 diff（hunks + HTML）→恢复（当前版先入池、恢复可逆）。"""
    from musespiral.infra.storage import filesystem as fs

    art = root / "story.md"
    v1 = "---\ntitle: 故事\n---\n\n原写法：这里比较平淡。\n"
    v2 = "---\ntitle: 故事\n---\n\n改写法：用了比喻，节奏更快。\n"
    art.write_text(v2, encoding="utf-8")  # 当前版 = v2
    # 造一版池内旧版 v1（确定性时间戳）
    (root / "story.md.bak.20261119-080000").write_text(v1, encoding="utf-8")

    with _server(root) as server:
        # 1) 版本清单（当前版 + 池内旧版）
        code, payload = _get(server, "/api/article/diff?path=story.md")
        assert code == 200 and payload["diff"] is None
        ids = [v["id"] for v in payload["versions"]]
        assert ids[0] == "current" and "20261119-080000" in ids

        # 2) 两版 diff（旧 → 新）：hunks + 红删绿增 HTML
        code, payload = _get(
            server,
            "/api/article/diff?path=story.md&v1=20261119-080000&v2=current",
        )
        assert code == 200 and payload["diff"] is not None
        diff = payload["diff"]
        assert diff["removed"] >= 1 and diff["added"] >= 1
        assert '<del class="dl-del">' in diff["html"]
        assert '<ins class="dl-add">' in diff["html"]
        assert any(r["kind"] == "del" for h in diff["hunks"] for r in h["rows"])
        assert any(r["kind"] == "ins" for h in diff["hunks"] for r in h["rows"])

        # 3) 恢复旧版：confirm 闸门 → 当前正文变回 v1，且恢复前的 v2 先入池
        code, payload = _post(server, "/api/article/restore", {
            "confirm": True, "article": "story.md", "version": "20261119-080000",
        })
        assert code == 200 and payload["ok"] is True
        assert art.read_text(encoding="utf-8") == v1, "恢复未覆盖当前正文"
        now = version_service.list_versions(art)
        assert now[0]["id"] == "current"  # 当前版恒首项
        # 恢复前的当前版 v2 已入池（同秒时间戳可能覆写，故按「池中可见 v2 内容」判）
        pool_texts = [b.path.read_text(encoding="utf-8") for b in fs.list_backups(art)]
        assert v2 in pool_texts, "恢复前当前版未入池（撤销不可撤销）"
