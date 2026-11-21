"""知乎发布流 — 选择器见 ../selectors/zhihu.yaml（真机校准前为占位值）。

F37 平台登记漂移对齐：以 platforms/registry 为单一事实源，registry 的
manual_pack 平台集（channels/douyin/xhs/zhihu）即本包平台集——kuaishou
未在 registry 登记故不再注册（对拍测试看守，tests/test_uploader_browser.py）。
"""
from __future__ import annotations

from .base import BrowserFlow


class ZhihuFlow(BrowserFlow):
    """知乎专栏：文章编辑器无独立摘要框，正文即内容（与 xhs 同口径）。"""

    platform = "zhihu"
    desc_source = "body"
