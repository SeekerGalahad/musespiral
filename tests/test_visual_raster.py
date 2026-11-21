"""F70-B/§5.2-② 光栅层测试（infra/visual/raster.py）——mock playwright 不依赖真实浏览器。

覆盖：lazy 导入缺 playwright → BrowserNotInstalled（静默降 C 由服务层决策，
本层抛）；offline_route 执法（http(s) abort / file:// continue）；正常路径
截图产物存在。真光栅用例挂 @pytest.mark.browser（缺浏览器 skip）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from musespiral.browser.session import BrowserNotInstalled, INSTALL_HINT
from musespiral.infra.visual import raster as raster_mod


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """默认无浏览器：让 _sync_playwright 抛 BrowserNotInstalled（隔离真实环境）。

    @pytest.mark.browser 用例跳过本 fixture（它们要真实 playwright——缺浏览器时
    用例内 importorskip 呈 skip）。
    """
    if request.node.get_closest_marker("browser"):
        return
    monkeypatch.setattr(
        raster_mod, "_sync_playwright",
        lambda: (_raise_browser_not_installed()),
    )


def _raise_browser_not_installed() -> Any:
    def _inner():
        raise BrowserNotInstalled(INSTALL_HINT)

    return _inner


def test_missing_playwright_raises_browser_not_installed(tmp_path: Path) -> None:
    """缺 playwright → BrowserNotInstalled（调用方降 C 路的判定信号）。"""
    with pytest.raises(BrowserNotInstalled):
        raster_mod.raster_svg("<svg/>", out=tmp_path / "o.png", size=(10, 10))


def test_browser_not_installed_has_install_hint() -> None:
    """异常文案含安装提示（可读性红线，与 browser.session 同源常量）。"""
    assert "pip install" in INSTALL_HINT or "Playwright" in INSTALL_HINT


# ── offline_route 执法（光栅不触网红线，poster.py:188-197 同款）────


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.aborted = False
        self.continued = False

    def abort(self) -> None:
        self.aborted = True

    def continue_(self) -> None:
        self.continued = True


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example/a.png",
        "https://evil.example/a.png",
        # scheme 大小写不敏感是 RFC 3986 事实——原样前缀比对会放过这些
        "HTTPS://x/y.png",
        "HTTP://x/y.png",
        "HtTpS://x/y.png",
        # 协议相对地址：继承页面协议即网络请求
        "//evil.example/a.png",
        "//x/y.png",
    ],
)
def test_offline_route_aborts_http(url: str) -> None:
    """① http(s) 与协议相对地址一律中止（大小写不敏感 + `//host/path`）。

    原实现按原样前缀比对 `url.startswith(("http://","https://"))`，实测
    `HTTPS://e/x.png` 与 `//e/x.png` 均被放行（aborted=False）——大小写
    绕过与协议相对绕过两条。scheme 大小写不敏感是 RFC 3986 事实，协议相对
    地址继承页面协议即网络请求，两者都必须中止。
    """
    route = _FakeRoute(url)
    raster_mod._offline_route(route)
    assert route.aborted and not route.continued, f"{url} 应中止（光栅不触网）"


def test_offline_route_allows_file() -> None:
    """file:// 放行（光栅只 file:// 加载）。"""
    route = _FakeRoute("file:///tmp/doc.svg")
    raster_mod._offline_route(route)
    assert route.continued and not route.aborted


def test_offline_route_allows_data_uri() -> None:
    """dataURI 放行（文档层 href 已执法，本层不再拦 data:）。"""
    route = _FakeRoute("data:image/png;base64,AAAA")
    raster_mod._offline_route(route)
    assert route.continued and not route.aborted


# ── 正常路径（mock playwright，不依赖真实浏览器）─────


class _FakePage:
    def screenshot(self, **kwargs) -> None:
        out = kwargs.get("path")
        if out:
            Path(out).write_bytes(b"PNGDATA")

    def goto(self, url: str, **kwargs) -> None:
        assert url.startswith("file://"), "红线：光栅只 file:// 加载"


class _FakeContext:
    route_calls: list[tuple] = []

    def route(self, pattern: str, handler) -> None:
        self.route_calls.append((pattern, handler))
        assert pattern == "**/*"
        assert handler is raster_mod._offline_route

    def new_page(self) -> _FakePage:
        return _FakePage()

    def close(self) -> None:
        pass


class _FakeBrowser:
    def new_context(self, viewport: dict) -> _FakeContext:
        assert viewport["width"] > 0 and viewport["height"] > 0
        return _FakeContext()

    def close(self) -> None:
        pass


class _FakeChromium:
    def launch(self, headless: bool) -> _FakeBrowser:
        assert headless is True
        return _FakeBrowser()


class _FakePW:
    chromium = _FakeChromium()

    def stop(self) -> None:
        pass


def test_raster_svg_writes_png_via_mock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mock playwright：raster_svg 写 PNG 到指定路径，context.route 挂 offline_route。"""
    class _Starter:
        def start(self):
            return _FakePW()

    monkeypatch.setattr(raster_mod, "_sync_playwright", lambda: (lambda: _Starter()))
    out = tmp_path / "out.png"
    result = raster_mod.raster_svg(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"/>',
        out=out, size=(100, 50),
    )
    assert result == out
    assert out.exists()
    assert _FakeContext.route_calls, "context.route 必须挂载（外域请求中止执法）"


# ── 真光栅（@browser，缺浏览器 skip）─────


@pytest.mark.browser
def test_raster_real_browser_size_exact(tmp_path: Path) -> None:
    """真浏览器：SVG → PNG 尺寸精确（缺 playwright 时 importorskip 呈 skip）。"""
    pytest.importorskip("playwright.sync_api")
    from PIL import Image

    out = tmp_path / "real.png"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="120" '
        'viewBox="0 0 240 120"><rect x="0" y="0" width="240" height="120" fill="#ff0000"/></svg>'
    )
    raster_mod.raster_svg(svg, out=out, size=(240, 120))
    with Image.open(out) as im:
        assert im.size == (240, 120)
