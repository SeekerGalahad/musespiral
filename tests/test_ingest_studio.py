"""B 采集扩展 Studio 契约测试（§五 5.2）：/api/ingest/* 四端点 + rss 源契约面
（T65-9 + T66-1…T66-6 + 手动爬取 pending 流 + refresh 端点）。

真实回环服务器 + MUSESPIRAL_HOME 隔离 + mock fetcher（测试不联网）；robots
缓存逐用例清态。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra import fetcher
from musespiral.services import ingest_service as ing
from musespiral.services import materials_service as ms
from musespiral.services import sources_service as src_svc
from tests.test_clipper import BLOG_HTML, LIST_HTML


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    fetcher.reset_robots_cache()
    yield home
    fetcher.reset_robots_cache()


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
        from musespiral.studio.actions import _reset_runner

        _reset_runner()


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


def _wait_job(server, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, job = _request(server, "GET", f"/api/jobs/{job_id}")
        assert code == 200
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


class _FetchSpy:
    """fetcher.fetch 替身：可切换 body；记录调用（E2E-5 零请求断言面）。"""

    def __init__(self, body: bytes = BLOG_HTML.encode(), final: str = "https://blog.example.com/post") -> None:
        self.body = body
        self.final = final
        self.calls: list[str] = []

    def __call__(self, url: str, *, mode: str = "auto"):
        self.calls.append(url)
        return fetcher.FetchResult(
            final_url=self.final, status=200, content_type="text/html",
            body=self.body, robots={"state": "allowed", "crawl_delay": None},
        )


def _materials_total() -> int:
    return len(ms.list_materials())


# ── T65-9 契约：rss 源入 GET /api/sources + type 缺失 400 文案 ──────


def _rss_feed_body() -> bytes:
    return (
        '<rss version="2.0"><channel><title>契约源</title>'
        '<item><title>一条</title><link>https://blog.example.com/p1/</link>'
        "<guid>urn:uuid:c-1</guid></item></channel></rss>"
    ).encode()


def test_t65_9_rss_source_contract_surface(server, studio_root, _isolate_home, monkeypatch) -> None:
    monkeypatch.setattr(ing.fetcher, "fetch", _FetchSpy(_rss_feed_body()))
    code, reg = _request(server, "POST", "/api/sources", {
        "confirm": True, "id": "zhipu-blog", "type": "rss",
        "config": {"url": "https://blog.example.com/feed.xml"},
    })
    assert code == 200 and reg["ok"] is True
    assert reg["source"]["type"] == "rss"
    assert reg["source"]["config"]["interval_minutes"] == 360  # 缺省补
    assert reg["source"]["config"]["max_items"] == 50
    # 刷新走新端点（job kind=ingest → rss 分派）
    code, sub = _request(server, "POST", "/api/ingest/refresh",
                         {"confirm": True, "source_id": "zhipu-blog"})
    assert code == 200 and sub["job_id"]
    job = _wait_job(server, sub["job_id"])
    assert job["kind"] == "ingest" and job["status"] == "done"
    assert job["result"]["status"] == "ok" and job["result"]["added"] == 1
    # GET /api/sources：type 枚举含 rss + health.last_refresh（ISO）
    code, payload = _request(server, "GET", "/api/sources")
    assert code == 200
    row = next(s for s in payload["list"] if s["id"] == "zhipu-blog")
    assert row["type"] == "rss"
    assert row["health"]["last_refresh"] and "T" in row["health"]["last_refresh"]
    # POST /api/sources type 缺失 → 400 文案含四型枚举
    code, err = _request(server, "POST", "/api/sources", {"confirm": True, "id": "x"})
    assert code == 400
    assert "radar_hub/kb/dir/rss" in err["error"]
    # 缺 source_id → 400
    assert _request(server, "POST", "/api/ingest/refresh", {"confirm": True})[0] == 400


# ── T66-1/T66-2 预览（服务级）：零落盘 + 降级链 ─────────────────────


def test_t66_1_clip_preview_zero_write(_isolate_home, monkeypatch) -> None:
    monkeypatch.setattr(ing.fetcher, "fetch", _FetchSpy())
    before = _materials_total()
    preview = ing.clip_url("https://blog.example.com/post")
    assert preview["mode"] == "crawled"
    assert preview["confidence"] >= 0.5
    assert preview["text"].strip()
    assert preview["extractor"] == "readability-lxml"
    assert preview["word_count"] > 0
    assert preview["url"] == "https://blog.example.com/post"
    assert _materials_total() == before  # preview 零落盘


def test_t66_2_low_confidence_degrades_to_link(_isolate_home, monkeypatch) -> None:
    monkeypatch.setattr(ing.fetcher, "fetch", _FetchSpy(LIST_HTML.encode()))
    preview = ing.clip_url("https://blog.example.com/listing")
    assert preview["mode"] == "link"
    assert preview["text"] == ""
    assert preview["confidence"] < 0.5  # 降级链产物形态非错误


# ── T66-3 Studio preview → adopt 全链（job kind=clip）──────────────


def test_t66_3_clip_preview_then_adopt(server, studio_root, _isolate_home, monkeypatch) -> None:
    monkeypatch.setattr(ing.fetcher, "fetch", _FetchSpy())
    code, sub = _request(server, "POST", "/api/ingest/clip",
                         {"confirm": True, "action": "preview", "url": "https://blog.example.com/post"})
    assert code == 200 and sub["ok"] is True and sub["job_id"]
    job = _wait_job(server, sub["job_id"])
    assert job["kind"] == "clip" and job["status"] == "done"
    result = job["result"]
    for key in ("mode", "url", "title", "text", "confidence", "extractor",
                "word_count", "license", "robots"):  # 8 键 + robots
        assert key in result
    assert result["mode"] == "crawled"
    # adopt（preview 结果原样回传，可人工编辑）
    code, adopt = _request(server, "POST", "/api/ingest/clip", {
        "confirm": True, "action": "adopt", "url": result["url"],
        "title": result["title"], "text": result["text"], "mode": result["mode"],
        "confidence": result["confidence"], "word_count": result["word_count"],
        "license": result["license"],
    })
    assert code == 200 and adopt["ok"] is True
    material = adopt["material"]
    assert material["type"] == "crawled"
    meta = material["payload"]["meta"]
    assert meta["extractor"] == "readability-lxml"
    assert meta["confidence"] > 0 and meta["word_count"] > 0  # meta 三键
    assert ms.get_material(material["id"]) is not None


# ── T66-4 拒绝即消失（不 adopt → 素材库零变化）──────────────────────


def test_t66_4_preview_without_adopt_zero_residual(server, studio_root, _isolate_home, monkeypatch) -> None:
    monkeypatch.setattr(ing.fetcher, "fetch", _FetchSpy())
    before = _materials_total()
    _, sub = _request(server, "POST", "/api/ingest/clip",
                      {"confirm": True, "action": "preview", "url": "https://blog.example.com/post"})
    _wait_job(server, sub["job_id"])
    assert _materials_total() == before  # 素材库零行零残留（job 台账属运行口径注）


# ── T66-5 提交期拒（IP 字面量 → 400，零请求发出）────────────────────


def test_t66_5_submit_gate_rejects_local_targets(server, studio_root, _isolate_home, monkeypatch) -> None:
    spy = _FetchSpy()
    monkeypatch.setattr(ing.fetcher, "fetch", spy)
    for bad in ("http://127.0.0.1:8787/x", "http://10.0.0.5/x", "http://[::1]:8790/x"):
        code, err = _request(server, "POST", "/api/ingest/clip",
                             {"confirm": True, "action": "preview", "url": bad})
        assert code == 400, bad
        assert "IP 字面量" in err["error"]
    assert spy.calls == []  # mock fetcher 断言零请求发出


# ── T66-6 confirm 闸门（新端点不豁免）+ 契约锁 ──────────────────────


def test_t66_6_confirm_gate_and_contract_lock(server, studio_root, _isolate_home) -> None:
    code, err = _request(server, "POST", "/api/ingest/clip", {"action": "preview", "url": "https://x.example.com/a"})
    assert code == 400
    assert "动作接口必须带 confirm:true" in err["error"]
    for route in ("/api/ingest/refresh", "/api/ingest/crawl"):
        assert _request(server, "POST", route, {})[0] == 400
    from tests.test_architecture import CONTRACT_POST_EXACT

    for route in ("/api/ingest/refresh", "/api/ingest/clip", "/api/ingest/crawl"):
        assert route in CONTRACT_POST_EXACT  # CONTRACT_POST_EXACT 含该路由（路由面锁同步）
    # action 枚举外 → 400
    code, err = _request(server, "POST", "/api/ingest/clip",
                         {"confirm": True, "action": "zzz", "url": "https://x.example.com/a"})
    assert code == 400 and "preview/adopt" in err["error"]


# ── 手动爬取（F67 首切片）：crawl → pending → adopt → dup ───────────


def test_crawl_manual_pending_adopt_dup(server, studio_root, _isolate_home, monkeypatch) -> None:
    spy = _FetchSpy()
    monkeypatch.setattr(ing.fetcher, "fetch", spy)
    # ① crawl → pending 区
    code, sub = _request(server, "POST", "/api/ingest/crawl",
                         {"confirm": True, "url": "https://blog.example.com/post"})
    assert code == 200 and sub["job_id"]
    job = _wait_job(server, sub["job_id"])
    assert job["kind"] == "clip" and job["status"] == "done"
    result = job["result"]
    assert result["status"] == "pending" and result["pending_id"]
    assert spy.calls  # 手动触发确实出网（经 fetcher 咽喉）
    # ② status 端点可见 pending
    code, status = _request(server, "GET", "/api/ingest/status")
    assert code == 200 and status["ok"] is True
    assert status["pending_count"] == 1
    assert status["pending"][0]["url"] == "https://blog.example.com/post"
    assert status["busy"] is False  # 串行锁空闲
    assert any(s["id"] == "clip" or s["type"] for s in status["sources"])  # 源面非空
    # ③ adopt（pending_id 翻 adopted）
    code, adopt = _request(server, "POST", "/api/ingest/clip", {
        "confirm": True, "action": "adopt", "pending_id": result["pending_id"],
        "url": result["url"], "title": result["title"] or "人工标题",
        "text": result["text"], "mode": result["mode"],
        "confidence": result["confidence"], "word_count": result["word_count"],
    })
    assert code == 200 and adopt["ok"] is True
    assert _materials_total() == 1
    code, status2 = _request(server, "GET", "/api/ingest/status")
    assert status2["pending_count"] == 0  # adopted 出 pending 区
    # ④ 再 crawl 同 URL → dup（已存在标 dup 不入库）
    code, sub2 = _request(server, "POST", "/api/ingest/crawl",
                          {"confirm": True, "url": "https://blog.example.com/post"})
    job2 = _wait_job(server, sub2["job_id"])
    assert job2["result"]["status"] == "dup"
    assert _materials_total() == 1  # dup 不入库
    # ⑤ 提交期拒：IP 字面量 → 400 且零请求
    n_calls = len(spy.calls)
    code, _ = _request(server, "POST", "/api/ingest/crawl",
                       {"confirm": True, "url": "http://192.168.1.10/x"})
    assert code == 400 and len(spy.calls) == n_calls


def test_crawl_status_get_only_semantics(server, studio_root, _isolate_home) -> None:
    assert _request(server, "POST", "/api/ingest/status", {"confirm": True})[0] == 405
    assert _request(server, "GET", "/api/ingest/status")[0] == 200
