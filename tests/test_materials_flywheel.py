"""N-2 素材闭环**飞轮一整圈**端到端测试（集成工程师轮，2026-11-21）。

与 test_materials_feedback.py 的分工：那边是四条验收硬要求的**分段**契约
测试（每条路径独立成用例）；本文件是**一整圈**——验收标准「跑通一整圈，
不是三个端点各自 200」的落测。单一用例按真实顺序走完：

  造文章（有 publish_log）→ 反哺三路径（full/excerpt/distill 各一次）
  → 三批素材全部进素材库（列表可查）→ 创作工坊引用其中两条（full 素材
  + distill spark）→ build_prompt 含其内容 → 断言整条链路。

harness 与 test_materials_feedback.py 同款：真实回环服务器 + http.client，
MUSESPIRAL_HOME/tmp_path 全隔离，AI 进程内 monkeypatch（服务线程同进程即时生效）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import compose_service
from musespiral.services import materials_service as ms

# 文章正文（独特标记句——飞轮断言锚：全链各环节都认这两句）
BODY_MARK = "飞轮闭环的唯一验证是整圈跑通，而非三个端点各自 200。"
POINT_MARK = "回程素材必须能再次进入创作输入面"

ARTICLE = """---
title: 飞轮验收文
tags: [飞轮, 验收]
---

## 起点

发布只是飞轮的一半。

## 命题

""" + BODY_MARK + """

## 回程

""" + POINT_MARK + """——论点也应回流成灵感。
"""

# 剥 frontmatter 后正文行（1-based）：1=空行 2=## 起点 3=空行 4=发布只是… 5=空行
# 6=## 命题 7=空行 8=BODY_MARK 9=空行 10=## 回程 11=空行 12=POINT_MARK 行 13=…
EXCERPT_LINE = 8  # BODY_MARK 所在行


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """本地无 key 档案（material_distill 未配路由 → 走 default）。"""
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return _isolate_home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path) -> studio.ThreadingHTTPServer:
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _post(server, path: str, body: dict):
    return _request(server, "POST", path, body)


def _materials(server, query: str = "") -> dict:
    status, data = _request(server, "GET", f"/api/materials{query}")
    assert status == 200, data
    return data


def test_materials_flywheel_full_circle(server, studio_root, ai_on, monkeypatch) -> None:
    """飞轮一整圈：文章(有 publish_log) → 三路径反哺 → 入库 → 工坊引用 → prompt 含其内容。

    每一跳的断言都**消费前一跳的产物 id/文本**（不是各自造数）——断的正是
    「圈」本身：任何一环断链（素材没入库 / 工坊查不到 / prompt 不带正文），
    本用例在那一环转红。
    """
    # ── ① 造一篇文章（有 publish_log）──────────────────────────────
    slug = "flywheel-e2e"
    (studio_root / f"{slug}.md").write_text(ARTICLE, encoding="utf-8")
    from musespiral.services.publish_log_service import append_manual, merge_logs, root_log_path

    append_manual(slug, "wechat", "飞轮验收首发", log_path=root_log_path(studio_root))
    rows = [r for r in merge_logs(studio_root) if r.get("article") == slug and r.get("action") == "publish"]
    assert rows, "前置不成立：publish_log 未记录该文章"

    # ── ② 反哺三路径（各一次）───────────────────────────────────────
    # 路径 A：full 整篇
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": slug, "mode": "full"})
    assert status == 200 and data["ok"] is True and data["dedup"] is False, data
    full = data["material"]
    assert full["provenance"]["chain"] == [{"from_id": slug, "op": "published_from"}]

    # 路径 B：excerpt 摘取（BODY_MARK 所在行）
    status, data = _post(server, "/api/materials/from-article", {
        "confirm": True, "slug": slug, "mode": "excerpt",
        "start_line": EXCERPT_LINE, "end_line": EXCERPT_LINE,
    })
    assert status == 200 and data["ok"] is True and data["dedup"] is False, data
    exc = data["material"]
    assert exc["payload"]["text"] == BODY_MARK  # 摘到的正是标记句

    # 路径 C：distill AI 提炼（mock 回两条论点，断言正文进过模板）
    def fake_chat(messages, *, task=None, **kwargs):
        assert task == "material_distill"
        assert BODY_MARK in messages[0]["content"]  # 文章正文进了 prompt 模板
        return {"text": f"- {POINT_MARK}\n- 回程第二论点", "cache_hit": False}

    monkeypatch.setattr(ms, "_chat_policed", fake_chat)
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": slug, "mode": "distill"})
    assert status == 200 and data["ok"] is True and len(data["materials"]) == 2, data
    distill_sparks = data["materials"]
    assert [s["payload"]["text"] for s in distill_sparks] == [POINT_MARK, "回程第二论点"]

    # ── ③ 三批素材全部进入素材库（列表可查）─────────────────────────
    ids = {full["id"], exc["id"], *[s["id"] for s in distill_sparks]}
    listed = {m["id"] for m in _materials(server)["items"]}
    assert ids <= listed, f"反哺素材未全部入素材库: 缺 {ids - listed}"
    # 来源族筛选面：article 虚拟源下恰这 4 条（full+excerpt+2 spark）
    by_source = _materials(server, "?source=article")
    assert by_source["total"] == 4
    # distill 产物在 spark 筛选面可见（回程进灵感面）
    spark_ids = {m["id"] for m in _materials(server, "?sub_type=spark")["items"]}
    assert {s["id"] for s in distill_sparks} <= spark_ids

    # ── ④ 创作工坊引用它（full 素材 + distill spark 两条直引）───────
    status, data = _post(server, "/api/compose/new", {"confirm": True, "slug": "flywheel", "title": "飞轮再创作"})
    assert status == 200 and data["ok"] is True, data
    for mid in (full["id"], distill_sparks[0]["id"]):
        status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "flywheel", "material_id": mid})
        assert status == 200 and data["ok"] is True and data["path"] == f"material://{mid}", data

    session = compose_service._load_session("flywheel", studio_root)[0]
    assert [s.path for s in session.sources] == [f"material://{full['id']}", f"material://{distill_sparks[0]['id']}"]

    # ── ⑤ 生成 prompt 含其内容（回程真正流回创作输入面）─────────────
    prompt = compose_service.build_prompt(session)
    assert BODY_MARK in prompt  # full 素材正文（文章全文）进 prompt
    assert POINT_MARK in prompt  # distill spark 论点进 prompt
    assert "飞轮验收文" in prompt  # 文章 title 作素材标签
