"""发布执行命令组：publish / submit / stats。

R1 自 cli.py 拆分（正典 §四）；函数体逐字节保留，仅 @cli→@click 装饰器与
函数内相对导入层级（from . → from ..）两类机械调整。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ..core.content.parser import parse_article
from ..core.quality.checks import format_report, has_failures, run_checks
from ..core.quality.render_checks import render_checks
from ._shared import _PLATFORM_NAMES, _build_renderer


# ── publish ───────────────────────────────────────────────
@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--account", default=None, help="公众号账号名（使用默认账号可不填）")
@click.option("--skip-check", is_flag=True, help="跳过审核检查（不推荐）")
def publish(article: str, account: str | None, skip_check: bool) -> None:
    """上传图片并创建草稿（默认仅建草稿，不自动发表）。"""
    from ..platforms.wechat import publish_flow
    from ..infra.wechat.token import TokenError

    path = Path(article)
    parsed = parse_article(path)

    # 1. Checklist gate（frontmatter 级先跑——不依赖渲染，坏主题也能先拿到检查报告）
    results = [] if skip_check else run_checks(parsed, path)

    # 2. Render（渲染级检查与上传共用；渲染失败转检查报告而非堆栈，与 check --render 同口径）
    try:
        html = _build_renderer(parsed).render(parsed)
    except (FileNotFoundError, ValueError) as e:
        if results:
            click.echo(format_report(results))
        click.echo(f"❌ 渲染失败: {e}（请检查 frontmatter.theme）", err=True)
        sys.exit(1)
    if not skip_check:
        results += render_checks(html, parsed, path)
        click.echo(format_report(results))
        if has_failures(results):
            click.echo("\n❌ 审核未通过，请修正后再发布。", err=True)
            sys.exit(1)

    # 3-6. 发布编排（封面/正文图容错上传 + digest 兜底 + 建草稿记日志）。
    #      单一实现在 platforms.wechat.publish_flow——CLI 与适配器共用，防双份漂移
    try:
        media_id = publish_flow(parsed, path, html, account=account, echo=click.echo)
    except (TokenError, ValueError) as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)

    # C1（第十四期顺手批）：publish 语义=建草稿——成功后 draft→reviewed 自动
    # 置位（原置 published 与状态机冲突：draft→published 非法迁移恒 WARN）；
    # 置位失败只告警，不阻塞草稿成功提示
    from ..services.publish_service import auto_mark_reviewed

    try:
        auto_mark_reviewed(path)
    except (ValueError, OSError) as e:
        click.echo(f"⚠️ 状态置位失败（草稿已创建，不影响）: {e}", err=True)

    click.echo(f"\n✅ 草稿创建成功！")
    click.echo(f"   draft_media_id: {media_id}")
    click.echo(f"   请到公众号后台草稿箱查看并手动发表")
    click.echo(f"   或运行: musespiral submit {article} --draft-id {media_id}")


# ── submit ────────────────────────────────────────────────
@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--draft-id", required=True, help="草稿 media_id")
def submit(article: str, draft_id: str) -> None:
    """提交草稿发表（freepublish，需二次确认）。"""
    from ..infra.wechat.token import get_access_token
    from ..infra.wechat.publisher import check_publish_status, submit_publish
    from ..infra.storage.jsonl import default_log_path, set_publish_url

    if not click.confirm("确认提交发表？发表后不可撤回。"):
        click.echo("已取消")
        return

    path = Path(article)
    title = str(parse_article(path).metadata.get("title", ""))
    token = get_access_token()
    click.echo("提交发表...")
    publish_id = submit_publish(token, draft_id, article=str(path), title=title)
    click.echo(f"publish_id: {publish_id}，正在等待审核结果...")

    result = check_publish_status(token, publish_id)
    article_url = result.get("article_detail", {}).get("item", [{}])[0].get("article_url", "")
    click.echo(f"✅ 发表成功!")
    if article_url:
        click.echo(f"   文章链接: {article_url}")
        set_publish_url(default_log_path(), publish_id, article_url)


# ── stats ─────────────────────────────────────────────────
@click.command()
@click.option("--sync", is_flag=True, help="先调微信图文统计接口回填阅读/分享数（需认证服务号）")
def stats(sync: bool) -> None:
    """汇总发布日志（累计/本月草稿与发表数、最近条目）。"""
    # 第十四期集成：读侧统一走 publish_log_service——双日志合并读序（root 优先、
    # home 补充、去重）+ demo_ 前缀 fixture 行判除（正典 §1.2「周历与统计均排除」）
    from ..services import publish_log_service
    from ..services.discover_service import DEFAULT_ARTICLES_ROOT

    root = DEFAULT_ARTICLES_ROOT
    s = publish_log_service.summarize(root)
    if not s["recent"]:
        click.echo("暂无发布记录。创建草稿（musespiral publish）或发表（musespiral submit）后会自动记录。")
        return

    if sync:
        from ..infra.storage.jsonl import default_log_path
        from ..infra.wechat.analytics import AnalyticsError, sync_reads

        try:
            done = sync_reads(default_log_path())
        except AnalyticsError as e:
            click.echo(f"❌ 数据回填失败: {e}", err=True)
            sys.exit(1)
        click.echo(
            f"数据回填: 候选 {done['candidates']} 条 / URL 命中 {done['matched']} 条 / "
            f"更新 {done['updated']} 行（{done['windows']} 个时间窗，取回 {done['fetched']} 条统计）"
        )
        s = publish_log_service.summarize(root)  # 回填后重读，reads/shares 反映本次更新
    click.echo(
        f"累计: 草稿 {s['total_draft']} 篇 / 发表 {s['total_publish']} 篇 / "
        f"阅读 {s['total_reads']} / 分享 {s['total_shares']}"
    )
    click.echo(f"本月: 草稿 {s['month_draft']} 篇 / 发表 {s['month_publish']} 篇")

    # F13 多平台化：按 platform 字段分组（旧日志行缺 platform 读作 mp）
    by_platform = s.get("by_platform", {})
    if by_platform:
        click.echo("\n按平台:")
        for p, g in by_platform.items():
            name = _PLATFORM_NAMES.get(p, p)
            click.echo(
                f"  {name}（{p}）: 草稿 {g['drafts']} / 发表 {g['publishes']}"
                f"（本月 草稿 {g['month_drafts']} / 发表 {g['month_publishes']}）"
            )
            # 每平台最近至多 3 条（第五期遗留 low 修复：分组 recent 此前未展示）
            for r in g.get("recent", [])[:3]:
                action = "发表" if r.get("action") == "publish" else "草稿"
                click.echo(f"      · {r.get('ts', '?')} [{action}] {r.get('title', '')}")

    recent = s["recent"]
    if recent:
        click.echo(f"\n最近 {len(recent)} 条:")
        for r in recent:
            action = "发表" if r.get("action") == "publish" else "草稿"
            line = f"  {r.get('ts', '?')} [{action}] {r.get('title', '')}（{r.get('article', '?')}）"
            if r.get("article_url"):
                line += f" {r['article_url']}"
            click.echo(line)
