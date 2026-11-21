"""F22 光栅层 — lazy playwright 逐卡 element.screenshot（§十八 10.0）。

复用 browser.session 的浏览器导入与 BrowserNotInstalled（同一错误
类型、同一条安装提示，不另起炉灶）：本模块不 import playwright，起拍时经
session._sync_playwright() lazy 导入，缺失即 BrowserNotInstalled。

拆截实现：每卡一个固定尺寸容器（templates 的 section.musespiral-card），按容器
class 定位 element.screenshot 逐卡拍——不整页截图后裁剪，产物尺寸即容器
尺寸；视口取卡组 HTML 自带的 data-w/data-h（与卡容器同源，不在此写死规格）。
"""
from __future__ import annotations

from pathlib import Path

from ....browser.session import BrowserNotInstalled, _sync_playwright
from .templates import card_specs_from_html


def require_browser() -> None:
    """预检浏览器可用性：缺 playwright 抛 BrowserNotInstalled（不落任何文件）。

    CLI 截图路径先调它，避免装没装浏览器都要先落半成品目录。
    """
    _sync_playwright()


def shoot(html_path: str | Path, out_dir: str | Path) -> list[Path]:
    """卡组单文件 HTML → 逐卡 PNG（<stem>-01.png…），返回产物路径列表（按卡序）。"""
    sync_playwright = _sync_playwright()  # 缺 playwright → BrowserNotInstalled
    html_file = Path(html_path).resolve()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    width, height = card_specs_from_html(html_file.read_text(encoding="utf-8"))

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.goto(f"file://{html_file}")  # 红线：光栅只 file:// 加载，不触网
            loc = page.locator(".musespiral-card")
            paths: list[Path] = []
            for i in range(loc.count()):
                target = out / f"{html_file.stem}-{i + 1:02d}.png"
                loc.nth(i).screenshot(path=str(target))
                paths.append(target)
        finally:
            browser.close()
    finally:
        pw.stop()
    return paths


__all__ = ["BrowserNotInstalled", "require_browser", "shoot"]
