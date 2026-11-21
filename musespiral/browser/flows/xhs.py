"""小红书发布流 — 选择器见 ../selectors/xhs.yaml（真机校准前为占位值）。"""
from __future__ import annotations

from .base import BrowserFlow


class XhsFlow(BrowserFlow):
    """小红书创作服务平台：无独立描述框、正文即描述（与 pack README 同口径）。"""

    platform = "xhs"
    desc_source = "body"
