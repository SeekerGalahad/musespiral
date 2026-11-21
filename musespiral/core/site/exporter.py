"""静态站导出渲染器（G-T6，正典=《musespiral-调研吸收功能设计-2026-11-18》§七）。

三支纯函数：render_article_html / render_index / render_rss。零网络、零 JS、
零外联 **script/style** 资源（<script>/<link>/@import/url()——build 侧
external_resource_refs fail-closed 门）；纸墨 token 以 <style> 内联，取自
studio/ui.css 的 :root 子集。**边界（2026-11-19 复核判例）**：正文里的外链
<img>（Markdown 图片）属**内容面**、按原样导出——探针不拦，离线/防盗链环境
会裂图；导出时是否拉平为本地资源属产品裁决，本批按内容保真不拦。

文章正文复用发布链渲染管线（core.content.renderer.Renderer，md→html 同一
实现），保证静态站与公众号正稿视觉一致；本模块只在其外包裹站点导航、meta
与纸墨 CSS。RSS 里的文章 <link> 是内容而非资源引用，不受零外联断言约束。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime, timezone as _timezone
from email.utils import format_datetime
from html import escape as html_escape
from typing import Sequence

from ... import config
from ..content.parser import ParsedArticle
from ..content.renderer import Renderer

# 纸墨工坊 token（studio/ui.css:1 :root 子集，展开为字面值——<style> 自包含，
# 不依赖外部 CSS 变量定义）。仅站点 chrome 用；正文沿用发布链主题的内联样式。
SITE_CSS = """
:root{color-scheme:light;--vermilion:#c0392b;--indigo:#2c5f8a;--ink-1:#1a1a1a;
--ink-2:#6b6b6b;--ink-3:#9a948c;--paper-0:#faf9f7;--paper-1:#ffffff;
--paper-2:#f1efec;--line-1:#e8e6e3;--line-2:#d5d2ce;
--font-body:-apple-system,"SF Pro Text","PingFang SC","Noto Sans SC","Microsoft YaHei",sans-serif;
--font-mono:"SF Mono","JetBrains Mono",ui-monospace,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--paper-0);color:var(--ink-1);
font-family:var(--font-body);font-size:15px;line-height:1.75}
.site-wrap{max-width:760px;margin:0 auto;padding:32px 20px 64px}
.site-head{border-bottom:2px solid var(--ink-1);padding-bottom:12px;margin-bottom:28px}
.site-head a{color:var(--ink-1);text-decoration:none;font-size:13px;letter-spacing:.08em}
.site-title{font-size:28px;font-weight:700;letter-spacing:.04em;margin:0 0 4px}
.site-meta{color:var(--ink-2);font-size:13px;margin:6px 0 20px}
.site-tags{color:var(--ink-3);font-size:12px;margin-bottom:20px}
.site-tags span{display:inline-block;border:1px solid var(--line-2);border-radius:999px;
padding:1px 10px;margin:0 6px 4px 0}
.site-article{background:var(--paper-1);border:1px solid var(--line-1);
border-radius:8px;padding:24px 28px}
.site-foot{margin-top:36px;padding-top:14px;border-top:1px solid var(--line-1);
color:var(--ink-3);font-size:12px}
.post-list{list-style:none;margin:0;padding:0}
.post-list li{padding:14px 0;border-bottom:1px solid var(--line-1)}
.post-list a{color:var(--ink-1);font-size:17px;font-weight:600;text-decoration:none}
.post-list a:hover{color:var(--vermilion)}
.post-date{color:var(--ink-2);font-size:13px;margin-left:10px}
.post-digest{color:var(--ink-2);font-size:13px;margin:4px 0 0}
.post-tags{color:var(--ink-3);font-size:12px;margin-top:4px}
""".strip()

_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)

# 外部资源引用探针（零外联断言）：任意 <script>/<link> 标签、CSS @import、
# url(http...) 都视为外联资源。正文的 <a href="https://..."> 是内容链接，
# 发布链已脚注化，不在本探针范围；同理正文 <img src="https://..."> 是内容
# 面外链图，按原样导出、不在本探针范围（边界注见模块头）。
_SCRIPT_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_IMPORT_RE = re.compile(r"@import\s+[^;]+;", re.IGNORECASE)
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?https?://", re.IGNORECASE)


@dataclass(frozen=True)
class SiteEntry:
    """站点条目（index/rss 的公共数据面）。

    href:      相对站点根的路径，如 "posts/foo.html"（内容是链接，非资源）。
    link:      绝对 URL（RSS <link>/<guid> 用）；缺省由 render_rss 的 site_link 拼接。
    date:      ISO 日期 YYYY-MM-DD（缺省空串）。
    tags:      标签元组。
    digest:    摘要（index 描述 / RSS description）。
    """

    title: str
    href: str
    date: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    digest: str = ""
    link: str = ""


def _publish_body(parsed: ParsedArticle) -> str:
    """复用发布链 Renderer 把 md 正文渲染为 HTML，返回 <body> 内片段。

    Renderer.render 产出完整文档；本函数只抽取其 body 内容注入站点壳，正文的
    主题内联样式 / 代码高亮 / 外链脚注化与 publish 路径逐字节一致。
    """
    theme = str(parsed.metadata.get("theme") or config.DEFAULT_THEME)
    code_theme = str(parsed.metadata.get("code_theme") or config.DEFAULT_CODE_THEME)
    dark = bool(parsed.metadata.get("dark", False))
    doc = Renderer(theme_name=theme, code_theme=code_theme, dark=dark).render(parsed)
    match = _BODY_RE.search(doc)
    if not match:  # 发布链产物结构漂移 → fail closed，不产出残缺页
        raise RuntimeError("发布链渲染产物结构异常：找不到 <body>...</body>")
    return match.group(1)


def _tags_html(tags: Sequence[str], css_class: str) -> str:
    if not tags:
        return ""
    items = "".join(f"<span>{html_escape(str(t))}</span>" for t in tags)
    return f'<div class="{css_class}">{items}</div>'


def render_article_html(
    parsed: ParsedArticle,
    *,
    date: str = "",
    home_href: str = "../index.html",
    site_title: str = "musespiral 文章站",
) -> str:
    """单篇已发布文章 → 纸墨主题 HTML 文档（零 JS / 零外联 script/style 资源）。

    正文外链 <img> 属内容面照原样导出（边界注见模块头）。

    frontmatter 的 title/author/digest/tags 进 <head> meta 与页头；正文走发布链
    渲染管线。所有 frontmatter 派生文本均转义，防注入逃逸。
    """
    meta = parsed.metadata
    title = str(meta.get("title") or "无标题")
    author = str(meta.get("author") or "")
    digest = str(meta.get("digest") or "")
    tags = tuple(str(t) for t in (meta.get("tags") or ()) if str(t).strip())

    head = [f'<meta charset="utf-8">',
            f'<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{html_escape(title)} · {html_escape(site_title)}</title>",
            f'<meta name="description" content="{html_escape(digest, quote=True)}">',
            f'<meta name="keywords" content="{html_escape(", ".join(tags), quote=True)}">']
    if author:
        head.append(f'<meta name="author" content="{html_escape(author, quote=True)}">')
    if date:
        head.append(f'<meta property="article:published_time" content="{html_escape(date, quote=True)}">')
    head.append(f"<style>{SITE_CSS}</style>")
    head_html = "\n".join(head)

    meta_bits = []
    if date:
        meta_bits.append(f'<time class="post-date">{html_escape(date)}</time>')
    if author:
        meta_bits.append(html_escape(author))
    meta_html = ' · '.join(meta_bits)
    meta_line = f'<div class="site-meta">{meta_html}</div>' if meta_html else ""

    body = (
        f'<!DOCTYPE html><html lang="zh-CN"><head>\n{head_html}\n</head><body>'
        f'<div class="site-wrap">'
        f'<header class="site-head"><a href="{html_escape(home_href, quote=True)}">← 返回目录</a></header>'
        f'<h1 class="site-title">{html_escape(title)}</h1>'
        f'{meta_line}'
        f'{_tags_html(tags, "site-tags")}'
        f'<main class="site-article">{_publish_body(parsed)}</main>'
        f'<footer class="site-foot">由 musespiral 静态站导出 · {html_escape(site_title)}</footer>'
        f'</div></body></html>'
    )
    return body


def render_index(
    entries: Sequence[SiteEntry],
    *,
    site_title: str = "musespiral 文章站",
    description: str = "已发布文章归档",
) -> str:
    """文章列表页 index.html（日期 + 标签 + 摘要，零 JS）。"""
    rows: list[str] = []
    for e in entries:
        date_html = f'<span class="post-date">{html_escape(e.date)}</span>' if e.date else ""
        digest_html = f'<p class="post-digest">{html_escape(e.digest)}</p>' if e.digest else ""
        tags_html = (
            f'<div class="post-tags">{" · ".join(html_escape(t) for t in e.tags)}</div>'
            if e.tags else ""
        )
        rows.append(
            f'<li><a href="{html_escape(e.href, quote=True)}">{html_escape(e.title)}</a>{date_html}'
            f'{digest_html}{tags_html}</li>'
        )
    listing = "".join(rows) or "<li>暂无已发布文章</li>"
    return (
        f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{html_escape(site_title)}</title>'
        f'<meta name="description" content="{html_escape(description, quote=True)}">'
        f"<style>{SITE_CSS}</style></head><body><div class=\"site-wrap\">"
        f'<header class="site-head"><h1 class="site-title">{html_escape(site_title)}</h1>'
        f'<div class="site-meta">{html_escape(description)} · 共 {len(entries)} 篇</div></header>'
        f'<ul class="post-list">{listing}</ul>'
        f'<footer class="site-foot">由 musespiral 静态站导出</footer>'
        f'</div></body></html>'
    )


def _rfc822(date_str: str) -> str:
    """ISO 日期 → RFC 822（RSS pubDate）；非法/缺失回退原样（不崩）。"""
    if not date_str:
        return ""
    try:
        d = _date.fromisoformat(date_str)
        dt = _datetime(d.year, d.month, d.day, tzinfo=_timezone.utc)
    except ValueError:
        try:
            dt = _datetime.fromisoformat(date_str)
        except ValueError:
            return date_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_timezone.utc)
    return format_datetime(dt)


def render_rss(
    entries: Sequence[SiteEntry],
    *,
    site_title: str = "musespiral 文章站",
    site_link: str = "",
    description: str = "已发布文章归档",
) -> str:
    """RSS 2.0 feed.xml（文章 <link> 是内容链接，不是外链资源）。"""
    items: list[str] = []
    for e in entries:
        link = e.link or (f"{site_link.rstrip('/')}/{e.href}" if site_link else e.href)
        parts = [f"<title>{html_escape(e.title)}</title>",
                 f"<link>{html_escape(link)}</link>",
                 f"<guid isPermaLink=\"true\">{html_escape(link)}</guid>"]
        if e.date:
            parts.append(f"<pubDate>{html_escape(_rfc822(e.date))}</pubDate>")
        if e.digest:
            parts.append(f"<description>{html_escape(e.digest)}</description>")
        items.append("<item>" + "".join(parts) + "</item>")
    channel = (
        "<channel>"
        f"<title>{html_escape(site_title)}</title>"
        f"<link>{html_escape(site_link)}</link>"
        f"<description>{html_escape(description)}</description>"
        + "".join(items)
        + "</channel>"
    )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0">' + channel + "</rss>\n"


def external_resource_refs(html: str) -> list[str]:
    """列出 HTML 里的外部 script/style 资源引用（<script>/<link>/@import/url()）。

    空列表=零外联 JS/CSS（供 build fail-closed）。内容面外链（<a href>、
    正文 <img>）**不在**本探针范围——边界注见模块头。
    """
    refs: list[str] = []
    for pattern in (_SCRIPT_RE, _LINK_RE, _IMPORT_RE, _CSS_URL_RE):
        refs.extend(m.group(0) for m in pattern.finditer(html))
    return refs


__all__ = [
    "SITE_CSS",
    "SiteEntry",
    "external_resource_refs",
    "render_article_html",
    "render_index",
    "render_rss",
]
