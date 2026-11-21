"""B站专栏发布流 — 选择器见 ../selectors/bilibili.yaml。

F42 骨架（待真机④校准）：selectors/flows 均为占位值，本期代码层 mock 验收
到平台规格核对为止。平台集以 platforms/registry 为单一事实源（对拍测试
看守，tests/test_uploader_browser.py）。
"""
from __future__ import annotations

from .base import BrowserFlow


class BilibiliFlow(BrowserFlow):
    """B站专栏：编辑器无独立摘要框，正文即内容（与 zhihu 同口径）。"""

    platform = "bilibili"
    desc_source = "body"
