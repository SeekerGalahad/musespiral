"""个人风格资产命令组：style（F44 第十七期 §一）。

musespiral style init——从文章库已有 published/reviewed 定稿抽取范文段（每篇
取首个合用正文段，最多 3 篇），写入 ~/.musespiral/style_samples.md；无可抽
定稿则生成模板（纯 HTML 注释块，未填写时零注入）。style_samples.md 与
habits.json 同为纯本地风格资产：永不入 git、永不上云，人工可编辑。

本文件只定义不注册（两组禁碰 cli.py 约定——style 组挂载归集成轮）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import click
import frontmatter

from .. import config
from ..core.article import PUBLISHED, REVIEWED, read_status
from ..infra.ai.habits_inject import SAMPLES_MAX, STYLE_SAMPLES_FILE
from ..services.discover_service import DEFAULT_ARTICLES_ROOT

# 定稿状态（抽取来源）：已发布优先语义上同权，统一按 mtime 新者先取
_FINAL_STATUSES = (REVIEWED, PUBLISHED)

# 批注预览产物（frontmatter 沿袭原文，status 可能同为 reviewed）不作为范文源
_SKIP_SUFFIX = ".annotated.md"

# 不适合做风格样例的段首（标题/引用/图片/表格/列表/围栏/注释/小红书话题行）
_SKIP_PREFIXES = ("#", ">", "!", "|", "-", "*", "`", "<!--", "话题：", "话题:", "【")

# 单段范文截断长度（控 token；截断以 … 收尾）
_EXCERPT_MAX_CHARS = 300

_TEMPLATE = (
    "<!-- musespiral 风格范文（模板）：文章库中没有 published/reviewed 定稿（或定稿正文没有可抽取的段落）。\n"
    "手工把 2-3 段最能代表你写作风格的定稿文字粘贴到本文件（段与段之间空一行，可删除本注释）；\n"
    "AI 六个生成任务注入时取前 3 段作为风格样例。本文件纯本地资产，永不入 git、永不上云。 -->"
)

_HEADER_COMMENT = (
    "<!-- musespiral 个人风格范文（musespiral style init 生成，可手工编辑）：AI 生成任务注入时取前 3 段；"
    "纯本地资产，永不入 git、永不上云。 -->"
)


@click.group()
def style() -> None:
    """个人风格资产（定稿范文样例；纯本地，永不入 git、永不上云）。"""


@style.command("init")
@click.option(
    "--root",
    "root_dir",
    default=None,
    type=click.Path(file_okay=False),
    help="文章库根（默认 ~/musespiral-articles，与 backup/discover 同源）",
)
def init(root_dir: str | None) -> None:
    """从已发布/已审核定稿抽取范文段，生成 ~/.musespiral/style_samples.md（无定稿则生成模板）。"""
    arts_root = Path(root_dir).expanduser() if root_dir is not None else DEFAULT_ARTICLES_ROOT
    try:
        out, sources = init_style(arts_root)
    except FileExistsError as e:
        click.echo(f"❌ {e}", err=True)
        sys.exit(1)
    if sources:
        click.echo(f"✅ 已从 {len(sources)} 篇定稿抽取范文段 → {out}:")
        for name in sources:
            click.echo(f"  - {name}")
        click.echo("   六个 AI 生成任务（rewrite/adapt/title/digest/annotate_rewrite/discover_suggest）注入时自动生效")
    else:
        click.echo(f"⚠️  未找到可抽取的定稿，已生成模板 {out}")
        click.echo("   手工填 2-3 段范文后生效（纯本地资产，不入 git 不上云）")


def init_style(
    articles_root: str | Path,
    samples_path: str | Path | None = None,
) -> tuple[Path, list[str]]:
    """生成 style_samples.md，返回 (写入路径, 来源文件名列表)。

    有合用定稿 → 抽段文件（头部说明注释 + ≤3 段范文）；无 → 模板（单一
    HTML 注释块，未填写时注入为零段）。文件已存在拒绝覆盖（FileExistsError，
    人工维护资产不自动重建）。
    """
    out = Path(samples_path) if samples_path is not None else config.home_dir() / STYLE_SAMPLES_FILE
    if out.exists():
        raise FileExistsError(f"范文资产已存在，拒绝覆盖: {out}（人工维护；如需重建请先删除）")
    excerpts: list[str] = []
    sources: list[str] = []
    for p in collect_finals(articles_root):
        if len(excerpts) >= SAMPLES_MAX:
            break
        try:
            post = frontmatter.load(p)
        except (OSError, ValueError):  # ValueError 含 UnicodeDecodeError
            continue
        para = extract_sample_paragraph(post.content)
        if para:
            excerpts.append(para)
            sources.append(p.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    if excerpts:
        out.write_text(_HEADER_COMMENT + "\n\n" + "\n\n".join(excerpts) + "\n", encoding="utf-8")
    else:
        out.write_text(_TEMPLATE + "\n", encoding="utf-8")
    return out, sources


def collect_finals(articles_root: str | Path) -> list[Path]:
    """文章库内 status 为 reviewed/published 的 .md 定稿，mtime 新者优先（并列按路径名）。"""
    root = Path(articles_root)
    if not root.is_dir():
        return []
    finals: list[Path] = []
    for p in root.rglob("*.md"):
        if not p.is_file() or p.name.endswith(_SKIP_SUFFIX):
            continue
        try:
            if read_status(p) in _FINAL_STATUSES:
                finals.append(p)
        except (OSError, ValueError):  # ValueError 含 UnicodeDecodeError（非 UTF-8 定稿）
            continue
    return sorted(finals, key=lambda p: (-p.stat().st_mtime, str(p)))


def extract_sample_paragraph(content: str, max_chars: int = _EXCERPT_MAX_CHARS) -> str:
    """定稿正文中首个适合做风格样例的散文段；无则空串。

    空行分段，跳过标题/引用/图片/表格/列表/围栏/HTML 注释/话题行起头的段；
    超长段截断（… 收尾，控 token）。
    """
    for para in re.split(r"\n\s*\n", content.strip()):
        p = para.strip()
        if not p or any(p.startswith(pre) for pre in _SKIP_PREFIXES):
            continue
        if len(p) > max_chars:
            return p[:max_chars].rstrip() + "…"
        return p
    return ""
