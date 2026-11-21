"""任务队列命令组：jobs（list / clean）——R4 新增，只读查看与清理。

命令注册归集成阶段（cli.py 装配；本文件只定义不注册，与 status_cmds 同约）。
list 只读最近 20 条（新在前）；clean 清 done/failed 行——字节级保留其余行
（含损坏行/非 UTF-8 行，见 core/jobs/store 写路径纪律）。CLI 本身保持同步
（正典 §三-3），本命令只消费 jobs.jsonl，不经任务层执行。
"""
from __future__ import annotations

import click

from ..jobs.store import JobStore


@click.group(name="jobs")
def jobs() -> None:
    """查看与清理任务队列（~/.musespiral/jobs.jsonl，Studio 后台动作的记录）。"""


@jobs.command("list")
def jobs_list() -> None:
    """最近 20 条任务（新在前；只读）。"""
    rows = JobStore().list(limit=20)
    if not rows:
        click.echo("暂无任务记录（Studio 动作会在此留下后台任务）。")
        return
    click.echo(f"{'ID':8}  {'类型':11}  {'状态':7}  {'创建时间':19}  说明")
    for job in rows:
        detail = ""
        if job.get("error"):
            detail = "❌ " + str(job["error"])
        elif job.get("phase") and job.get("status") == "running":
            detail = "进度 " + str(job["phase"])
        elif isinstance(job.get("result"), dict) and job["result"]:
            detail = "✅ " + ", ".join(f"{k}={v}" for k, v in job["result"].items())
        click.echo(
            f"{str(job.get('id', '?')):8}  {str(job.get('kind', '?')):11}  "
            f"{str(job.get('status', '?')):7}  {str(job.get('created', '')):19}  {detail}"
        )


@jobs.command("clean")
def jobs_clean() -> None:
    """清理已完成/失败任务行（done/failed；其余行字节级保留）。"""
    removed = JobStore().clean()
    click.echo(f"🧹 已清理 {removed} 条 done/failed 任务记录。")
