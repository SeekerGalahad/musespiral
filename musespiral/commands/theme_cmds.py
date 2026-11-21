"""主题命令组：theme（list / new / learn）。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .. import config


# ── theme ─────────────────────────────────────────────────
@click.group()
def theme() -> None:
    """主题管理。"""


@theme.command("list")
def theme_list() -> None:
    """列出全部主题（内置 ∪ 用户目录，标注来源）。"""
    from ..core.content.themes import list_all

    items = list_all()
    if not items:
        click.echo("暂无主题")
        return
    for name, origin in items:
        click.echo(f"  - {name} [{origin}]")


@theme.command("new")
@click.argument("name")
@click.option("--base", default=config.DEFAULT_THEME, help="继承的基座主题（默认 lapis）")
def theme_new(name: str, base: str) -> None:
    """生成用户主题骨架（~/.musespiral/themes/<name>.yaml，含 extends 继承）。"""
    from ..core.content.themes import new_skeleton

    try:
        target = new_skeleton(name, base, config.home_dir() / "themes")
    except (FileExistsError, FileNotFoundError, ValueError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已生成主题骨架: {target}")
    click.echo(f"   编辑覆盖项（colors/typography/components 与基座深合并）")
    click.echo(f"   musespiral theme list 可见 [用户] 标注，musespiral preview 试看效果")


@theme.command("learn")
@click.argument("url", required=False)
@click.option("--html", "html_file", default=None, type=click.Path(exists=True, dir_okay=False), help="本地公众号文章 HTML 文件（离线路径，优先于 url）")
@click.option("--name", default="learned", help="生成的主题名（默认 learned）")
@click.option("--out", "-o", default=None, type=click.Path(), help="输出目录（默认 ~/.musespiral/themes/）")
def theme_learn(url: str | None, html_file: str | None, name: str, out: str | None) -> None:
    """从公众号文章 HTML 学习配色/字号生成主题（频次归一 + extends: default）。"""
    import requests

    from ..core.content.theme_learn import learn_from_html

    if html_file:
        html_text = Path(html_file).read_text(encoding="utf-8")
        click.echo(f"来源: 本地文件 {html_file}")
    elif url:
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            click.echo(f"❌ 拉取文章 HTML 失败: {e}", err=True)
            sys.exit(1)
        html_text = resp.text
        click.echo(f"来源: {url}")
    else:
        click.echo("❌ 需要指定文章 URL，或用 --html <本地文件> 走离线路径", err=True)
        sys.exit(1)

    try:
        target = learn_from_html(html_text, name, out_dir=out)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已学习生成主题: {target}")
    click.echo(f"   musespiral theme list 可见；musespiral generate <文章> --theme {name} 试渲染")
