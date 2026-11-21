"""backup 命令（F35 文章库备份——独立先行切片，差距分析 §四）。

一条命令打包 articles root + ~/.musespiral（除 cookies/）为带时间戳 tar.gz
并轮转。恢复为手工解包（tar 内 articles/ 与 musespiral-home/ 两顶层目录）。
"""
from __future__ import annotations

import sys

import click

from ..services import backup_service


@click.command("backup")
@click.option(
    "--output",
    "output_dir",
    default=None,
    type=click.Path(file_okay=False),
    help="备份目录（默认 ~/musespiral-backups/）",
)
@click.option(
    "--keep",
    default=5,
    show_default=True,
    type=click.IntRange(min=1),
    help="轮转保留份数（最旧的超额份自动删除）",
)
def backup(output_dir: str | None, keep: int) -> None:
    """打包文章库与 ~/.musespiral（除 cookies/）为 tar.gz 并轮转（F35）。"""
    try:
        dst = backup_service.create_backup(output_dir=output_dir, keep=keep)
    except Exception as e:  # 盘满/权限等——本地可读错误，退出码非零
        click.echo(f"❌ 备份失败：{e}", err=True)
        sys.exit(1)
    size_mb = dst.stat().st_size / 1_048_576
    click.echo(f"✅ 备份完成：{dst}（{size_mb:.1f} MB）")
