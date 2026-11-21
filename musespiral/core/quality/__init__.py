"""发布前质量门（后端架构正典 §八 Phase B）——自 musespiral/checklist.py 拆分落位。

checks.py：格式级检查 run_checks + 共享结果类型 CheckLevel/CheckResult +
报告工具 format_report/has_failures；render_checks.py：渲染级检查
render_checks + 正文图片收集辅助 _collect_image_srcs。旧路径为 re-export
垫片，消费方 import 零改动。
"""
from __future__ import annotations

from .checks import CheckLevel, CheckResult, format_report, has_failures, run_checks
from .render_checks import render_checks

__all__ = [
    "CheckLevel",
    "CheckResult",
    "format_report",
    "has_failures",
    "render_checks",
    "run_checks",
]
