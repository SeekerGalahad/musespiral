"""Render engine — convert parsed blocks to WeChat-compatible HTML.

All styles are inlined (style="...") because WeChat strips <style> blocks
and class selectors. Themes are YAML files with {placeholder} variables.

自 musespiral/renderer/（__init__.py + engine.py 两文件）合并迁入（后端架构
正典 §八 Phase B）；旧路径为 re-export 垫片，消费方 import 零改动。内置
主题 YAML 数据目录仍驻留 musespiral/renderer/themes/，经 importlib.resources
按包名引用。
"""
from __future__ import annotations

import re
from html import escape as html_escape
from importlib import resources
from pathlib import Path
from typing import Any

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, TextLexer

from .parser import ParsedArticle

# 内置主题 YAML 数据目录（Phase C 自 musespiral/renderer/themes/ 随包迁入 themes_data/）
_THEME_DIR = resources.files("musespiral.core.content").joinpath("themes_data")


def _load_theme(name: str) -> dict[str, Any]:
    """Load a theme via the shared theme engine（用户目录优先 + extends 深合并）.

    themes 模块在初始化时经 resources.files 触发本包导入，故此处函数内
    延迟导入，避免 import 环。
    """
    from .themes import load_theme

    return load_theme(name)


# 参考资料 <ol> 的固定样式 — checklist.render_checks 据此剥离该段：
# 它是外链脚注化的预期归宿，不计入「外链残留」检查（§十二 F3/F4）。
_FOOTNOTES_OL_STYLE = (
    "margin-top: 2em; padding-left: 1.5em; border-top: 1px solid #e5e7eb; padding-top: 1em;"
)


def _resolve_vars(template: str, colors: dict[str, str], typo: dict[str, str]) -> str:
    """Replace {color_name} and {typo_name} placeholders in a style string."""
    result = template
    for k, v in colors.items():
        result = result.replace("{" + k + "}", v)
    for k, v in typo.items():
        result = result.replace("{" + k + "}", v)
    return result


def _resolve_components(theme: dict[str, Any]) -> dict[str, str]:
    """Resolve all component style strings with theme variables."""
    colors = theme.get("colors", {})
    typo = theme.get("typography", {})
    comps = theme.get("components", {})
    resolved: dict[str, str] = {}
    for name, val in comps.items():
        if isinstance(val, dict):
            for sub, style in val.items():
                resolved[f"{name}.{sub}"] = _resolve_vars(style, colors, typo)
        else:
            resolved[name] = _resolve_vars(val, colors, typo)
    return resolved


def _highlight_code(code: str, lang: str, code_theme: str) -> str:
    """Highlight code with Pygments, return HTML with inline styles."""
    try:
        lexer = get_lexer_by_name(lang, stripall=False) if lang else TextLexer()
    except Exception:
        lexer = TextLexer()
    formatter = HtmlFormatter(style=code_theme, noclasses=True)
    return highlight(code, lexer, formatter)


# WeChat-internal links (official account articles) stay clickable inside WeChat
_WECHAT_LINK_PREFIX = "https://mp.weixin.qq.com/"


def _convert_links_to_footnotes(html: str) -> tuple[str, list[str]]:
    """Convert external <a href> links to numbered footnotes at the bottom.

    WeChat blocks external links in articles, so we replace them with
    numbered references and append a reference list. WeChat-internal links
    (https://mp.weixin.qq.com/...) are exempt and keep their <a> tag.
    """
    links: list[str] = []

    def repl(m: re.Match) -> str:
        href = m.group(1)
        text = m.group(2)
        if href.startswith(_WECHAT_LINK_PREFIX):
            return m.group(0)
        links.append(href)
        idx = len(links)
        return f"{text}<sup>[{idx}]</sup>"

    html = re.sub(r'<a href="(https?://[^"]+)"[^>]*>(.*?)</a>', repl, html, flags=re.DOTALL)
    return html, links


class Renderer:
    def __init__(self, theme_name: str = "lapis", code_theme: str = "monokai", dark: bool = False) -> None:
        self.theme = _load_theme(theme_name)
        if dark and self.theme.get("colors_dark"):
            self.theme["colors"] = {**self.theme.get("colors", {}), **self.theme["colors_dark"]}
        self.dark = dark
        self.styles = _resolve_components(self.theme)
        self.code_theme = code_theme

    def render(self, article: ParsedArticle) -> str:
        """Render parsed article to a complete HTML document string."""
        parts: list[str] = []
        for block in article.blocks:
            parts.append(self._render_block(block))
        body = "\n".join(parts)

        # frontmatter linkfoot: false disables link→footnote conversion entirely
        if article.metadata.get("linkfoot", True):
            body, footnotes = _convert_links_to_footnotes(body)
            if footnotes:
                body += self._render_footnotes(footnotes)

        title = article.metadata.get("title", "")
        return self._wrap_document(title, body)

    def _render_block(self, block: dict[str, Any]) -> str:
        btype = block["type"]
        if btype == "heading":
            level = block["level"]
            tag = f"h{level}" if level <= 3 else "h3"
            style = self.styles.get(f"heading.{tag}", self.styles.get("heading.h3", ""))
            return f'<section style="{style}">{block["text"]}</section>'
        if btype == "paragraph":
            text = block["text"]
            # inline code
            text = self._inline_code(text)
            return f'<section style="{self.styles["paragraph"]}">{text}</section>'
        if btype == "list":
            tag = "ol" if block["ordered"] else "ul"
            items = "".join(
                f'<li style="{self.styles["list_item"]}">{it}</li>' for it in block["items"]
            )
            return f'<{tag} style="{self.styles["list"]}">{items}</{tag}>'
        if btype == "quote":
            return f'<blockquote style="{self.styles["quote"]}">{block["text"]}</blockquote>'
        if btype == "code":
            highlighted = _highlight_code(block["code"], block["lang"], self.code_theme)
            return f'<pre style="{self.styles["code_block"]}">{highlighted}</pre>'
        if btype == "image":
            return f'<img src="{block["src"]}" alt="{block.get("alt", "")}" style="{self.styles["image"]}" />'
        if btype == "table":
            return f'<table style="{self.styles["table"]}">{block["html"]}</table>'
        if btype == "hr":
            return f'<hr style="{self.styles["hr"]}" />'
        return ""

    def _inline_code(self, text: str) -> str:
        return re.sub(
            r"<code>(.*?)</code>",
            lambda m: f'<code style="{self.styles["inline_code"]}">{m.group(1)}</code>',
            text,
        )

    def _render_footnotes(self, links: list[str]) -> str:
        items = "".join(
            f'<li style="font-size: 13px; color: #9ca3af; margin: 4px 0;">'
            f'<a href="{url}" style="color: #9ca3af; word-break: break-all;">[{i}] {url}</a></li>'
            for i, url in enumerate(links, 1)
        )
        return f'<ol style="{_FOOTNOTES_OL_STYLE}">{items}</ol>'

    def _wrap_document(self, title: str, body: str) -> str:
        bg = self.theme.get("colors", {}).get("background", "#ffffff")
        # title 来自 frontmatter，必须转义：原样插入 </title><script> 会逃逸出
        # <title> 并在同源预览 iframe（studio /api/article/html）里执行
        return (
            f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{html_escape(title)}</title></head>"
            f'<body style="background: {bg}; max-width: 677px; margin: 0 auto; padding: 20px;">'
            f"{body}</body></html>"
        )


def list_builtin_themes() -> list[str]:
    return sorted(p.stem for p in _THEME_DIR.glob("*.yaml"))


__all__ = ["Renderer", "list_builtin_themes"]
