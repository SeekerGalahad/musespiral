"""排期与登记命令组（第十四期 F36/F36c，正典 §三-3.1）：plan / log add。

同一数据面归一组（周历读侧 + 人工发布登记写侧），命令层只做参数解析与
用户提示，编排全在 services/plan_service + services/publish_log_service。
本期**只定义不注册**——`musespiral plan` / `musespiral log` 挂入 cli 主组的注册与
快照基线扩展统一归集成阶段（两组并行禁碰 cli.py）。
"""
from __future__ import annotations

import sys

import click

from ..services import plan_service


@click.command()
@click.option("--week", default=None, metavar="YYYY-Www", help="回看指定 ISO 周（如 2026-W47）；缺省本周")
def plan(week: str | None) -> None:
    """排期周历（F36）：计划/已交付/已发布三态文字视图，只读不改。"""
    try:
        click.echo(plan_service.render_week(week=week))
    except ValueError as e:  # 周序非法 → 用法错误（exit 2），不当堆栈
        raise click.BadParameter(str(e), param_hint="'--week'") from e


@click.group()
def log() -> None:
    """发布日志人工登记（F36c：mp 终发是人工后台点击，登记后周历归位）。"""


@log.command("add")
@click.argument("article")
@click.option("--platform", "platform_code", required=True, help="平台代码（mp/xhs/zhihu/…）")
@click.option("--title", default=None, help="覆盖标题（缺省读文章 frontmatter.title）")
@click.option("--url", default=None, help="文章链接（可选，回填进登记行）")
def log_add(article: str, platform_code: str, title: str | None, url: str | None) -> None:
    """人工登记一次发表：追加 manual:true 的 publish 行（ts=now）。

    ARTICLE 为 articles root 内相对路径（或 root 内绝对路径），须存在。
    """
    codes = sorted(plan_service.known_platform_codes())
    if platform_code not in codes:
        raise click.BadParameter(
            f"未知平台代码 {platform_code!r}（可选: {'/'.join(codes)}）",
            param_hint="'--platform'",
        )
    try:
        row = plan_service.register_manual_publish(
            article, platform_code, title=title, url=url
        )
    except ValueError as e:  # 文章不存在 / 不在 root 内 → 可读错误，退出码非零
        click.echo(f"❌ 登记失败: {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 已登记发表: {row['platform']} {row['title']}（{row['article']}）")
    if url:
        click.echo(f"   链接: {url}")
    click.echo("   musespiral plan 的「已发布」已即时归位")
