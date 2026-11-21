"""版本对比领域层（E-6，L2 纯；正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.7）。

单模块 `diff`：difflib.unified_diff → 带行号的结构化 hunks + HTML 红删绿增渲染。
零 I/O、零网络、零三方依赖；版本池的读取与恢复（落盘）在 services/version_service。
"""
from __future__ import annotations

from .diff import (
    CTX,
    DEFAULT_CONTEXT,
    DEL,
    INS,
    DiffHunk,
    DiffResult,
    DiffRow,
    diff_result,
    render_diff_html,
    summarize,
    unified_diff_lines,
)

__all__ = [
    "CTX",
    "DEFAULT_CONTEXT",
    "DEL",
    "INS",
    "DiffHunk",
    "DiffResult",
    "DiffRow",
    "diff_result",
    "render_diff_html",
    "summarize",
    "unified_diff_lines",
]
