"""site 命令组（G-T6 静态站导出，正典=《musespiral-调研吸收功能设计-2026-11-18》§七）。

`musespiral site build --output <dir>`：只导 publish_log 已发布正稿 → 纸墨 HTML +
index.html + feed.xml，零 JS 纯静态。CLI-only，无 Studio 端点。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ..services import site_service


@click.group()
def site() -> None:
    """已发布文章 → 零 JS 纯静态站（HTML + index + RSS，内容自主权）。"""


@site.command("build")
@click.option(
    "--output",
    "output_dir",
    required=True,
    type=click.Path(file_okay=False),
    help="站点输出目录（不存在则自建，同名产物覆盖）",
)
@click.option("--root", default=None, type=click.Path(exists=True, file_okay=False),
              help="文章目录根（默认 ~/musespiral-articles）")
@click.option("--title", default=site_service.DEFAULT_SITE_TITLE, show_default=True,
              help="站点标题")
@click.option("--site-link", default="", help="站点绝对 URL（仅写入 RSS 链接，可空）")
def site_build(output_dir: str, root: str | None, title: str, site_link: str) -> None:
    """扫描已发布正稿 → 纸墨 HTML 站点（草稿不导出，零外联 JS）。"""
    try:
        result = site_service.build(
            output_dir, root=Path(root) if root else None,
            site_title=title, site_link=site_link,
        )
    except (RuntimeError, OSError, ValueError) as e:
        click.echo(f"❌ 静态站构建失败：{e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 静态站构建完成：{result.output_dir}")
    click.echo(f"   已发布 {result.published_total} 篇 / 写出 {len(result.written)} 篇 HTML")
    if result.skipped:
        click.echo(f"   ⚠️ 跳过 {len(result.skipped)} 篇：", err=True)
        for s in result.skipped:
            click.echo(f"      · {s}", err=True)
    click.echo(f"   目录：index.html（列表）· feed.xml（RSS）")
