"""F23 长图渲染器 — 文章 → 1080 宽单列竖版长图（模板/光栅分层，正典 §十八）。

模板层（纯本地，全测）：build_poster_html —— 头部大标题区（title+digest/日期）
+ 正文流 + 文末标识区（theme 名 + musespiral 生成标注），纯内联 CSS。零外部资源
红线：不引任何 CDN 版 MathJax/KaTeX——LaTeX/Mermaid 段以**源码等宽呈现**，
图片化本身即公众号「转图片」两条 WARN 的承接（poster 路径豁免为提示）。

光栅层（lazy browser）：shoot —— full_page=True 截图（宽 1080 高度流式，
§10.0-V4 实证），并经 _offline_route 执法「光栅不触网」红线：http(s) 请求
一律中止（正文透传的外域图片在截图时不发起任何网络请求），file:// 放行；
复用 browser.session 的 BrowserNotInstalled 与
安装提示（同一错误类型、同一条安装命令，不另起炉灶），本模块顶层不 import
playwright。

复用抉择（任务书二选一，选「复用 Renderer」）：文件边界不允许改
renderer/engine.py，「提取 renderer 可复用段」出局；故以 _PosterRenderer
子类原样继承块渲染管线，仅两处 poster 特化——
① 字号整体上调一档：在 resolved styles 层对 font-size: Npx 统一 ×1.2
   （16px→19px）。不用 CSS 缩放容器（zoom/transform 依赖浏览器实现且会
   破坏 1080 固定宽布局），字符串层缩放确定性可测；
② LaTeX/Mermaid 段源码等宽呈现：检测语义复用 checklist._detect_latex，
   与被豁免的两条 WARN 保持同口径。
"""
from __future__ import annotations

import re
from html import escape as html_escape
from pathlib import Path
from typing import Any

from ...browser.session import BrowserNotInstalled, _sync_playwright
from ..content.parser import ParsedArticle, parse_article
from ..content.renderer import Renderer, _convert_links_to_footnotes
from ..quality.checks import _detect_latex

POSTER_WIDTH = 1080
FONT_SCALE = 1.2  # 「字号整体上调一档」：16px → 19px（缩放值取整到 0.5px）
# 公式/图源码语言（代码块 lang 命中即走等宽源码呈现，不进 pygments）
_FORMULA_LANGS = {"mermaid", "math", "latex"}
_MONO_STACK = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def _bump_font_size(style: str) -> str:
    """style 串内所有 font-size: Npx 统一 ×FONT_SCALE（取整到 0.5px，去尾零）。"""

    def scale(m: re.Match[str]) -> str:
        px = round(float(m.group(1)) * FONT_SCALE * 2) / 2
        return f"font-size: {px:g}px"

    return re.sub(r"font-size: (\d+(?:\.\d+)?)px", scale, style)


class _PosterRenderer(Renderer):
    """Renderer 块渲染管线复用面：块渲染逻辑零改动，仅字号上调特化。"""

    def __init__(self, theme_name: str = "lapis") -> None:
        super().__init__(theme_name=theme_name)
        self.styles = {k: _bump_font_size(v) for k, v in self.styles.items()}
        self.colors: dict[str, str] = self.theme.get("colors", {})


def _formula_span(raw: str, colors: dict[str, str]) -> str:
    """行内公式 $..$ 原文 → 等宽字体块（零外部资源：原文即内容）。"""
    return (
        f'<code style="font-family: {_MONO_STACK};'
        f' background: {colors.get("code_bg", "#f3f4f6")};'
        f' color: {colors.get("primary", "#2563eb")}; padding: 2px 8px;'
        f' border-radius: 4px; font-size: 0.95em;">{raw}</code>'
    )


def _formula_pre(raw: str, colors: dict[str, str], *, already_escaped: bool) -> str:
    """公式/图源码段 → 等宽 pre 块（不引 MathJax/KaTeX，源码原文呈现）。

    already_escaped：段落文本经 mistune 已转义，代码块 code 为原文需自转义。
    """
    body = raw if already_escaped else html_escape(raw)
    return (
        f'<pre style="font-family: {_MONO_STACK};'
        f' background: {colors.get("code_bg", "#f3f4f6")};'
        f' color: {colors.get("code_text", "#1f2937")}; padding: 1em;'
        f' border: 1px solid {colors.get("border", "#e5e7eb")};'
        f' border-radius: 8px; margin: 1.2em 0; font-size: 19px; line-height: 1.6;'
        f' white-space: pre-wrap; word-break: break-all;">{body}</pre>'
    )


def _render_poster_body(article: ParsedArticle, renderer: _PosterRenderer) -> str:
    """正文流：Renderer 块渲染管线 + LaTeX/Mermaid 段等宽源码特化。"""
    colors = renderer.colors
    parts: list[str] = []
    for block in article.blocks:
        btype = block.get("type")
        # ① mermaid/公式语言代码块：源码等宽呈现（不走 pygments——源码即内容）
        if btype == "code" and str(block.get("lang", "")).lower() in _FORMULA_LANGS:
            parts.append(_formula_pre(block["code"], colors, already_escaped=False))
            continue
        # ② 整段即 $$..$$ 展示公式：独立等宽源码块
        if btype == "paragraph":
            stripped = str(block["text"]).strip()
            if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) >= 4:
                parts.append(_formula_pre(stripped, colors, already_escaped=True))
                continue
        # ③ 段/引/标题内行内公式：$..$ 原文换等宽 span（口径=checklist WARN）
        if btype in {"paragraph", "quote", "heading"}:
            hits = _detect_latex([block])
            if hits:
                text = str(block["text"])
                for hit in hits:
                    text = text.replace(hit, _formula_span(hit, colors))
                block = {**block, "text": text}
        parts.append(renderer._render_block(block))
    body = "\n".join(parts)
    # 外链→脚注与 Renderer.render 同款收尾（尊重 frontmatter linkfoot 开关）
    if article.metadata.get("linkfoot", True):
        body, footnotes = _convert_links_to_footnotes(body)
        if footnotes:
            body += renderer._render_footnotes(footnotes)
    return body


def _render_header(metadata: dict[str, Any], colors: dict[str, str]) -> str:
    """头部大标题区：主题色装饰条 + 大标题 + digest/日期（有则呈现）。"""
    lines = [
        f'<header style="padding: 72px 72px 36px;'
        f' background: {colors.get("background", "#ffffff")};">',
        '<div style="width: 72px; height: 8px;'
        f' background: {colors.get("primary", "#2563eb")};'
        ' border-radius: 4px; margin-bottom: 36px;"></div>',
        f'<h1 style="margin: 0 0 24px; font-size: 56px; line-height: 1.35;'
        f' font-weight: 700; color: {colors.get("text", "#1f2937")};">'
        f'{html_escape(str(metadata.get("title", "")))}</h1>',
    ]
    digest = str(metadata.get("digest", "") or "").strip()
    if digest:
        lines.append(
            f'<p style="margin: 0 0 16px; font-size: 26px; line-height: 1.6;'
            f' color: {colors.get("muted", "#6b7280")};">{html_escape(digest)}</p>'
        )
    date = str(metadata.get("date", "") or "").strip()
    if date:
        lines.append(
            f'<p style="margin: 0; font-size: 22px;'
            f' color: {colors.get("muted", "#6b7280")};">{html_escape(date)}</p>'
        )
    lines.append("</header>")
    return "".join(lines)


def _render_footer(theme_name: str, colors: dict[str, str]) -> str:
    """文末标识区：musespiral 生成标注 + 主题名。"""
    return (
        f'<footer style="margin-top: 56px; padding: 40px 72px 64px;'
        f' border-top: 1px solid {colors.get("border", "#e5e7eb")};">'
        f'<p style="margin: 0; text-align: center; font-size: 20px;'
        f' color: {colors.get("muted", "#6b7280")};">'
        f"由 musespiral 生成长图 · 主题 {html_escape(theme_name)}</p></footer>"
    )


def build_poster_html(article_path: str | Path, *, theme: str = "lapis") -> str:
    """文章 Markdown → 1080 宽单列竖版长图 HTML（纯内联 CSS，零外部资源）。"""
    article = parse_article(article_path)
    renderer = _PosterRenderer(theme)
    header = _render_header(article.metadata, renderer.colors)
    body = _render_poster_body(article, renderer)
    footer = _render_footer(theme, renderer.colors)
    title = html_escape(str(article.metadata.get("title", "")))
    return (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{title}</title></head>'
        f'<body style="margin: 0; background: {renderer.colors.get("background", "#ffffff")};">'
        f'<div style="width: {POSTER_WIDTH}px; margin: 0 auto; box-sizing: border-box;">'
        f"{header}"
        f'<main style="padding: 24px 72px 0;">{body}</main>'
        f"{footer}"
        f"</div></body></html>"
    )


def require_browser() -> None:
    """预检浏览器可用性：缺 playwright 抛 BrowserNotInstalled（不落任何文件）。

    CLI 截图路径先调它（同 cards 降级策略），避免半成品文件卡住重跑。
    """
    _sync_playwright()


def _offline_route(route: Any) -> None:
    """光栅红线执法（§10.0「光栅不触网」）：http(s) 请求一律中止——正文
    透传的外域图片等外部资源在截图时不发起任何网络请求；file:// 等本地
    协议放行（长图 HTML 本体仍以 file:// 加载）。
    """
    url = str(getattr(route.request, "url", ""))
    if url.startswith(("http://", "https://")):
        route.abort()
    else:
        route.continue_()


def shoot(html_path: str | Path, out_png: str | Path) -> Path:
    """长图 HTML → PNG 全页截图（lazy playwright；宽 1080 高度流式）。

    外域 http(s) 请求经 _offline_route 一律中止（光栅不触网红线）。
    """
    sync_playwright = _sync_playwright()  # 缺 playwright → BrowserNotInstalled
    html_file = Path(html_path).resolve()
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport={"width": POSTER_WIDTH, "height": 960})
            context.route("**/*", _offline_route)  # 红线：外域请求一律中止，不触网
            page = context.new_page()
            page.goto(html_file.as_uri())  # 红线：光栅只 file:// 加载，不触网
            page.screenshot(path=str(out), full_page=True)
        finally:
            browser.close()
    finally:
        pw.stop()
    return out


__all__ = [
    "POSTER_WIDTH",
    "BrowserNotInstalled",
    "build_poster_html",
    "require_browser",
    "shoot",
]
