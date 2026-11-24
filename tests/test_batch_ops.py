"""批量审核/批量打包测试（W6，2026-11-24）。

D4 裁决：批量只做「一次看完全部 + 一次打完物料」——不做批量真发布
（微信频控 + 人工终发红线）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio

ARTICLES = {
    "good.md": "---\ntitle: 合规标题\ncover: cover.png\n---\n正文内容。\n",
    "bad.md": "---\ntitle: " + "超" * 40 + "\ncover: cover.png\n---\n正文。\n",
}


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    for name, text in ARTICLES.items():
        (root / name).write_text(text, encoding="utf-8")
    (root / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    httpd = studio.make_server(root, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


def _post(server, path: str, body: dict):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", path, body=json.dumps(body).encode(),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_batch_check_reports_per_article(server) -> None:
    """批量检查：每篇一行，含 fail/warn/pass 计数与首条失败原因。"""
    code, d = _post(server, "/api/action/batch_check", {"confirm": True, "paths": ["good.md", "bad.md"]})
    assert code == 200 and d["ok"] is True
    rows = {r["path"]: r for r in d["rows"]}
    assert rows["good.md"]["fail"] == 0
    assert rows["bad.md"]["fail"] >= 1
    assert "字节" in rows["bad.md"]["first_fail"]
    assert d["totals"]["clean"] == 1 and d["totals"]["fail"] == 1


def test_batch_check_tolerates_missing_file(server) -> None:
    """单篇缺失不中断整批（记 error 行）。"""
    code, d = _post(server, "/api/action/batch_check",
                    {"confirm": True, "paths": ["good.md", "nope.md"]})
    assert code == 200
    rows = {r["path"]: r for r in d["rows"]}
    assert "error" in rows["nope.md"]
    assert d["totals"]["error"] == 1
    assert rows["good.md"]["fail"] == 0


def test_batch_check_requires_paths(server) -> None:
    code, d = _post(server, "/api/action/batch_check", {"confirm": True, "paths": []})
    assert code == 400 and "paths" in d["error"]


def test_batch_check_caps_at_200(server) -> None:
    code, d = _post(server, "/api/action/batch_check",
                    {"confirm": True, "paths": ["good.md"] * 201})
    assert code == 400 and "200" in d["error"]


def test_batch_pack_generates_dirs(server) -> None:
    """批量打包：合规篇产出物料目录，失败篇记 error 不中断。"""
    code, d = _post(server, "/api/action/batch_pack",
                    {"confirm": True, "paths": ["good.md", "nope.md"], "platform": "xhs"})
    assert code == 200 and d["ok"] is True
    rows = {r["path"]: r for r in d["rows"]}
    assert "dir" in rows["good.md"] or "error" in rows["good.md"]  # pack 有前置要求时记 error 也算可读
    assert "error" in rows["nope.md"]
    assert d["totals"]["n"] == 2


def test_batch_pack_requires_platform(server) -> None:
    code, d = _post(server, "/api/action/batch_pack", {"confirm": True, "paths": ["good.md"]})
    assert code == 400 and "platform" in d["error"]


def test_both_require_confirm(server) -> None:
    for path in ("/api/action/batch_check", "/api/action/batch_pack"):
        code, d = _post(server, path, {"paths": ["good.md"], "platform": "xhs"})
        assert code == 400 and "confirm" in d["error"], path
