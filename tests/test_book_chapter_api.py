"""读章端点 Studio 契约测试（2026-11-24 源中枢重设计批 A4）。

GET /api/book/chapter?sourceUrl=&bookUrl=&chapterUrl=：
  - 参数缺失 400
  - 引擎缺席 → 200 status:"absent"（与 info/toc 同语义）
  - mock 引擎（MUSESPIRAL_ENGINE_MOCK=1）→ 200 status:"ok" + title/content/chars
  - 超长正文截断（truncated:true）
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio

@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))

@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        from musespiral.studio.actions import _reset_runner

        _reset_runner()

def _get(server, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data

def test_chapter_requires_all_params(server) -> None:
    code, body = _get(server, "/api/book/chapter?sourceUrl=https://s")
    assert code == 400 and "chapterUrl" in body["error"]

def test_chapter_absent_when_engine_down(server, monkeypatch) -> None:
    monkeypatch.delenv("MUSESPIRAL_ENGINE_MOCK", raising=False)
    from musespiral.infra import bookengine, engine_select
    monkeypatch.setattr(bookengine.BookEngineClient, "available", lambda self: False)
    monkeypatch.setattr(engine_select, "_browser_ready", lambda: False)
    code, body = _get(server,
        "/api/book/chapter?sourceUrl=https://s&bookUrl=https://b&chapterUrl=https://c")
    assert code == 200 and body["status"] == "absent"

def test_chapter_mock_engine_returns_content(server, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_ENGINE_MOCK", "1")
    code, body = _get(server,
        "/api/book/chapter?sourceUrl=https://s&bookUrl=https://b&chapterUrl=https://mock.example/book/1/c3")
    assert code == 200 and body["status"] == "ok"
    assert body["content"] and body["chars"] == len(body["content"])
    assert "第 3 章" in body["title"]
    assert body["truncated"] is False

def test_chapter_truncates_oversize(server, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_ENGINE_MOCK", "1")
    from musespiral.infra import bookengine
    big = "长" * (300_000)
    monkeypatch.setattr(bookengine.BookEngineClient, "content",
                        lambda self, s, b, c: {"ok": True, "title": "超长", "content": big})
    code, body = _get(server,
        "/api/book/chapter?sourceUrl=https://s&bookUrl=https://b&chapterUrl=https://c")
    assert code == 200 and body["truncated"] is True
    assert body["chars"] == 200_000
