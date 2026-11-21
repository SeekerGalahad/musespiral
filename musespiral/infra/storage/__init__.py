"""infra/storage 存储子包（正典 §2.3 infra/storage/）。

- jsonl：publish_log 的 JSONL 行级读写（追加/回填/汇总）
- filesystem：原子写与行尾保留共用件（jobs store / analytics 同源经验）

注：SQLite 笔记源在 infra/sources/sqlite.py（Phase C 任务书落位），
不在本包重复安家。
"""
from . import filesystem, jsonl  # noqa: F401
from .filesystem import atomic_write_bytes, line_tail
from .jsonl import (
    append_log,
    default_log_path,
    set_publish_url,
    summarize,
)

__all__ = [
    "append_log",
    "atomic_write_bytes",
    "default_log_path",
    "line_tail",
    "set_publish_url",
    "summarize",
]
