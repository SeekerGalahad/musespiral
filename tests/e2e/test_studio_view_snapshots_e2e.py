"""W19a 视图 DOM Snapshot 层（real Chromium）——字符串锚的 DOM 替代地基。

动机（遗留问题深度评估正典 §二）：901 处字符串锚（test_studio_ui.py 等）
是"断言源码包含某字符串"的妥协——没有真 DOM 层的产物。本套件在真实
浏览器里断言**核心元素存在且可见**、关键文案可读，让改实现只要 DOM 行为
不变就不红。

与 test_studio_arch1_views_e2e.py 的分工：
  - arch1 套件：每视图 section 可见 + 高度>0 + innerHTML 非空（骨架注入/暗色）
  - 本套件：每视图 **3-5 个核心交互元素**（入口按钮/搜索框/状态灯/列表容器）
    用 Playwright locator 精确断言 is_visible()，给出比"innerHTML 非空"更
    强的行为保护；W19b 起按视图把 test_studio_ui.py 的存在性字符串锚逐批
    迁到本层（契约锁/否向断言/历史沿革类不动——它们保护的是另一类语义）。

每视图 1 个测试（11 视图），全部零外部网络（回环 studio.make_server）。
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright  # noqa: E402

from musespiral import studio  # noqa: E402
from musespiral.studio import actions as studio_actions  # noqa: E402

pytestmark = pytest.mark.e2e

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# 每视图核心元素（id）——来自 ui.views/<v>.html 实读（W19a 盘点 2026-11-24）
# 断言每个元素存在且 is_visible()（首屏 publish 无需注入等待，其余走 wait_for）
SNAPSHOT_IDS: dict[str, tuple[str, ...]] = {
    "sources": ("src-add-btn", "src-clip-btn", "src-crawl-btn", "src-verify-all-btn"),
    "material": ("mat-upload-btn", "mat-gen-btn", "mat-reg-btn", "coll-rail"),
    "books": ("bk-lamp", "bk-subs-btn", "bk-search-box", "bk-guide"),
    "compose": ("cm-new-slug", "cm-new-title", "cm-smart-btn", "cm-new-btn"),
    "design": ("dv-load-btn", "dv-save-btn", "dv-export-btn", "dv-specs"),
    "publish": ("fetch-url", "fetch-btn", "queue-filters", "count-all"),
    "insight": ("radar-card", "radar-refresh", "radar-body", "stat-tiles"),
    "article": ("detail-title", "detail-meta", "mark-reviewed-btn", "tts-btn"),
    "settings": ("cred-verify-btn", "kb-root-input", "kb-save-btn", "kb-sources-body"),
    "plugins": ("pg-refresh-btn", "pg-new-btn", "pg-grid", "pg-kinds"),
    # plugin-run 清单态：详情按钮（pr-back/pr-run/pr-flow）带 hidden，不在清单态断言
    "plugin-run": ("pr-title", "pr-pick", "view-plugin-run"),
}

@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))
    # 体验模式：让书籍详情态经 mock 引擎拿到 info/toc（本层只测 DOM 渲染）
    monkeypatch.setenv("MUSESPIRAL_ENGINE_MOCK", "1")

@pytest.fixture
def e2e_server(tmp_path: Path):
    studio_actions._reset_runner()
    root = tmp_path / "studio-demo"
    shutil.copytree(FIXTURES / "studio_demo", root)
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        studio_actions._reset_runner()

@pytest.fixture(scope="module")
def browser_page():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        yield page
        ctx.close()
        browser.close()

def _goto_view(page, base: str, name: str) -> None:
    # article 需 path/tab 参数（裸 #article 回落 publish）
    target = name + "?path=01_quick_start.md&tab=edit" if name == "article" else name
    page.goto(f"{base}/#{target}")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        """v => { const el = document.getElementById('view-' + v);
                  return el && !el.hidden && el.getBoundingClientRect().height > 0; }""",
        arg=name, timeout=6000,
    )

@pytest.mark.parametrize("view", list(SNAPSHOT_IDS))
def test_view_core_elements_visible(e2e_server, browser_page, view: str) -> None:
    """每视图核心交互元素在真实 DOM 中存在且可见（W19a snapshot 层）。"""
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    _goto_view(browser_page, base, view)
    for element_id in SNAPSHOT_IDS[view]:
        locator = browser_page.locator(f"#{element_id}")
        assert locator.count() == 1, f"{view}: #{element_id} 不唯一或缺失（count={locator.count()}）"
        assert locator.is_visible(), f"{view}: #{element_id} 存在但不可见"

def test_books_subs_state_has_crud_controls(e2e_server, browser_page) -> None:
    """书籍订阅子态（W17）snapshot：表单 3 输入 + 3 按钮 + 列表容器真在 DOM 里。"""
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    browser_page.goto(f"{base}/#books?tab=subs")
    browser_page.wait_for_load_state("domcontentloaded")
    browser_page.wait_for_function(
        "() => { const el = document.getElementById('bk-subs'); return el && !el.hidden; }",
        timeout=6000,
    )
    for element_id in ("bs-url", "bs-interval", "bs-silent", "bs-add", "bs-refresh-all", "bs-list", "bk-subs-back"):
        locator = browser_page.locator(f"#{element_id}")
        assert locator.count() == 1 and locator.is_visible(), f"订阅态 #{element_id} 缺失或不可见"


def test_books_detail_state_core_elements_visible(e2e_server, browser_page) -> None:
    """W19b 试点：书籍详情态 19 个核心元素 DOM 断言——替代 test_studio_ui
    里的字符串 id 集合锚（它们只证明"源码有这个 id"，本测试证明"真在 DOM 里
    可见"）。"""
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    browser_page.goto(
        f"{base}/#books?book=https://mock.example/book/1&source=https://mock.example/source"
    )
    browser_page.wait_for_load_state("domcontentloaded")
    browser_page.wait_for_function(
        """() => { const el = document.getElementById('bk-body');
                  return el && !el.hidden && el.getBoundingClientRect().height > 0; }""",
        timeout=8000,
    )
    # 详情态核心元素（id 集合锚原样迁入本层，逐元素 DOM 可见性断言）
    # 无条件可见的核心元素（列表/筛选/导航/摘要开关）
    detail_ids = ("bk-detail", "bk-body", "bk-cover", "bk-name",
                  "bk-src-sel", "bk-toc", "bk-filter", "bk-specs")
    for element_id in detail_ids:
        locator = browser_page.locator(f"#{element_id}")
        assert locator.count() == 1, f"详情态 #{element_id} 缺失"
        assert locator.is_visible(), f"详情态 #{element_id} 存在但不可见"
    # 条件渲染元素（范围输入/操作条/进度/摘要开关）：断言真在 DOM 里，可见性
    # 由交互驱动（点 custom / 勾选章节后才显示）——这比源码字符串锚更强。
    for element_id in ("bk-range", "bk-opbar", "bk-prog", "bk-digest", "bk-import-btn", "bk-absent"):
        locator = browser_page.locator(f"#{element_id}")
        assert locator.count() == 1, f"详情态条件元素 #{element_id} 不在 DOM 中"
