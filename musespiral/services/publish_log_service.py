"""``publish_log_service`` —— 兼容门面（W2-2 发布域包化批，2026-11-24）。

实现已迁 ``services/publish/publish_log_service.py``；本模块 re-export 其**全量符号**
（含单下划线私有名——测试与 studio 调用面零改动）。符号集由
``tests/test_publish_pkg_facade.py`` 运行时对拍锁定（防未来漂移）。
"""
from __future__ import annotations

from .publish.publish_log_service import (  # noqa: F401
    Any,
    Path,
    _is_demo,
    _now_iso,
    _read_rows,
    _row_key,
    annotations,
    append_log,
    append_manual,
    append_uploaded,
    default_log_path,
    merge_logs,
    root_log_path,
    summarize,
    summarize_rows,
)

__all__ = [
    "Any",
    "Path",
    "_is_demo",
    "_now_iso",
    "_read_rows",
    "_row_key",
    "annotations",
    "append_log",
    "append_manual",
    "append_uploaded",
    "default_log_path",
    "merge_logs",
    "root_log_path",
    "summarize",
    "summarize_rows",
]
