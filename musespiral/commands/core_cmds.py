"""核心内容命令组：generate / preview / check / review / digest / title。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
F34（第十三期）：check / generate / digest 的 ARTICLE 改 nargs=-1——单文件
输出与旧版逐字节一致（run_batch 单篇直跑零批量痕迹），多文件逐篇不短路、
收尾追加汇总表、任一 FAIL 聚合 exit 1。
F32（第十三期）：title 命令（与 digest 同范式）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from .. import config
from ..core.content.parser import parse_article
from ..core.quality.checks import format_report, has_failures, run_checks
from ..core.quality.render_checks import render_checks
from ..infra.storage.filesystem import backup_article
from ..preview import start_preview
from ..core.content.renderer import Renderer
from ._shared import _build_renderer, run_batch


# ── generate ──────────────────────────────────────────────
@click.command()
@click.argument("articles", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--theme", default=None, help="覆盖主题（lapis/default/phycat）")
@click.option("--code-theme", default=None, help="代码高亮主题（Pygments 样式）")
@click.option("--dark", is_flag=True, help="使用暗色配色")
def generate(articles: tuple[str, ...], theme: str | None, code_theme: str | None, dark: bool) -> None:
    """解析 Markdown 并生成本地 HTML（不调用任何 API）。"""
    sys.exit(run_batch("generate", articles, lambda a: _generate_one(a, theme, code_theme, dark)))


def _generate_one(article: str, theme: str | None, code_theme: str | None, dark: bool) -> int:
    path = Path(article)
    parsed = parse_article(path)

    t = theme or parsed.metadata.get("theme") or config.DEFAULT_THEME
    ct = code_theme or parsed.metadata.get("code_theme") or config.DEFAULT_CODE_THEME
    use_dark = dark or bool(parsed.metadata.get("dark", False))
    renderer = Renderer(theme_name=t, code_theme=ct, dark=use_dark)
    html = renderer.render(parsed)

    out_dir = path.parent / f"{path.stem}.musespiral"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "rendered.html"
    out_file.write_text(html, encoding="utf-8")

    click.echo(f"✅ 已生成: {out_file}")
    click.echo(f"   主题: {t}{' (暗色)' if use_dark else ''} | 代码主题: {ct}")
    click.echo(f"   下一步: musespiral preview {article}  或  musespiral check {article}")
    return 0


# ── preview ───────────────────────────────────────────────
@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--port", default=8765, type=int, help="预览端口")
@click.option("--dark", is_flag=True, help="暗色预览")
def preview(article: str, port: int, dark: bool) -> None:
    """启动本地 HTTP 服务器，在浏览器预览渲染效果。"""
    start_preview(article, port=port, dark=dark)


# ── check ─────────────────────────────────────────────────
@click.command()
@click.argument("articles", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--render", is_flag=True, help="追加渲染级自动验收（外链残留/图片外域/封面比例/非法标签）")
@click.option("--platform", "platform_code", default=None, help="按平台规格预检（字限/图数/封面三项只对该平台生效）")
def check(articles: tuple[str, ...], render: bool, platform_code: str | None) -> None:
    """运行发布前审核清单（--render 追加渲染级自动验收）。"""
    if platform_code:
        from ..platforms import profiles

        try:
            profiles.get_limits(platform_code)
        except ValueError as e:
            raise click.BadParameter(str(e), param_hint="'--platform'") from None
    sys.exit(run_batch("check", articles, lambda a: _check_one(a, render, platform_code)))


def _check_one(article: str, render: bool, platform_code: str | None) -> int:
    parsed = parse_article(article)
    results = run_checks(parsed, article, platform=platform_code)
    if render:
        try:
            html = _build_renderer(parsed).render(parsed)
        except (FileNotFoundError, ValueError) as e:
            # 主题不存在/extends 非法等渲染失败 → 以检查报告形式呈现，而非堆栈
            results.append(("FAIL", f"渲染失败: {e}"))
        else:
            results += render_checks(html, parsed, article)
    click.echo(format_report(results))
    return 1 if has_failures(results) else 0


# ── review（F8 对照审阅）───────────────────────────────────
@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--note", required=True, type=click.Path(exists=True, dir_okay=False), help="原笔记路径（对照基准）")
@click.option("--out", "-o", default=None, type=click.Path(), help="输出 HTML 路径（默认 <article>.review.html）")
def review(article: str, note: str, out: str | None) -> None:
    """生成原笔记 ↔ 文章对照审阅 HTML（纯本地生成，不调 AI）。"""
    from ..core.quality.review import build_review_html

    page = build_review_html(note, article)
    src = Path(article)
    out_file = Path(out) if out else src.with_name(f"{src.stem}.review.html")
    out_file.write_text(page, encoding="utf-8")
    click.echo(f"✅ 已生成对照审阅页: {out_file}")
    click.echo("   浏览器核对删改痕迹后，再进入 check / preview / publish")


# ── digest（F10 备选摘要）──────────────────────────────────
@click.command()
@click.argument("articles", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--apply", "apply_n", default=None, type=click.IntRange(1, 3), help="采用第 N 条候选写回 frontmatter.digest")
def digest(articles: tuple[str, ...], apply_n: int | None) -> None:
    """生成备选摘要候选（AI 优先，未配置回退启发式两条）。"""
    sys.exit(run_batch("digest", articles, lambda a: _digest_one(a, apply_n)))


def _digest_one(article: str, apply_n: int | None) -> int:
    import frontmatter

    from ..core.quality.digest import candidates

    items = candidates(article)
    if not items:
        click.echo("正文为空，无法生成摘要候选")
        return 1
    click.echo(f"共 {len(items)} 条备选摘要:")
    for i, s in enumerate(items, 1):
        click.echo(f"  {i}. {s}")
    if apply_n is None:
        click.echo("\n选用某条写回: musespiral digest <article> --apply 1|2|3")
        return 0
    if apply_n > len(items):
        click.echo(f"❌ 只有 {len(items)} 条候选，--apply {apply_n} 超出范围", err=True)
        return 1
    picked = items[apply_n - 1]
    post = frontmatter.load(Path(article))
    post.metadata["digest"] = picked
    with open(article, "w", encoding="utf-8") as f:
        frontmatter.dump(post, f)
    click.echo(
        f"\n✅ 已写回第 {apply_n} 条为 digest（正文不动；frontmatter 整体重写，"
        "键序可能重排、YAML 注释会丢失）"
    )
    return 0


# ── title（F32 标题多候选优选）─────────────────────────────
@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--platform", "platform_code", default=None, help="按平台字限过滤候选（mp/xhs/channels/douyin/zhihu）")
@click.option("--apply", "apply_n", default=None, type=int, help="采用第 N 条候选写回 frontmatter.title（旧版入 .md.bak.<时间戳> 滚动版本池，restore 可恢复）")
def title(article: str, platform_code: str | None, apply_n: int | None) -> None:
    """生成备选标题候选（AI 优先，未配置回退启发式；--platform 按字限过滤）。"""
    import frontmatter

    from ..core.quality.title import annotate, candidates, describe_limit

    try:
        items = candidates(article, platform=platform_code)
    except ValueError as e:  # 未知平台（profiles.get_limits）
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    if not items:
        click.echo("正文为空，无法生成标题候选")
        sys.exit(1)
    hint = f"（--platform {platform_code}，{describe_limit(platform_code)}）" if platform_code else ""
    click.echo(f"共 {len(items)} 条标题候选{hint}:")
    for i, c in enumerate(items, 1):
        click.echo(f"  {i}. {c.text}（{annotate(c, platform_code)}，{c.source}）")
    if apply_n is None:
        click.echo("\n选用某条写回: musespiral title <article> --apply N")
        return
    if apply_n < 1 or apply_n > len(items):
        click.echo(f"❌ 只有 {len(items)} 条候选，--apply {apply_n} 超出范围", err=True)
        sys.exit(1)
    picked = items[apply_n - 1].text
    path = Path(article)
    bak = backup_article(path)  # F43 滚动版本池（三处写回共用 helper）
    post = frontmatter.load(path)
    post.metadata["title"] = picked
    with open(article, "w", encoding="utf-8") as f:
        frontmatter.dump(post, f)
    click.echo(
        f"\n✅ 已写回第 {apply_n} 条为 title（原文件已入版本池 {bak.name}；"
        "musespiral restore <article> 可查看/恢复；frontmatter 整体重写，键序可能重排、"
        "YAML 注释会丢失）"
    )
