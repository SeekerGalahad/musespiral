"""多平台命令组：platform（能力矩阵）/ adapt / pack / upload（浏览器上传）。

R1 自 cli.py 拆分（正典 §四）；Phase A 起接 services 层：adapt 走
adapt_service.generate_variant、pack 走 pack_service.build_pack_for（limits
由服务层注入），本层只做参数解析与用户提示。
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import click

from ..core.content.parser import parse_article
from ._shared import _PLATFORM_NAMES, run_batch


# ── platform / adapt / pack（第五期多平台发布层，§十四）────

# 路线分档（§14.0）→ 表格展示名
_ROUTE_LABELS = {"official_api": "官方API", "manual_pack": "物料包", "browser": "浏览器"}

# 能力矩阵「限制摘要」列：profiles 限制键 → 摘要片段（按序拼接，缺哪键跳过哪项）
_LIMIT_SUMMARY_KEYS: tuple[tuple[str, str], ...] = (
    ("title_max_bytes", "标题≤{v}字节"),
    ("digest_max_bytes", "摘要≤{v}字节"),
    ("title_max_chars", "标题≤{v}字"),
    ("content_max_chars", "正文≤{v}字"),
    ("desc_max_chars", "描述≤{v}字"),
    ("tags_max", "话题≤{v}个"),
    ("cover_ratio", "封面{v}:1"),
)


def _cjk_pad(text: str, width: int) -> str:
    """按显示宽度右补空格（中日韩宽字符计 2 列），用于能力矩阵表对齐。"""
    display = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
    return text + " " * max(0, width - display)


def _limits_summary(limits: dict) -> str:
    """把 profiles 限制表压缩为摘要片段（能力矩阵表最后一列）。"""
    parts = [tpl.format(v=limits[k]) for k, tpl in _LIMIT_SUMMARY_KEYS if k in limits]
    return " / ".join(parts) or "—"


@click.group()
def platform() -> None:
    """多平台发布层：平台能力矩阵。"""


@platform.command("list")
def platform_list() -> None:
    """列出已注册平台的能力矩阵（平台/代码/通道/能力/限制摘要）。"""
    from ..platforms import profiles, registry

    adapters = registry.list_platforms()
    if not adapters:
        click.echo("暂无已注册平台")
        return
    header = (
        _cjk_pad("平台", 14) + _cjk_pad("代码", 8) + _cjk_pad("通道", 12)
        + _cjk_pad("能力", 28) + "限制摘要"
    )
    click.echo(header)
    for a in adapters:
        caps = "/".join(sorted(a.capabilities)) or "—"
        click.echo(
            _cjk_pad(_PLATFORM_NAMES.get(a.name, a.name), 14)
            + _cjk_pad(a.name, 8)
            + _cjk_pad(_ROUTE_LABELS.get(a.route, a.route), 12)
            + _cjk_pad(caps, 28)
            + _limits_summary(profiles.get_limits(a.name))
        )


@click.command()
@click.argument("articles", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--platform", "platform_code", required=True, help="目标平台代码（xhs/channels/douyin/zhihu）")
@click.option("--out", "-o", default=None, type=click.Path(), help="输出路径（默认 <stem>.<platform>.md，存在即拒绝覆盖；仅单文件调用合法）")
def adapt(articles: tuple[str, ...], platform_code: str, out: str | None) -> None:
    """一稿多态：把文章改写成指定平台的文字变体（仅产出本地文件）。"""
    if out and len(articles) > 1:
        # C2：单值 --out 对多文件无法逐篇对应——前置显式报错（与 --variant 同语义守门）
        raise click.UsageError(
            "--out 仅支持单文件调用；多文件批量输出为逐篇默认 <stem>.<platform>.md"
            "（--out 逐篇指定能力后置）"
        )
    sys.exit(run_batch("adapt", articles, lambda a: _adapt_one(a, platform_code, out)))


def _adapt_one(article: str, platform_code: str, out: str | None) -> int:
    from ..core.adaptation import AdaptNeedsAI
    from ..infra.ai import AIError
    from ..services import adapt_service

    try:
        dst = adapt_service.generate_variant(article, platform_code, out_path=out)
    except AdaptNeedsAI as e:
        click.echo(f"❌ {e}", err=True)
        return 1
    except (ValueError, FileExistsError, AIError) as e:
        click.echo(f"❌ {e}", err=True)
        return 1
    name = _PLATFORM_NAMES.get(platform_code, platform_code)
    click.echo(f"✅ 已生成{name}变体: {dst}")
    warnings = parse_article(dst).metadata.get("adapt_warnings") or []
    for w in warnings:
        click.echo(f"   ⚠️ 本地校验: {w}")
    click.echo("   ⚠️ 仅产出本地文件，人审后才可进入 pack / publish")
    return 0


@click.command()
@click.argument("articles", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--platform", "platform_code", required=True, help="目标平台代码（channels/xhs/zhihu/douyin）")
@click.option("--variant", default=None, type=click.Path(), help="adapt 产物路径（payload 优先取其内容，缺失回退原文；仅单文件调用合法）")
@click.option("--outdir", "-o", default=None, type=click.Path(), help="物料包目录（默认 <stem>.<platform>.pack/，存在即拒绝覆盖；仅单文件调用合法）")
@click.option("--force", is_flag=True, help="已交付平台也强制重做物料包（F37 断点续传前置检查的豁免闸）")
def pack(
    articles: tuple[str, ...],
    platform_code: str,
    variant: str | None,
    outdir: str | None,
    force: bool,
) -> None:
    """生成 manual_pack 物料包（README/payload/checks/cover 四件套）。"""
    if variant and len(articles) > 1:
        # variant 单值对多文件无法逐篇对应——显式报错（逐篇指定后置，避免歧义）
        raise click.UsageError(
            "--variant 仅支持单文件调用；多文件批量请逐篇执行（variant 逐篇指定能力后置）"
        )
    if outdir and len(articles) > 1:
        # C2：单值 --outdir 对多文件无法逐篇对应——前置显式报错（与 --variant 同语义守门）
        raise click.UsageError(
            "--outdir 仅支持单文件调用；多文件批量请逐篇执行（outdir 逐篇指定能力后置）"
        )
    sys.exit(
        run_batch("pack", articles, lambda a: _pack_one(a, platform_code, variant, outdir, force))
    )


def _pack_one(
    article: str, platform_code: str, variant: str | None, outdir: str | None, force: bool
) -> int:
    from ..services import pack_service

    # F37 断点续传前置：同 article+platform 已交付（uploaded/draft）即跳过，--force 重做
    # （root 从文章路径向上探测——嵌套布局下日志可能在 root 顶层而非文章旁）
    if not force and platform_code in _delivered_platforms(article, _probe_log_root(Path(article))):
        name = _PLATFORM_NAMES.get(platform_code, platform_code)
        click.echo(f"⏭️  已交付 {name}（{platform_code}）：跳过物料包生成；--force 重做")
        return 0
    try:
        pack_dir = pack_service.build_pack_for(article, platform_code, variant=variant, outdir=outdir)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        # FileNotFoundError：显式传入的 --variant 路径不存在——拒绝静默回退原文，
        # 产出「以为是口播稿实为原文」的物料包比报错更危险
        click.echo(f"❌ {e}", err=True)
        return 1
    click.echo(f"✅ 物料包已生成: {pack_dir}")
    click.echo("   按 README.md 的入口与字段对应表粘贴发布；checks.txt 全部 ok 后再点发布")
    return 0


# ── F37 断点续传：已交付平台前置查（publish_log 读侧经单一持有者）──


def _probe_log_root(start: Path) -> Path:
    """从 start 向上探测最近的「含 publish_log.jsonl 的祖先目录」作 root。

    嵌套布局（文章在 root 子目录、日志在 root 顶层 publish_log.jsonl）下
    start.parent 不是日志 root——逐级向上找；找不到回退 start.parent
    （原行为：merge_logs 仍会补读 home 侧日志）。探测复用
    publish_log_service.root_log_path（root 侧路径语义单一持有者）。
    """
    from ..services.publish_log_service import root_log_path  # 延迟导入（命令层惯例）

    for d in start.parents:
        if root_log_path(d).is_file():
            return d
    return start.parent


def _delivered_platforms(article_ref: str | Path, root: Path | None) -> set[str]:
    """查该文章已交付的平台集（F37）：走 publish_log_service.merge_logs
    （root 优先 home 补充、demo 行滤除、坏行宽容），禁止散落直读 jsonl。

    已交付口径（正典周历三态）：result=='uploaded'（浏览器交付）或
    action=='draft'（API 建草稿）。article 匹配按 stem——日志行 article 字段
    历史上是调用时路径串（abs/rel 并存），stem 是唯一稳定键。
    """
    from ..services import publish_log_service  # 延迟导入：services 在调用点解析（命令层惯例）

    stem = Path(article_ref).stem
    platforms: set[str] = set()
    for row in publish_log_service.merge_logs(root):
        platform = row.get("platform")
        if not platform:
            continue
        if row.get("result") == "uploaded" or row.get("action") == "draft":
            if Path(str(row.get("article") or "")).stem == stem:
                platforms.add(str(platform))
    return platforms


def _article_from_pack(pack_dir: str | Path, platform: str) -> str:
    """从物料包目录名反推文章 stem：<stem>.<platform>.pack 约定（pack_service
    缺省命名）；命名不符时用目录名原样（读侧宽容，不做存在性断言）。"""
    name = Path(pack_dir).name
    suffix = f".{platform}.pack"
    return name[: -len(suffix)] if name.endswith(suffix) else name


# ── upload（F16 浏览器上传器，§十六）──────────────────────
@click.group()
def upload() -> None:
    """浏览器上传器：填表交给机器、点发布留给人（需 musespiral[browser]）。"""


@upload.command("login")
@click.option("--platform", required=True, help="平台代码（channels/douyin/xhs/zhihu）")
def upload_login(platform: str) -> None:
    """打开浏览器扫码登录平台，保存登录态到 ~/.musespiral/cookies/。"""
    from ..browser import BrowserNotInstalled
    from ..browser import login as browser_login

    try:
        cookie_path = browser_login(platform)
    except BrowserNotInstalled as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    click.echo(f"✅ 登录态已保存: {cookie_path}")
    click.echo(f"   下一步: musespiral upload publish --platform {platform} --pack <物料包目录>")


@upload.command("publish")
@click.option("--platform", required=True, help="平台代码（channels/douyin/xhs/zhihu）")
@click.option("--pack", required=True, type=click.Path(exists=True, file_okay=False), help="物料包目录（musespiral pack 产物）")
@click.option("--dry-run", is_flag=True, help="不起浏览器：打印步骤清单并校验选择器后退出")
@click.option("--force", is_flag=True, help="忽略「已交付」前置检查强制重开会话（F37 断点续传）")
def upload_publish(platform: str, pack: str, dry_run: bool, force: bool) -> None:
    """打开创作者平台自动填表，人工核对后自行点「发布」（永不代点）。"""
    from ..browser import BrowserNotInstalled, CookieMissing, publish_flow
    from ..browser import dry_run as browser_dry_run

    if dry_run:
        try:
            steps = browser_dry_run(platform, pack)
        except (ValueError, FileNotFoundError) as e:
            # 选择器 YAML 缺文件/缺键（ValueError）或物料包缺 payload.md
            click.echo(f"❌ {e}", err=True)
            sys.exit(1)
        for line in steps:
            click.echo(line)
        return

    # F37 断点续传前置：同 article+platform 已交付（uploaded/draft）即跳过，--force 重做
    # （root 从物料包路径向上探测——嵌套布局下日志可能在 root 顶层而非物料包旁）
    article = _article_from_pack(pack, platform)
    if not force and platform in _delivered_platforms(article, _probe_log_root(Path(pack))):
        name = _PLATFORM_NAMES.get(platform, platform)
        click.echo(f"⏭️  已交付 {name}（{platform}/{article}）：跳过上传会话；--force 重做")
        return

    if not click.confirm(f"将打开 {platform} 创作者平台并自动填写表单（发布按钮永不代点），继续？"):
        click.echo("已取消")
        return
    try:
        publish_flow(platform, pack, on_delivered=_append_uploaded)
    except CookieMissing as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    except BrowserNotInstalled as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)


def _append_uploaded(platform: str, pack_dir: Path) -> None:
    """F37 会话收尾回执：追加 result=uploaded 行（publish_log_service 单一持有者）。

    由 browser.publish_flow 在会话正常结束点回调注入（异常中断不会到达；
    infra 层不 import services，故以回调承载）。红线：回执不含 cookies/密钥，
    只记 article/platform/result；「已交付」≠「已发布」——发布成功与否由人工
    确认。回执写入失败只告警不报错：物料已实际交付，账面失败不误伤交付语义。
    """
    from ..services import publish_log_service  # 延迟导入：services 在调用点解析（命令层惯例）

    article = _article_from_pack(pack_dir, platform)
    try:
        publish_log_service.append_uploaded(article=article, platform=platform)
    except Exception as e:
        click.echo(f"⚠️ 交付回执写入失败（不影响本次交付）: {e}", err=True)
