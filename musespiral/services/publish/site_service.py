"""静态站构建编排（G-T6，正典=《musespiral-调研吸收功能设计-2026-11-18》§七）。

数据面铁律：只导出 publish_log 里 action=publish 的已发布正稿——草稿
（action=draft）与仅交付回执（result=uploaded）一律不导出。日志双位置读序
复用 publish_log_service.merge_logs（root 优先 + home 补充 + 去重 + demo 滤除），
不在本层另起一套口径。

渲染复用 core.site（发布链 Renderer + 纸墨 CSS）；build 落盘后对每份 HTML 跑
external_resource_refs 零外联断言——发现 <script>/<link>/@import/外链 url() 即
fail-closed（抛错），保证产物纯静态可部署任意托管。site build 是 CLI-only，
不注册任何 Studio 端点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.content.parser import parse_article
from ...core.site import (
    SiteEntry,
    external_resource_refs,
    render_article_html,
    render_index,
    render_rss,
)
from . import publish_log_service
from ..discover_service import DEFAULT_ARTICLES_ROOT  # 跨域（discover 不在发布域）

DEFAULT_SITE_TITLE = "musespiral 文章站"
DEFAULT_SITE_DESCRIPTION = "已发布文章归档"
_DRY_RUN_PLACEHOLDER = "__SITE_LINK_PLACEHOLDER__"


@dataclass
class BuildResult:
    """一次 build 的结果统计（CLI 报告用）。"""

    output_dir: str
    published_total: int = 0          # publish_log 中已发布文章去重数
    written: list[str] = field(default_factory=list)   # 落盘的文章 html 相对路径
    skipped: list[str] = field(default_factory=list)   # 解析失败/文件缺失跳过
    index: str = "index.html"
    feed: str = "feed.xml"


def _resolve_root(root: str | Path | None) -> Path:
    return Path(root).expanduser() if root is not None else DEFAULT_ARTICLES_ROOT


def _published_articles(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """从合并日志提取已发布文章去重清单 → [(article 相对路径, 代表日志行)]。

    判据 action == 'publish'（publisher.submit_publish 与 F36c 人工登记同口径）；
    demo_ 行已在 merge_logs 滤除；同篇多平台/多次发布只取首条（按日志序）作为
    date 兜底来源。返回顺序 = 日志首次出现顺序（index/feed 按此排）。
    """
    seen: set[str] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    for row in publish_log_service.merge_logs(root):
        if row.get("action") != "publish":
            continue
        article = str(row.get("article") or "").strip()
        if not article or article in seen:
            continue
        seen.add(article)
        out.append((article, row))
    return out


def _row_date(row: dict[str, Any]) -> str:
    ts = str(row.get("ts") or "")
    return ts[:10] if len(ts) >= 10 and ts[4:5] == "-" else ""


def _meta_date(meta: dict[str, Any]) -> str:
    """frontmatter date / publish_at → YYYY-MM-DD（读侧宽容）。"""
    for key in ("date", "publish_at"):
        raw = meta.get(key)
        if raw is None:
            continue
        s = str(raw)
        if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":
            return s[:10]
    return ""


def _meta_tags(meta: dict[str, Any]) -> tuple[str, ...]:
    raw = meta.get("tags") or ()
    if isinstance(raw, str):
        raw = [raw]
    return tuple(str(t).strip() for t in raw if str(t).strip())


def _html_rel(article_rel: str) -> str:
    """文章 md 相对路径 → 站点 HTML 相对路径（保留子目录，.md→.html）。"""
    p = Path(article_rel)
    return p.with_suffix(".html").as_posix()


def _home_href(html_rel: str) -> str:
    """从文章 HTML 回到站点根 index.html 的相对路径（每深一层加一个 ../）。"""
    depth = len(Path(html_rel).parent.parts)  # "a.html"→0；"sub/a.html"→1
    return "../" * depth + "index.html"


def _assert_no_external(html: str, label: str) -> None:
    refs = external_resource_refs(html)
    if refs:
        raise RuntimeError(f"零外联断言失败（{label}）：发现外部/脚本资源 {refs}")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build(
    output_dir: str | Path,
    *,
    root: str | Path | None = None,
    site_title: str = DEFAULT_SITE_TITLE,
    description: str = DEFAULT_SITE_DESCRIPTION,
    site_link: str = "",
) -> BuildResult:
    """扫描已发布文章 → 逐篇渲染 HTML + index.html + feed.xml → 落盘。

    - output_dir 不存在则自建；已存在则覆盖同名产物（幂等重建）。
    - 解析失败/源文件缺失的文章跳过并记入 result.skipped，不阻塞整站（发布过的
      文章仍其余照常导出）；零外联断言失败则 fail-closed。
    - site_link 仅进 RSS <link>/<guid>（内容，非资源），CLI 缺省为空。
    """
    out = Path(output_dir).expanduser()
    arts_root = _resolve_root(root)
    published = _published_articles(arts_root)
    result = BuildResult(output_dir=str(out), published_total=len(published))

    entries: list[SiteEntry] = []
    for article_rel, row in published:
        src = arts_root / article_rel
        if not src.is_file():
            result.skipped.append(f"{article_rel}: 源文件不存在")
            continue
        try:
            parsed = parse_article(src)
        except Exception as e:  # 坏 frontmatter/读失败：跳过不崩（与 plan 线同宽容口径）
            result.skipped.append(f"{article_rel}: 解析失败 {e}")
            continue

        meta = parsed.metadata
        html_rel = _html_rel(article_rel)
        date = _meta_date(meta) or _row_date(row)
        digest = str(meta.get("digest") or "")
        tags = _meta_tags(meta)
        title = str(meta.get("title") or Path(article_rel).stem)

        article_html = render_article_html(
            parsed, date=date, home_href=_home_href(html_rel), site_title=site_title
        )
        _assert_no_external(article_html, html_rel)
        _write(out / html_rel, article_html)
        result.written.append(html_rel)

        link = f"{site_link.rstrip('/')}/{html_rel}" if site_link else html_rel
        entries.append(SiteEntry(title=title, href=html_rel, date=date,
                                 tags=tags, digest=digest, link=link))

    index_html = render_index(entries, site_title=site_title, description=description)
    _assert_no_external(index_html, "index.html")
    _write(out / "index.html", index_html)

    feed_xml = render_rss(entries, site_title=site_title, site_link=site_link,
                          description=description)
    _write(out / "feed.xml", feed_xml)
    return result


__all__ = ["BuildResult", "build", "DEFAULT_SITE_TITLE", "DEFAULT_SITE_DESCRIPTION"]
