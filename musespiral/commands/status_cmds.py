"""文章状态命令（R3，重构正典 §四-R3）：musespiral status <article> [--set ...]。

无 --set 只读展示当前状态与合法迁移提示；--set 人工迁移（published 只由
publish 成功自动置位，不开放人工 --set）。本文件由轮次二集成阶段在 cli.py
统一注册（任务边界：只建命令文件，不动 cli.py）。
"""
from __future__ import annotations

import sys

import click

from ..core.article import (
    MANUAL_SETTABLE,
    allowed_targets,
    read_status,
    set_status,
)


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--set",
    "set_to",
    default=None,
    type=click.Choice(MANUAL_SETTABLE),
    help="人工迁移状态（published 只由 publish 成功自动置位）",
)
def status(article: str, set_to: str | None) -> None:
    """查看/迁移文章状态（draft/reviewed/published/archived）。"""
    if set_to is None:
        cur = read_status(article)
        click.echo(f"当前状态: {cur}")
        click.echo(f"可迁移到: {'、'.join(allowed_targets(cur)) or '（无）'}")
        click.echo("迁移: musespiral status <article> --set reviewed|draft|archived")
        return
    try:
        old = set_status(article, set_to)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 状态已迁移: {old} → {set_to}")
