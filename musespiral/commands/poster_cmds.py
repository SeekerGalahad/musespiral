"""F23 poster 命令（§十八）——CLI 注册归集成阶段（F24），本模块建好不接线。

musespiral poster <article> [--html-only] [-o out.png]
降级策略同 cards：先探浏览器可用性再落盘——未装 playwright 且未给
--html-only 时给可读错误并提示两条出路（安装 / --html-only），不落半成品。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--html-only", is_flag=True, help="不截图，只产出长图 HTML（无需浏览器）")
@click.option("--out", "-o", default=None, type=click.Path(), help="输出路径（默认 <stem>.poster.png；--html-only 时 <stem>.poster.html）")
def poster(article: str, html_only: bool, out: str | None) -> None:
    """生成长图：文章 → 1080 宽竖版长图（HTML 模板 + 浏览器全页截图）。"""
    from ..core.visual import poster as poster_mod
    from ..browser import BrowserNotInstalled

    src = Path(article)
    # 输出存在即拒绝覆盖（与 adapt/pack/cards 同纪律；默认路径与实际写盘点同口径）
    if html_only:
        html_target = Path(out) if out else src.with_name(f"{src.stem}.poster.html")
        if html_target.exists():
            click.echo(f"❌ 目标文件已存在，拒绝覆盖: {html_target}", err=True)
            sys.exit(1)
    else:
        png_target = Path(out) if out else src.with_name(f"{src.stem}.poster.png")
        if png_target.exists():
            click.echo(f"❌ 目标文件已存在，拒绝覆盖: {png_target}", err=True)
            sys.exit(1)

    if not html_only:
        # 先探浏览器可用性再落盘：未装 playwright 不留半成品文件卡住重跑
        try:
            poster_mod.require_browser()
        except BrowserNotInstalled as e:
            click.echo(f"❌ {e}", err=True)
            click.echo(
                "两条出路：安装 playwright 后重跑，或加 --html-only 只产长图 HTML",
                err=True,
            )
            sys.exit(1)

    html = poster_mod.build_poster_html(src)
    if html_only:
        target = Path(out) if out else src.with_name(f"{src.stem}.poster.html")
        target.write_text(html, encoding="utf-8")
        click.echo(f"✅ 长图 HTML 已生成: {target}")
        return

    html_path = src.with_name(f"{src.stem}.poster.html")
    html_path.write_text(html, encoding="utf-8")
    png = poster_mod.shoot(html_path, Path(out) if out else src.with_name(f"{src.stem}.poster.png"))
    click.echo(f"✅ 长图 PNG 已生成: {png}（HTML 源同目录保留）")
