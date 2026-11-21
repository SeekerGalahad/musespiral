"""视频号发布流 — 选择器见 ../selectors/channels.yaml（真机校准前为占位值）。"""
from __future__ import annotations

from .base import BrowserFlow


class ChannelsFlow(BrowserFlow):
    """视频号助手：标题/描述（简介框）/话题/封面各就各位（基础取材口径）。"""

    platform = "channels"
    desc_source = "desc"
