"""F70-B 后端光栅化 — SVG→PNG 浏览器光栅化（L1；复用 browser.session 懒导入）。

- `rasterize_svg(svg, *, size=None) -> bytes`：SVG 字符串 → PNG 字节（F70-T6
  B vs C 对拍消费面；size 缺省从 SVG 头 width/height 解析）。
- `raster_svg(svg, *, out, size)`：SVG 字符串 → 落盘 PNG（F71-F73 导出编排
  消费面——同一内核，只是落盘形态）。
- 缺 playwright → BrowserNotInstalled；本层不自行降级——降级 B→C 由
  services/visual_service.py 决策（C 路在 core/visual/canvas/pillow_render.py）。
- `_offline_route`：http(s) 一律中止，file:// 放行（与
  core/visual/poster.py:188-197 同款「光栅不触网」红线，L1 复用）。
- 截图用 `clip` 定死在 size 矩形（不用 full_page——SVG 根页面无流式高度，
  full_page 会等字体/布局稳定到超时；clip 是 O(1) 取精确像素）。
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from ...browser.session import BrowserNotInstalled, _sync_playwright  # noqa: F401

_SIZE_RE_W = re.compile(r'<svg[^>]*\bwidth="(\d+)"')
_SIZE_RE_H = re.compile(r'<svg[^>]*\bheight="(\d+)"')


def _offline_route(route: Any) -> None:
    """光栅红线执法（§头红线）：网络请求一律中止，file:// / data: 放行。

    协议判定**大小写不敏感**且覆盖**协议相对地址**（`//host/path`）：
    `HTTPS://` / `HtTpS://` 与 `//host/x` 都是网络请求，原样前缀比对会被绕过
    （URL scheme 大小写不敏感是 RFC 3986 事实；协议相对地址继承页面协议即
    为 https）。放行面仅限 `file:`（光栅只 file:// 加载）与 `data:`（内联
    资源，文档层已执法）。
    """
    url = str(getattr(route.request, "url", ""))
    low = url.lower()
    if low.startswith(("http://", "https://", "//")):
        route.abort()
    else:
        route.continue_()


def _svg_size(svg: str) -> tuple[int, int]:
    """SVG 头 width/height → (w, h)（序列化产物恒带整数字面量，F70-T4 同源）。"""
    mw, mh = _SIZE_RE_W.search(svg), _SIZE_RE_H.search(svg)
    if not mw or not mh:
        raise ValueError("SVG 头缺 width/height（无法确定光栅尺寸）")
    return int(mw.group(1)), int(mh.group(1))


def _shoot(svg: str, out_path: Path, size: tuple[int, int]) -> Path:
    """内核：SVG → out_path PNG（无头 Chromium、file:// 加载、clip 精确像素）。"""
    sync_playwright = _sync_playwright()  # 缺 playwright → BrowserNotInstalled
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    width, height = int(size[0]), int(size[1])
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.route("**/*", _offline_route)  # 红线：外域请求一律中止
            page = context.new_page()
            with tempfile.TemporaryDirectory(prefix="musespiral-raster-") as tmpdir:
                svg_path = Path(tmpdir) / "doc.svg"
                svg_path.write_text(svg, encoding="utf-8")
                page.goto(svg_path.as_uri(), wait_until="load")  # 红线：只 file:// 加载
                page.screenshot(
                    path=str(out),
                    clip={"x": 0, "y": 0, "width": width, "height": height},
                )
        finally:
            browser.close()
    finally:
        pw.stop()
    return out


def rasterize_svg(svg: str, *, size: tuple[int, int] | None = None) -> bytes:
    """SVG 字符串 → PNG 字节（size 缺省取 SVG 头 width/height）。"""
    w, h = size or _svg_size(svg)
    with tempfile.TemporaryDirectory(prefix="musespiral-raster-out-") as tmpdir:
        out = _shoot(svg, Path(tmpdir) / "doc.png", (w, h))
        return out.read_bytes()


def raster_svg(svg: str, *, out: Path, size: tuple[int, int]) -> Path:
    """SVG 字符串 → 落盘 PNG（调用方指定路径；尺寸三路恒精确的前提）。"""
    return _shoot(svg, Path(out), size)


__all__ = ["raster_svg", "rasterize_svg"]
