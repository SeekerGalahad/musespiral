"""网页正文提取（B 采集扩展 §二 F66/F67）——readability-lxml 裁决实现。

选型（B 正典 §十 R-B2 实施修订裁决引入 readability-lxml，替代原自研 readability-lite）：
密度评分/主容器定位交给 readability；本模块负责三件配套：
  - 摘要 HTML → Markdown 产块（stdlib HTMLParser 事件驱动，骨架纪律沿
    url_fetch._WxToMarkdown——h1-h6→#、pre/code→``` 围栏逐字保留、
    blockquote→>、strong/b→**、em/i→*、a→[text](href)）；
  - 置信度 = 主容器文本占全页可见文本比（0~1，两位小数）——提取器只标记
    置信不否决：≥0.5 建议 crawled、<0.5 降级 link（人工裁决存哪种）；
  - license 标记：rel="license" 链接 / CC meta → 声明原文（枚举推断归
    服务层）；缺省 ALL-RIGHTS（网络内容默认有主）也归服务层。

纯转换零 I/O（html 由 infra/fetcher 咽喉取得）。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from readability import Document

EXTRACTOR = "readability-lxml"

# 整体跳过其内容（提取摘要后的残余噪声/脚本样式）
_SKIP_TAGS = frozenset({"script", "style", "noscript", "nav", "header", "footer", "aside", "form"})
_VOID_TAGS = frozenset({"img", "br", "hr", "meta", "link", "input", "source", "wbr"})
_HEADING_TAGS = {f"h{i}": i for i in range(1, 7)}
_BLOCK_TAGS = frozenset(
    {"p", "div", "blockquote", "ul", "ol", "li", "article", "section", "table", "figure", "tr"}
)
_WS_RE = re.compile(r"[ \t\r\f\v]+")


class _HtmlToMarkdown(HTMLParser):
    """事件驱动 HTML → Markdown（畸形 HTML 不崩溃：栈恢复沿 url_fetch 判例）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.full_text_len = 0  # 全页可见文本长度（skip 剥离后）
        self._buf: list[str] = []
        self._stack: list[str] = []
        self._skip_depth = 0
        self._in_pre = 0
        self._bold = 0
        self._italic = 0
        self._href: list[str | None] = []
        self._link_buf: list[str] = []

    # ── 事件 ──────────────────────────────────────────────

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _VOID_TAGS:
            if tag == "br":
                self._buf.append("\n")
            return  # 无闭合标签不入栈
        self._stack.append(tag)
        if tag == "pre":
            self._flush()
            self._in_pre += 1
            self.blocks.append("```")
        elif tag in _HEADING_TAGS:
            self._flush()
        elif tag == "a":
            self._flush()
            self._href.append(dict(attrs).get("href"))
            self._link_buf.append("")
        else:
            if tag in ("strong", "b"):
                self._bold += 1
                self._buf.append("**")
            elif tag in ("em", "i"):
                self._italic += 1
                self._buf.append("*")
            if tag in _BLOCK_TAGS:
                self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag not in self._stack:
            return  # 游离结束标签直接忽略（畸形恢复，url_fetch 判例）
        while self._stack and self._stack[-1] != tag:
            self._close_tag(self._stack.pop())
        if self._stack:  # 匹配项弹出并闭合
            self._stack.pop()
            self._close_tag(tag)

    def _close_tag(self, tag: str) -> None:
        if tag == "pre":
            self._flush(pre=True)
            self._in_pre = max(0, self._in_pre - 1)
            self.blocks.append("```")
        elif tag in _HEADING_TAGS:
            self._flush(heading=_HEADING_TAGS[tag])
        elif tag == "a":
            href = self._href.pop() if self._href else None
            label = (self._link_buf.pop() if self._link_buf else "").strip()
            self._buf.append(f"[{label}]({href})" if href and label else label)
        else:
            if tag in ("strong", "b"):
                self._bold = max(0, self._bold - 1)
                self._buf.append("**")
            elif tag in ("em", "i"):
                self._italic = max(0, self._italic - 1)
                self._buf.append("*")
            if tag in _BLOCK_TAGS:
                self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        self.full_text_len += len(data.strip())
        if self._link_buf:  # 链接内文本由闭合时统一发（防重复）
            self._link_buf[-1] += data
            return
        self._buf.append(data)

    # ── 冲刷 ──────────────────────────────────────────────

    def _flush(self, *, pre: bool = False, heading: int = 0) -> None:
        raw = "".join(self._buf)
        self._buf = []
        if pre:
            text = "\n".join(ln.rstrip() for ln in raw.split("\n")).strip("\n")
            if text:
                self.blocks.append(text)
            return
        lines = [ln for ln in (_WS_RE.sub(" ", s).strip() for s in raw.split("\n")) if ln]
        if not lines:
            return
        text = "\n".join(lines)
        self.blocks.append(("#" * heading + " " + text) if heading else text)

    def finish(self) -> None:
        while self._stack:
            self._close_tag(self._stack.pop())
        self._flush(pre=bool(self._in_pre))
        self._flush()


def _html_to_markdown(html: str) -> tuple[str, int]:
    """HTML → (Markdown 正文, 全页可见文本长度)。"""
    parser = _HtmlToMarkdown()
    parser.feed(html)
    parser.close()
    parser.finish()
    return "\n\n".join(parser.blocks).strip(), parser.full_text_len


def _detect_license_note(html: str) -> str:
    """页面 CC 标记（rel="license" 链接 / CC meta）→ 声明原文。

    只返回 creativecommons.org 声明原文（枚举推断归服务层）；无标记空串。
    """
    href = ""
    match = re.search(r'<(?:a|link)[^>]+rel=["\']license["\'][^>]*>', html, re.IGNORECASE)
    if match:
        m2 = re.search(r'href=["\']([^"\']+)["\']', match.group(0), re.IGNORECASE)
        href = m2.group(1) if m2 else ""
    meta = re.search(
        r'<meta[^>]+name=["\'](?:cc\.license|copyright)["\'][^>]*>', html, re.IGNORECASE
    )
    content = ""
    if meta:
        m3 = re.search(r'content=["\']([^"\']*)["\']', meta.group(0), re.IGNORECASE)
        content = m3.group(1) if m3 else ""
    note = href or content
    return note if note and "creativecommons.org" in note.lower() else ""


def extract(html: bytes | str) -> dict[str, Any]:
    """readability 正文提取 → {title, text(Markdown), confidence, word_count,
    license_note}。

    提取失败/空摘要（列表页/极简页）不报错——返回零置信产物（降级链输入，
    mode 裁决归服务层）；置信度 = 主容器文本占全页比，两位小数。
    """
    if isinstance(html, bytes):
        html_text = html.decode("utf-8", errors="replace")
    else:
        html_text = html
    license_note = _detect_license_note(html_text)
    try:
        doc = Document(html_text)
        title = (doc.short_title() or "").strip()
        summary_html = doc.summary(html_partial=True)
    except Exception:  # readability 对残缺页可能抛异常 → 降级零置信产物
        return {"title": "", "text": "", "confidence": 0.0, "word_count": 0,
                "license_note": license_note}
    text, _ = _html_to_markdown(summary_html)
    _, full_len = _html_to_markdown(html_text)
    confidence = min(1.0, round(len(text) / full_len, 2)) if text and full_len > 0 else 0.0
    return {
        "title": title,
        "text": text,
        "confidence": confidence,
        "word_count": len(text.split()),
        "license_note": license_note,
    }
