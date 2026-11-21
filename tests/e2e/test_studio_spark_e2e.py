"""N-2 spark 灵感速记**前端一整圈** E2E（集成工程师轮，2026-11-21）。

验收链（设计 §五 P-1「快捷键 → 浮层 → 保存 → 素材库 spark 筛选可见 →
可被创作工坊引用」整圈，不是各自 200）：

  ⌘⇧I/Ctrl+Shift+I 任意视图唤起浮层 → 输入 Enter 保存（零壳 HTML，DOM
  动态建）→ #material?spark=1 深链筛出该条 → #compose 建会话 → 「✦ 我的
  素材」tab 找到它 → 「加入」入池 → #cm-sources 出现 material://<id>。

harness 沿 tests/e2e/test_studio_e2e.py：playwright chromium + 真实回环
服务器，MUSESPIRAL_HOME/tmp_path 全隔离。素材页断言的是**刚才那条 spark**
（标题取自输入首行前 24 字），不是预置数据——断的正是圈本身。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")  # e2e 前置：无 playwright 时 skip 而非 error

from playwright.sync_api import expect, sync_playwright  # noqa: E402

from musespiral import studio  # noqa: E402
from musespiral.studio import actions as studio_actions  # noqa: E402

pytestmark = pytest.mark.e2e

SPARK_TEXT = "E2E灵感：飞轮回程要整圈跑通才算数XYZ"


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def articles_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def e2e_server(articles_root: Path):
    studio_actions._reset_runner()
    httpd = studio.make_server(articles_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        studio_actions._reset_runner()


@pytest.fixture
def page(e2e_server):
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        pg = context.new_page()
        pg.goto(f"{base}/#publish")
        yield pg
        context.close()
        browser.close()


def test_spark_flywheel_keyboard_to_compose(page) -> None:
    """快捷键唤浮层 → Enter 保存 → 素材库 spark 深链可见 → 创作工坊可引用。"""
    # ── ① 快捷键唤起浮层（Ctrl+Shift+I 路径；零壳 HTML，DOM 动态建）──
    page.keyboard.press("Control+Shift+i")
    expect(page.locator(".spark-panel")).to_be_visible()
    expect(page.locator(".spark-in")).to_be_focused()

    # Esc 收拾链再唤起一次（toggle 契约）
    page.keyboard.press("Escape")
    expect(page.locator(".spark-panel")).to_have_count(0)
    page.keyboard.press("Control+Shift+i")
    expect(page.locator(".spark-panel")).to_be_visible()

    # ── ② 输入 Enter 保存 → toast 确认 → 浮层关闭 ────────────────────
    page.locator(".spark-in").fill(SPARK_TEXT)
    page.keyboard.press("Enter")
    expect(page.locator("#toast-text")).to_contain_text("灵感已入素材库")
    expect(page.locator(".spark-panel")).to_have_count(0)

    # ── ③ 素材库 spark 筛选可见（N-2 深链 #material?spark=1）─────────
    page.evaluate("location.hash='#material?spark=1'")
    rows = page.locator(".mat-row")
    expect(rows).to_have_count(1)
    expect(rows.first.locator(".mat-row-title")).to_contain_text(SPARK_TEXT[:24])

    # ── ④ 创作工坊建会话 → 「✦ 我的素材」tab 可见该 spark ────────────
    page.evaluate("location.hash='#compose'")
    page.locator("#cm-new-slug").fill("spark-e2e")
    page.locator("#cm-new-btn").click()
    expect(page.locator("#toast-text")).to_contain_text("会话已建")
    page.locator('[data-cmt="mine"]').click()
    mine = page.locator("#cm-mine-list .cm-pool-row")
    expect(mine).to_have_count(1)
    expect(mine.first).to_contain_text(SPARK_TEXT[:24])

    # ── ⑤ 「加入」入池 → 池面出现 material:// 定位符（可被引用）──────
    mine.first.locator("[data-cm-mtadd]").click()
    expect(page.locator("#cm-sources .cm-src .tag.accent").first).to_contain_text("material://")
    # 池行翻转为「移除」（added 态）
    expect(mine.first.locator(".cm-rm")).to_be_visible()
    expect(page.locator("#cm-mine-list [data-cm-mtadd]")).to_have_count(0)


def test_spark_shortcut_guard_in_input_fields(page) -> None:
    """验收项 6 防误触：textarea / input / contenteditable 聚焦时快捷键不唤起浮层。

    与 g 序列（ui.js:946 `!e.target.closest('input,select,textarea')`）同语义，
    spark 快捷键不得在用户打字时劫持（CM5 隐藏 textarea 也在此列）。
    失焦后普通态仍须能唤起（守卫只挡输入态，不废快捷键）。
    """
    # 探针 DOM：模拟真实输入框（CM5 的隐藏 textarea 同为 textarea 节点）
    page.evaluate("""
        const box = document.createElement('div');
        box.innerHTML = '<textarea id="review-ta"></textarea>'
          + '<input id="review-in">'
          + '<div id="review-ce" contenteditable="true"></div>';
        document.body.appendChild(box);
    """)
    for selector in ("#review-ta", "#review-in", "#review-ce"):
        page.locator(selector).focus()
        page.keyboard.press("Control+Shift+i")
        expect(page.locator(".spark-panel")).to_have_count(0)
        # 该输入确实持有焦点（防「focus 没生效，测试假绿」）
        expect(page.locator(selector)).to_be_focused()

    # 失焦回到普通态 → 快捷键仍可唤起（守卫不废功能）
    page.locator("#review-ta").blur()
    page.locator("body").click(position={"x": 2, "y": 2})
    page.keyboard.press("Control+Shift+i")
    expect(page.locator(".spark-panel")).to_be_visible()
