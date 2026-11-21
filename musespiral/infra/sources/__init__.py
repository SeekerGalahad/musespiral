"""infra/sources 输入源包（R6 + Phase C，正典 §四-R6 / §八 Phase C）。

Phase C 自 musespiral/core/sources/ 整体迁入，行为零变更。三源归一：
markdown_dir（目录扫描）/ sqlite（笔记库直连）/ url_fetch（公众号 URL
抓取）。旧模块 musespiral.notes / musespiral.notessql 为 re-export 垫片（Phase C
收尾删除），新代码一律从本包导入。
"""
from __future__ import annotations

from .markdown_dir import Note, scaffold_article, scan
from .sqlite import DEFAULT_COLS, get_by_id, list_from_db
from .url_fetch import FetchError, fetch_article

__all__ = [
    "DEFAULT_COLS",
    "FetchError",
    "Note",
    "fetch_article",
    "get_by_id",
    "list_from_db",
    "scaffold_article",
    "scan",
]
