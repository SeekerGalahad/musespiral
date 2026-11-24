"""``adapt_service`` —— 兼容门面（W2-2 发布域包化批，2026-11-24）。

实现已迁 ``services/publish/adapt_service.py``；本模块 re-export 其**全量符号**
（含单下划线私有名——测试与 studio 调用面零改动）。符号集由
``tests/test_publish_pkg_facade.py`` 运行时对拍锁定（防未来漂移）。
"""
from __future__ import annotations

from .publish.adapt_service import (  # noqa: F401
    BatchAdaptError,
    Callable,
    Path,
    annotations,
    generate_batch,
    generate_variant,
)

__all__ = [
    "BatchAdaptError",
    "Callable",
    "Path",
    "annotations",
    "generate_batch",
    "generate_variant",
]
