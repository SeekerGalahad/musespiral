"""Playwright 会话 — lazy import + 登录态存取（F16）。

playwright 是 [project.optional-dependencies] browser extra（红线③：核心功能
零依赖）：模块顶层不 import，仅在真正起浏览器时导入；缺失 raise
BrowserNotInstalled（含 pip extra、chromium 下载与境内镜像提示）。

运行路径纪律（红线①，结构测试 tests/test_browser.py 据此扫描本文件
与 flows/）：本包运行路径禁用一切 .click() 调用——发布按钮只经 selectors 的
publish_button 键定位后高亮提示，交互只用 goto/fill/set_input_files/press/
highlight。若未来确需非发布元素的点击，必须先把该交互收进显式命名的选择器
键（不得叫 publish_button），并同步修改结构测试。
"""
from __future__ import annotations

from types import TracebackType
from typing import Any

from . import cookies

INSTALL_HINT = (
    "未安装 Playwright，浏览器上传器不可用。安装步骤：\n"
    '  1) pip install "musespiral[browser]"\n'
    "  2) playwright install chromium\n"
    "境内网络可先设镜像加速下载："
    "export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/"
)


class BrowserNotInstalled(RuntimeError):
    """Playwright 缺失——附安装 extra、chromium 下载与镜像提示（可读错误）。"""


def _sync_playwright() -> Any:
    """lazy import playwright.sync_api.sync_playwright；缺失转 BrowserNotInstalled。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        # 测试以 sys.modules 注入 None 确定性复现本分支（真机缺失同理）
        raise BrowserNotInstalled(INSTALL_HINT) from exc
    return sync_playwright


class BrowserSession:
    """一次浏览器会话：launch（默认有头，供扫码登录）→ 带 cookie 的 context
    → 退出时保存 storage_state 刷新登录态。仅真实路径使用；dry-run 不经过本类。
    """

    def __init__(self, platform: str, *, headless: bool = False) -> None:
        self.platform = platform
        self.headless = headless
        self.page: Any = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None

    def __enter__(self) -> "BrowserSession":
        self._pw = _sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        context_kwargs: dict[str, Any] = {}
        if cookies.has(self.platform):
            context_kwargs["storage_state"] = str(cookies.path(self.platform))
        self._context = self._browser.new_context(**context_kwargs)
        self.page = self._context.new_page()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if self._context is not None:
                # 退出即保存/刷新登录态（storage_state 原生格式，落家目录）
                self._context.storage_state(path=str(cookies.path(self.platform)))
        finally:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
