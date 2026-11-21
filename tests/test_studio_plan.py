"""GET /api/plan 契约测试（P4 排期周历；契约 §3.2-2 + 接线期双形态）。

覆盖：
  - items 形态（契约 §3.2-2 预定义）：?range=30|90 → {ok, items:[…]}，三态
    口径 planned（frontmatter publish_at）/delivered（draft·uploaded）/
    published（publish 含 manual），窗口=|date-today|<days 对称；枚举外 400；
  - text 形态（接线期前端既有消费面，ui.js showPlan 无参读 d.text）：
    无参/?week=YYYY-Www → {ok, week, text}（render_week 文字周历）；非法
    周序 400；
  - plan_items 服务面单元：窗口边界（±(days-1)）、date 升序、日志行
    article 绝对路径归一 root 相对、无日期行/坏行宽容跳过；
  - 方法语义：POST /api/plan → 405。
真实回环服务器 + tmp_path 与 MUSESPIRAL_HOME 全隔离。
"""
from __future__ import annotations

import http.client
import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.services import plan_service


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
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


def _article(root: Path, name: str, *, title: str = "标题", publish_at: str | None = None) -> Path:
    fm = ["---", f"title: {title}", "cover: cover.png"]
    if publish_at is not None:
        fm.append(f"publish_at: {publish_at}")
    fm.append("---\n")
    path = root / name
    path.write_text("\n".join(fm) + "\n正文。\n", encoding="utf-8")
    return path


def _log(root: Path, rows: list[dict]) -> None:
    path = root / "publish_log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


TODAY = date(2026, 11, 16)


# ── items 形态（契约 §3.2-2）──────────────────────────────


def test_plan_range_items_three_states(server, studio_root: Path) -> None:
    """?range=30：三态各归位，条目五键形状齐全。"""
    _article(studio_root, "planned.md", publish_at=str(TODAY + timedelta(days=3)))
    _log(
        studio_root,
        [
            {
                "ts": f"{TODAY}T10:00:00",
                "action": "draft",
                "article": "planned.md",
                "title": "已交付篇",
                "platform": "mp",
                "media_id": "M1",
            },
            {
                "ts": f"{TODAY}T11:00:00",
                "action": "publish",
                "article": "planned.md",
                "title": "已发布篇",
                "platform": "mp",
                "manual": True,
            },
        ],
    )
    code, payload = _request(server, "GET", "/api/plan?range=30")
    assert code == 200
    assert payload["ok"] is True
    statuses = {item["status"] for item in payload["items"]}
    assert statuses == {"planned", "delivered", "published"}
    for item in payload["items"]:
        assert set(item) == {"article", "title", "status", "publish_at", "date"}
        assert item["article"] == "planned.md"  # root 相对 posix
    planned = next(i for i in payload["items"] if i["status"] == "planned")
    assert planned["publish_at"] == str(TODAY + timedelta(days=3))
    delivered = next(i for i in payload["items"] if i["status"] == "delivered")
    assert delivered["publish_at"] is None and delivered["date"] == str(TODAY)


def test_plan_range_enum_strict(server) -> None:
    """range 枚举外 → 400；90 合法。"""
    code, _ = _request(server, "GET", "/api/plan?range=7")
    assert code == 400
    code, payload = _request(server, "GET", "/api/plan?range=90")
    assert code == 200 and payload["ok"] is True and "items" in payload


def test_plan_default_is_text_form(server, studio_root: Path) -> None:
    """无参（前端 showPlan 既有调用形态）→ text 文字周历，非 items。"""
    _article(studio_root, "a.md", publish_at=str(TODAY))
    code, payload = _request(server, "GET", "/api/plan")
    assert code == 200
    assert payload["ok"] is True and payload["week"] == ""
    assert payload["text"].startswith("周历 ")
    assert "计划: 标题" in payload["text"]


def test_plan_week_param_and_invalid(server) -> None:
    """?week=2026-W47 回看指定周；非法周序 → 400。"""
    code, payload = _request(server, "GET", "/api/plan?week=2026-W47")
    assert code == 200 and payload["week"] == "2026-W47"
    assert payload["text"].startswith("周历 2026-W47")
    code, _ = _request(server, "GET", "/api/plan?week=bad")
    assert code == 400


def test_plan_post_405(server) -> None:
    """GET-only 端点：POST → 405。"""
    code, _ = _request(server, "POST", "/api/plan", {"confirm": True})
    assert code == 405


# ── plan_items 服务面（窗口/归一/宽容）─────────────────────


def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    root = tmp_path / "arts"
    root.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return root


def test_plan_items_window_symmetric(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口=|date-today|<days：±(days-1) 边界含/外不含。"""
    root = _env(tmp_path, monkeypatch)
    _article(root, "in_past.md", publish_at=str(TODAY - timedelta(days=29)))
    _article(root, "in_future.md", publish_at=str(TODAY + timedelta(days=29)))
    _article(root, "out_past.md", publish_at=str(TODAY - timedelta(days=30)))
    _article(root, "out_future.md", publish_at=str(TODAY + timedelta(days=30)))
    items = plan_service.plan_items(root, days=30, today=TODAY)
    names = [i["article"] for i in items]
    assert names == ["in_past.md", "in_future.md"]  # date 升序（10-18 < 12-15）


def test_plan_items_normalizes_absolute_log_article(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """日志行 article 绝对路径（CLI/Studio 写入口径）→ root 相对 posix。"""
    root = _env(tmp_path, monkeypatch)
    _log(
        root,
        [
            {
                "ts": f"{TODAY}T09:00:00",
                "action": "draft",
                "article": str(root / "abs.md"),
                "title": "绝对路径行",
                "platform": "mp",
            }
        ],
    )
    items = plan_service.plan_items(root, days=30, today=TODAY)
    assert [(i["article"], i["status"]) for i in items] == [("abs.md", "delivered")]


def test_plan_items_tolerant_bad_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """坏行（无日期/坏 JSON）与坏 frontmatter 文章宽容跳过，不抛错。"""
    root = _env(tmp_path, monkeypatch)
    (root / "broken.md").write_text("不是 frontmatter 的普通笔记\n", encoding="utf-8")
    _log(root, [{"action": "draft", "article": "no_ts.md", "title": "无日期行"}])
    (root / "publish_log.jsonl").open("a").write("{ 坏 JSON 行\n")
    assert plan_service.plan_items(root, days=30, today=TODAY) == []
