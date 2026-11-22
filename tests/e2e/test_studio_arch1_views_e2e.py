"""ARCH-1 ③ 集成回归：全部视图渲染 + 暗色模式（real Chromium）。

验收红线（正典 musespiral-极限架构与功能扩展总规划 §ARCH-1 / 任务书 12-15）：
  12. 所有视图在浏览器中渲染结果与拆分前一致（空占位 section 经 html.js 注入骨架）；
  15. 暗色模式正常（CSS 拆分不能丢失 dark theme 的视图样式——视图样式走
      CSS 变量 var(--ink-*/--paper-*)，暗色 token 覆写在 core.css 的
      html[data-theme=dark]，加载顺序 core.css 先于 views/<v>.css）。

每视图两态（light/dark）各断言：section 可见 + 高度>0 + innerHTML 非空（骨架确已注入
而非残留空占位）+ 无 assets/ 级 404（css/js/html.js 装载链全通）。
表 VIEWS 与 ui.js VIEWS 同源（11 视图：任务书「12 视图」为笔误，实际 10 懒+1 首屏）。

harness 沿 test_studio_e2e.py：playwright chromium + 回环服务器 + HOME 隔离。
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
VIEWS = ["sources", "material", "books", "compose", "design", "publish",
         "insight", "article", "settings", "plugins", "plugin-run"]


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


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


def test_all_views_render_in_light_and_dark(e2e_server) -> None:
    """11 视图 × (light,dark) 全部可见且骨架注入，assets 装载零 404。"""
    base = f"http://127.0.0.1:{e2e_server.server_address[1]}"
    asset_404s: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.on("response", lambda r: asset_404s.append(r.url)
              if r.status >= 400 and "/assets/" in r.url else None)
        pg.goto(f"{base}/#publish")
        pg.wait_for_timeout(800)  # core boot + meta

        for theme in ("light", "dark"):
            pg.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
            for name in VIEWS:
                # article 需 path 参数（裸 #article 按设计回落 #publish）
                target = (name + "?path=01_quick_start.md&tab=edit") \
                    if name == "article" else name
                pg.evaluate("t => { location.hash = '#' + t; }", target)
                pg.wait_for_function(
                    """v => {
                        const el = document.getElementById('view-' + v);
                        return el && !el.hidden &&
                               el.getBoundingClientRect().height > 0 &&
                               el.innerHTML.trim().length > 0;
                    }""", arg=name, timeout=6000)

        # 暗色 token 确实生效（视图 CSS 的 var(--paper-0) 取到暗值，非浅色值）
        pg.evaluate("() => document.documentElement.setAttribute('data-theme','dark')")
        paper_dark = pg.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--paper-0').trim()")
        pg.evaluate("() => document.documentElement.setAttribute('data-theme','light')")
        paper_light = pg.evaluate(
            "() => getComputedStyle(document.documentElement)"
            ".getPropertyValue('--paper-0').trim()")
        browser.close()

    assert asset_404s == [], f"视图装载链有 assets/ 404: {asset_404s}"
    assert paper_dark == "#211f1e", paper_dark
    assert paper_light == "#faf9f7", paper_light
    assert paper_dark != paper_light
