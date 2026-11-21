"""R4 任务层包：store（jobs.jsonl 持久层）+ runner（进程内执行器）。

Phase C 自 musespiral/core/jobs/ 提升为顶层 jobs/ 包（正典 §八 Phase C：从
core 提升为独立 infra 模块），行为零变更；旧路径 musespiral.core.jobs.* 为
re-export 垫片（Phase C 收尾删除）。

公开入口：JobStore / JobRunner / JobFailure（studio actions 与 CLI jobs 命令
各取所需；导入路径 musespiral.jobs.*）。
"""
from __future__ import annotations

from .runner import JobFailure, JobRunner
from .store import JOBS_FILE, KINDS, STATUSES, JobStore, jobs_path, now_iso, sanitize_error

__all__ = [
    "JOBS_FILE",
    "KINDS",
    "STATUSES",
    "JobFailure",
    "JobRunner",
    "JobStore",
    "jobs_path",
    "now_iso",
    "sanitize_error",
]
