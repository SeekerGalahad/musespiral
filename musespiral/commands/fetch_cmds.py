"""文章抓取命令（R6，重构正典 §四-R6）：musespiral fetch <url> [--html <file>] [-o out.md]。

--html 本地文件路径优先（离线解析，不联网——测试与冒烟走此路；url 参数
此时仅占位）。与 scaffold_article 同纪律：拒绝覆盖已存在的输出文件。
本文件由集成阶段在 cli.py 统一注册（任务边界：只建命令文件，不动 cli.py）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click
import frontmatter

from ..infra.sources.url_fetch import FetchError, fetch_article


@click.command()
@click.argument("url")
@click.option(
    "--html",
    "html_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="本地 HTML 文件路径（离线解析优先，不联网；测试/冒烟用）",
)
@click.option("--out", "-o", required=True, type=click.Path(), help="输出的文章草稿路径")
def fetch(url: str, html_path: str | None, out: str) -> None:
    """抓取公众号文章 URL 转 Markdown 草稿（失效链接给可读报错）。"""
    try:
        if html_path:
            post = fetch_article(Path(html_path).read_text(encoding="utf-8"), is_html=True)
        else:
            post = fetch_article(url)
    except FetchError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    dst = Path(out)
    if dst.exists():
        click.echo(f"❌ 目标文件已存在，拒绝覆盖: {dst}", err=True)
        sys.exit(1)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(frontmatter.dumps(post, allow_unicode=True, sort_keys=False), encoding="utf-8")

    click.echo(f"✅ 已生成抓取草稿: {dst}")
    click.echo(f"   标题: {post.metadata['title']}")
    if html_path:
        click.echo("   （离线导入：source_url 留空）")
    else:
        click.echo(f"   来源: {post.metadata['source_url']}")
        click.echo("   ⚠️ 在线抓取请保持低频（微信有频控验证）；批量文章建议浏览器另存 HTML 后走 --html 离线路径")
    click.echo("   ⚠️ 封面未抓取（cover 留空），请补充后再 check")
    click.echo(f"   下一步: 编辑补充 cover/digest → musespiral check {dst} → musespiral preview {dst}")
