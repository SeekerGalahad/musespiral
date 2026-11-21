"""Markdown parser — frontmatter + block-level structure.

Output: (metadata, blocks)
  metadata: dict from YAML frontmatter (title, cover, theme, ...)
  blocks:   list of {type, ...} preserving document order

Block types:
  - heading: {type, level, text}
  - paragraph: {type, text}
  - list: {type, ordered, items: [text]}
  - quote: {type, text}
  - code: {type, lang, code}
  - image: {type, src, alt}
  - table: {type, headers, rows}
  - hr: {type}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
import mistune
from mistune import HTMLRenderer


@dataclass
class ParsedArticle:
    metadata: dict[str, Any]
    blocks: list[dict[str, Any]] = field(default_factory=list)
    raw_body: str = ""


class _BlockCollector(HTMLRenderer):
    """Collect block-level tokens instead of rendering to HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[dict[str, Any]] = []

    def heading(self, text: str, level: int, **attrs) -> str:
        self.blocks.append({"type": "heading", "level": level, "text": text})
        return ""

    def paragraph(self, text: str) -> str:
        self.blocks.append({"type": "paragraph", "text": text})
        return ""

    def list(self, text: str, ordered: bool, **attrs) -> str:
        # text already contains rendered <li> items; extract plain text per item
        import re

        items = re.findall(r"<li>(.*?)</li>", text, re.DOTALL)
        self.blocks.append({"type": "list", "ordered": ordered, "items": items})
        return ""

    def block_quote(self, text: str) -> str:
        self.blocks.append({"type": "quote", "text": text})
        return ""

    def block_code(self, code: str, info: str | None = None) -> str:
        lang = (info or "").strip().split()[0] if info else ""
        self.blocks.append({"type": "code", "lang": lang, "code": code})
        return ""

    def table(self, text: str, **attrs) -> str:
        # mistune passes rendered table HTML; store raw for renderer to handle
        self.blocks.append({"type": "table", "html": text})
        return ""

    def hrule(self) -> str:
        self.blocks.append({"type": "hr"})
        return ""

    def image(self, text: str, url: str, title: str | None = None) -> str:
        # 对齐 mistune 3 HTMLRenderer.image(text, url, title=None) 的调用约定：
        # text（alt）为位置参数，url/title 以关键字传入（旧签名 src=/alt= 会
        # 直接 TypeError，任何含 ![](...) 的文章都解析崩溃）。行内图片以 <img>
        # 形式保留在段落文本里，渲染级检查（render_checks）据此识别正文图片。
        return f'<img src="{url}" alt="{text}" />'


def parse_article(md_path: str | Path) -> ParsedArticle:
    """Parse a Markdown file with YAML frontmatter."""
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"文章不存在: {path}")

    post = frontmatter.load(path)
    metadata = dict(post.metadata)
    body = post.content

    renderer = _BlockCollector()
    md = mistune.create_markdown(
        renderer=renderer,
        plugins=["table", "strikethrough"],
    )
    md(body)

    return ParsedArticle(metadata=metadata, blocks=renderer.blocks, raw_body=body)


def get_metadata_field(article: ParsedArticle, key: str, default: Any = None) -> Any:
    return article.metadata.get(key, default)
