"""F22 cards 命令（§十八）——CLI 注册归集成阶段（F24），本模块建好不接线。

musespiral cards <article> [--variant <xhs 变体>] [--ratio 3:4|4:5] [--html-only]
                      [-o outdir]
默认尝试截图（需 musespiral[browser]）；未装 playwright 且未给 --html-only 时
给可读错误并提示两条出路（安装 / --html-only），不自动降级、不落半成品目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--variant", default=None, type=click.Path(), help="adapt xhs 变体路径（切卡走变体路径；不存在即报错，拒绝静默回退）")
@click.option("--ratio", default="3:4", type=click.Choice(["3:4", "4:5"]), help="卡规格（默认 3:4 即 1242×1656）")
@click.option("--html-only", is_flag=True, help="不截图，只产出卡组单文件 HTML（无需浏览器）")
@click.option("--outdir", "-o", default=None, type=click.Path(), help="输出目录（默认 <stem>.cards/，存在即拒绝覆盖）")
def cards(article: str, variant: str | None, ratio: str, html_only: bool, outdir: str | None) -> None:
    """生成小红书图文卡组：封面卡 + 内容卡 N + 结尾卡（仅产出本地文件）。"""
    from ..core.visual.cards import compose, raster
    from ..browser import BrowserNotInstalled

    src = Path(article)
    out_dir = Path(outdir) if outdir else src.parent / f"{src.stem}.cards"

    try:
        html, n = compose.build_cards_html(src, variant=variant, ratio=ratio)
    except (ValueError, FileNotFoundError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    if out_dir.exists():
        click.echo(f"❌ 输出目录已存在，拒绝覆盖: {out_dir}", err=True)
        sys.exit(1)

    if not html_only:
        # 先探浏览器可用性再落盘：未装 playwright 不留半成品目录卡住重跑
        try:
            raster.require_browser()
        except BrowserNotInstalled as e:
            click.echo(f"❌ {e}", err=True)
            click.echo(
                "两条出路：安装 playwright 后重跑，或加 --html-only 只产出卡组 HTML",
                err=True,
            )
            sys.exit(1)

    out_dir.mkdir(parents=True)
    html_path = out_dir / f"{src.stem}.html"
    html_path.write_text(html, encoding="utf-8")
    if html_only:
        click.echo(f"✅ 卡组 HTML 已生成: {html_path}（{n} 卡）")
        return

    pngs = raster.shoot(html_path, out_dir)
    click.echo(f"✅ 图文卡已生成: {out_dir}（{len(pngs)} 张 PNG，HTML 源同目录保留）")
