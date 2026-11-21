"""F16 浏览器上传器 — 填表交给机器、点发布留给人（真机验证留用户）。

公开 API：login / publish_flow / dry_run（CLI `musespiral upload` 组接线归集成
阶段）。playwright 为 browser extra（session 内 lazy import，缺失抛
BrowserNotInstalled 附安装与镜像提示）；选择器外置 selectors/*.yaml 可更新；
红线①：发布按钮代码永不点击（结构测试 tests/test_browser.py 守护）；
红线②：cookie 只存 ~/.musespiral/cookies/（仓外）。
"""
from __future__ import annotations

from .cookies import CookieMissing
from .flows import PLATFORMS, dry_run, get_flow, login, publish_flow
from .session import BrowserNotInstalled, BrowserSession

__all__ = [
    "PLATFORMS",
    "BrowserNotInstalled",
    "BrowserSession",
    "CookieMissing",
    "dry_run",
    "get_flow",
    "login",
    "publish_flow",
]
