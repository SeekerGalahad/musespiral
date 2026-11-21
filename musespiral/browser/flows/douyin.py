"""抖音发布流 — 选择器见 ../selectors/douyin.yaml（真机校准前为占位值）。"""
from __future__ import annotations

from .base import BrowserFlow


class DouyinFlow(BrowserFlow):
    """抖音创作服务平台：文案=正文（与 pack README「复制 payload 全部内容粘贴
    进视频简介/文案框」同口径），标题单独填。"""

    platform = "douyin"
    desc_source = "body"
