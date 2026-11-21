"""F31 cover 命令（§二 F31）——CLI 注册见 cli.py（本期集成）。

musespiral cover ARTICLE [--title-card | --from-cards | --from-poster | --from-image <path>]
                    [--platform mp|xhs|channels|douyin] [--no-write-back]

四路线互斥（缺省 --title-card，多给报错）；title-card 无浏览器环境自动走
Pillow 兜底直绘（不报错、封面必有产出）；落盘 cover.png（默认/mp，写回
frontmatter cover 带 .md.bak.<时间戳> 滚动版本池备份）或 cover.<platform>.png（非 mp
只落图不写回——平台封面由 pack/check --platform 探测优先级发现）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ..services.cover_service import platform_choices


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--title-card", "title_card", is_flag=True,
              help="标题字卡封面（默认；标题+一句钩子，无浏览器时 Pillow 兜底直绘）")
@click.option("--from-cards", "from_cards", is_flag=True,
              help="取 .cards/ 首卡中心裁目标规格（需先 musespiral cards）")
@click.option("--from-poster", "from_poster", is_flag=True,
              help="取 <stem>.poster.png 头区裁目标规格（需先 musespiral poster）")
@click.option("--from-image", "from_image", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="用户源图 PIL 中心裁+缩放至目标规格（--from-image <path> 即选路线又给源图）")
@click.option("--from-design", "from_design", is_flag=True,
              help="读 <stem>.design/cover.svg → B 路光栅 → 规格（F71 设计稿桥；需先 musespiral design save）")
@click.option("--platform", default=None, type=click.Choice(platform_choices()),
              help="目标平台规格（默认 mp 落 cover.png；其余落 cover.<platform>.png，规格以 platform list 为准）")
@click.option("--no-write-back", is_flag=True, help="只落封面图，不写回 frontmatter（也不入 .md.bak.<时间戳> 滚动版本池）")
def cover(article: str, title_card: bool, from_cards: bool, from_poster: bool,
          from_image: str | None, from_design: bool, platform: str | None,
          no_write_back: bool) -> None:
    """生成封面：五路线派生目标平台规格 PNG（仅产出本地文件）。"""
    from ..services import cover_service

    # 旗标 → 路线名（--from-image 带路径即选中该路线）；互斥，多给报错
    picks = [
        ("--title-card", bool(title_card)),
        ("--from-cards", bool(from_cards)),
        ("--from-poster", bool(from_poster)),
        ("--from-image", from_image is not None),
        ("--from-design", bool(from_design)),
    ]
    given = [flag for flag, on in picks if on]
    if len(given) > 1:
        click.echo(f"❌ 路线互斥，只能给一个: {'/'.join(given)}（缺省 --title-card）", err=True)
        sys.exit(1)
    route = given[0].lstrip("-") if given else "title-card"

    try:
        out = cover_service.generate_cover(
            Path(article),
            route=route,
            platform=platform,
            image=from_image,
            write_back=not no_write_back,
        )
    except (ValueError, FileNotFoundError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    click.echo(f"✅ 封面已生成: {out}")
    if no_write_back:
        click.echo("   --no-write-back：仅落图，未写回 frontmatter")
    elif platform not in (None, "mp"):
        click.echo(
            f"   非mp封面只落图：frontmatter cover 不动（pack 探测优先 "
            f"cover.{platform}.png > cover.png/frontmatter 指向）"
        )
    elif route == "from-design":
        click.echo("   设计稿桥（from-design）：cover.svg → B 路光栅（无浏览器降 C）；frontmatter cover 已写回")
    else:
        click.echo(f"   frontmatter cover 已写回（旧版入滚动版本池 {Path(article).name}.bak.<时间戳>，musespiral restore 可查看/恢复）")
        click.echo(f"   下一步: musespiral check {article}（封面项应 PASS）后进入 pack/publish")
