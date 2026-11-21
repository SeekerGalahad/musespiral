"""插件流水线 run 目录命令组：plrun list / plrun prune（复核 P4 回收面）。

插件流水线的 run 目录（~/.musespiral/pipeline_runs/<run_id>/，含 workspace
抽帧 JPEG / clip MP4 / 输出 JSON）此前只有 Studio 读面（runs/<id>），无
清单无回收——成功态 workspace 引擎刻意保留（失败看现场），累积无上限
（2026-11-20 复核判例：帧产物是设计内落 config.home_dir 的应用状态，
但「无回收路径」不是设计意图）。本命令组补齐：list 看现状、prune 按
LRU 保留最近 N 次（agent runs --prune 判例同款，默认 PRUNE_KEEP=5）。
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from ..services import pipeline_service_plugins as psp


@click.group()
def plrun() -> None:
    """插件流水线 run 目录：list 清单 / prune 回收（LRU 保最近 N 次）。"""


@plrun.command("list")
@click.option("--limit", default=20, show_default=True, type=int, help="最多显示条数")
def list_cmd(limit: int) -> None:
    """列出现存 run（新在前）：run_id / 状态 / 节点 / 结束时间。"""
    root = psp.pipeline_runs_root()
    if not root.is_dir():
        click.echo("（无 run 目录——还没跑过插件流水线）")
        return
    rows: list[tuple[str, dict]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        finished = str(meta.get("finished") or "")
        try:
            stamp = directory.stat().st_mtime
        except OSError:
            stamp = 0.0
        rows.append((finished or f"mtime:{stamp:.0f}", {
            "run_id": str(meta.get("run_id") or directory.name),
            "status": str(meta.get("status") or "unknown"),
            "nodes": "→".join(str(n) for n in (meta.get("nodes") or [])),
            "finished": finished or "—",
        }))
    rows.sort(key=lambda pair: (pair[0], pair[1]["run_id"]), reverse=True)
    for _key, row in rows[: max(int(limit), 1)]:
        click.echo(f"{row['run_id']}  {row['status']:<9} {row['finished']:<19} {row['nodes']}")
    if len(rows) > int(limit):
        click.echo(f"…共 {len(rows)} 条（--limit 可调）", err=True)


@plrun.command("prune")
@click.option("--keep", default=psp.PRUNE_KEEP, show_default=True, type=int,
              help="保留最近多少次 run")
@click.option("--yes", is_flag=True, help="跳过确认（脚本/定时场景）")
def prune_cmd(keep: int, yes: bool) -> None:
    """LRU 回收 run 目录（保留最近 keep 个，按 meta finished/started 排序）。"""
    root = psp.pipeline_runs_root()
    count = sum(1 for p in root.iterdir() if p.is_dir()) if root.is_dir() else 0
    if count == 0:
        click.echo("（无 run 目录，无可回收）")
        return
    if not yes and not click.confirm(
        f"将把 {root} 下 {count} 个 run 回收到最近 {keep} 个，继续？"
    ):
        click.echo("已取消")
        return
    out = psp.prune_runs(keep)
    click.echo(f"✅ 已回收 {out['removed']} 个 run，保留 {out['kept']} 个")


__all__ = ["plrun"]
