"""创作工坊 e2e（2026-11-20 复核修复回归锁）：跨会话块隔离 + novel 双向可达。

两缺陷现场（复核报告，HEAD 即有、非单批引入）：

  ① HIGH 跨会话串台：cmWorkBlocks 工作副本缓存键只含轮号——两会话末轮号
     相同时，切会话后块区画**上一会话的缓存块**，其上的三态裁决以当前 slug
     落库（用户以为保留了眼前这块，实际 decide 打到了另一会话的同号块上；
     块 id b1… 跨会话可撞）。修复=缓存键补 slug（ui.js cmWorkBlocks）。
  ② MEDIUM novel 单向门：类型切换 chips 只画在 novel 面板内，而 cnPaint 对
     article 会话整面板销毁——article 态无任何切回 novel 的 UI 入口（后端
     kind 切换本就支持）。修复=chips 常驻 #cm-novel 顶部（正典 N-1 线框
     「会话类型切换（右栏顶部）」未条件化），两折叠区仍 novel 专属。

均从真实浏览器面锁（结构字面量锁见 tests/test_studio_ui.py
test_p3_block_structure_ops / test_compose_novel_ui.py 双态测试）。
AI 依赖进程内替换（服务线程与测试同进程，沿 test_studio_e2e 纪律）。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")  # e2e 前置：无 playwright 时 skip 而非 error

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from musespiral import studio  # noqa: E402
from musespiral.services import compose_service  # noqa: E402
from musespiral.studio import actions as studio_actions  # noqa: E402

pytestmark = pytest.mark.e2e

REPLY_A = "<<<block kind:para>>>甲会话第一块：林远翻开账本。\n"
REPLY_B = "<<<block kind:para>>>乙会话第一块：普通文章内容。\n"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """真 Studio 服务 + 按序出牌的 chat 假件（先生成甲、再生成乙）。"""
    replies = [REPLY_A, REPLY_B]

    def _fake_chat(messages, **kwargs):
        return replies.pop(0) if replies else REPLY_B

    monkeypatch.setattr(compose_service, "_chat", _fake_chat)
    monkeypatch.setattr(compose_service, "_chat_stream",
                        lambda messages, **kwargs: _fake_chat(messages, **kwargs))
    root = tmp_path / "articles"
    root.mkdir()
    studio_actions._reset_runner()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        studio_actions._reset_runner()


def _post(httpd, path: str, body: dict) -> dict:
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=15)
    conn.request("POST", path, body=json.dumps(body),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data


def _wait_job(httpd, job_id: str, tries: int = 200) -> dict:
    for _ in range(tries):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=15)
        conn.request("GET", f"/api/jobs/{job_id}")
        job = json.loads(conn.getresponse().read())
        conn.close()
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job 轮询超时")


def _make_round(httpd, slug: str) -> None:
    payload = _post(httpd, "/api/compose/new",
                    {"slug": slug, "title": slug, "confirm": True})
    assert payload.get("ok"), payload
    payload = _post(httpd, "/api/compose/generate", {"slug": slug, "confirm": True})
    job = _wait_job(httpd, payload["job_id"])
    assert job["status"] == "done", job


def test_cross_session_blocks_isolated_and_decide_lands_right(server) -> None:
    """HIGH 回归锁：甲/乙两会话各 1 轮（末轮号同为 1），乙→甲切换后
    块区必须画**甲**的块（修复前画乙的缓存块）；此时点「保留」，
    decide 必须落进**甲**的 1.decisions.json、乙的保持空。"""
    root, httpd = server
    _make_round(httpd, "rev-novel")
    _make_round(httpd, "rev-art")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            # 进乙会话（末位访问者——工作副本由此建立）
            page.goto(f"{base}/#compose?s=rev-art")
            expect(page.locator("#cm-blocks")).to_contain_text("乙会话第一块", timeout=10000)
            # 切甲会话：末轮号同为 1，修复前块区仍画乙的缓存块
            page.goto(f"{base}/#compose?s=rev-novel")
            expect(page.locator("#cm-blocks")).to_contain_text("甲会话第一块", timeout=10000)
            stale = "乙会话第一块" in page.locator("#cm-blocks").inner_text()
            assert not stale, "块区串台：画的是上一会话（rev-art）的缓存块"

            # 在甲会话点 b1「保留」——落库必须只进甲
            # （cmBlockRow 对已裁决块仍渲染按钮行、裁决态以 st-keep 类+徽标呈现）
            page.locator('[data-cm-keep="b1"]').first.click()
            expect(page.locator(".cm-block.st-keep").first).to_be_visible(timeout=10000)
            dec_a = json.loads((root / "rev-novel.compose" / "rounds" / "1.decisions.json")
                               .read_text(encoding="utf-8"))
            dec_b = json.loads((root / "rev-art.compose" / "rounds" / "1.decisions.json")
                               .read_text(encoding="utf-8"))
            assert any(d.get("block") == "b1" and d.get("action") == "keep" for d in dec_a), dec_a
            assert dec_b == [], f"裁决打错会话：rev-art 的 decisions 被写入 {dec_b}"
        finally:
            browser.close()


def test_novel_mode_reachable_both_ways_from_ui(server) -> None:
    """MEDIUM 回归锁：article 会话也有类型切换 chips（右栏顶部常驻），
    点「📖 小说模式」后两折叠区出现；再切回 article 折叠区销毁、chips 仍在。"""
    _, httpd = server
    _post(httpd, "/api/compose/new", {"slug": "s-art", "title": "文章", "confirm": True})

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context().new_page()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            page.goto(f"{base}/#compose?s=s-art")
            expect(page.locator("#cm-grid")).to_be_visible(timeout=10000)
            # article 态：chips 在、折叠区不在
            chip = page.locator('#cm-novel .chip[data-cn="k:novel"]')
            expect(chip).to_be_visible(timeout=10000)
            assert page.locator("#cm-novel details").count() == 0
            # 点 chip 切 novel → 两折叠区出现（无需任何 API 手工干预）
            chip.click()
            expect(page.locator("#cm-novel details")).to_have_count(2, timeout=10000)
            expect(page.locator("#cm-novel").get_by_text("📖 前情提要")).to_be_visible()
            # 再切回 article：折叠区销毁、chips 仍在（双向可达）
            page.locator('#cm-novel .chip[data-cn="k:article"]').click()
            expect(page.locator("#cm-novel details")).to_have_count(0, timeout=10000)
            expect(page.locator('#cm-novel .chip[data-cn="k:novel"]')).to_be_visible()
        finally:
            browser.close()
