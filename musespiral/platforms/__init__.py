"""platforms —— 多平台发布适配器框架（§十四 F12）。

base（抽象基类）/ profiles（平台限制常量表）/ wechat（公众号适配器）/
registry（注册表）见各模块 docstring。公众号（mp）为首个 official_api
适配器，包装既有 token/uploader/publisher，publish/submit 行为不变。
"""
from __future__ import annotations

from .base import PlatformAdapter, Route
from .profiles import PLIMITS, get_limits
from .registry import REGISTRY, get, list_platforms, register
from .wechat import WeChatAdapter

__all__ = [
    "PlatformAdapter",
    "Route",
    "WeChatAdapter",
    "PLIMITS",
    "get_limits",
    "REGISTRY",
    "register",
    "get",
    "list_platforms",
]
