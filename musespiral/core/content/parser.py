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
  - video: {type, src, poster?, caption?}  # B3 :::video 容器块（2026-11-26）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
import mistune
from mistune import HTMLRenderer

# ── B3 :::video 容器块（2026-11-26，三线扩展设计 §3.3）────────────────
# 形态（自定义 fenced 容器，不是 ``` 代码块）：
#     :::video{src="assets/lecture.mp4" poster="assets/f1.jpg"}
#     可选题注行（figcaption）
#     :::
# 为什么预处理分段而不是 mistune 插件：内置插件族无 directive/container
# （mistune 3.x 官方表实测），自写 AST 插件成本高于「按容器切段、段间
# 复用同一 _BlockCollector」；切段法保序（video 块与文本块按文档序交替
# 进 blocks），未闭合容器按普通文本回退（诚实不报错——坏语法不吞正文）。
# L2 纯层纪律：本文件零 I/O，容器切分只做字符串扫描。
_VIDEO_OPEN_RE = re.compile(r"^:::video[ \t]*\{(.*)\}[ \t]*$")
_VIDEO_CLOSE_RE = re.compile(r"^:::[ \t]*$")
# 属性形态：src="a b.mp4"（带引号，可含空格）或 poster=b.jpg（裸值，止于空白/}）
_VIDEO_ATTR_RE = re.compile(r'([a-zA-Z_][\w-]*)\s*=\s*(?:"([^"]*)"|([^\s"}]+))')


def _parse_video_attrs(raw: str) -> dict[str, str]:
    """容器头 {...} 内的 key=value 属性表（src/poster 消费面；未知键保留）。"""
    return {
        m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
        for m in _VIDEO_ATTR_RE.finditer(raw)
    }


def _split_video_containers(body: str) -> list[tuple[bool, Any]]:
    """正文 → [(is_video, payload), …] 文档序切段。

    文本段 payload=str（原样交 mistune）；视频段 payload=video 块 dict
    （caption=开闭行之间非空行，空格连接）。未闭合的 :::video 开行按普通
    文本回退（后续行全部归文本——不吞正文、不报错）。"""
    out: list[tuple[bool, Any]] = []
    lines = body.split("\n")
    text_buf: list[str] = []
    i = 0
    while i < len(lines):
        open_m = _VIDEO_OPEN_RE.match(lines[i])
        if open_m is None:
            text_buf.append(lines[i])
            i += 1
            continue
        # 找配对闭合行；找不到则整段回退为文本
        j = i + 1
        while j < len(lines) and _VIDEO_CLOSE_RE.match(lines[j]) is None:
            j += 1
        if j >= len(lines):
            text_buf.extend(lines[i:])
            break
        attrs = _parse_video_attrs(open_m.group(1))
        caption = " ".join(
            ln.strip() for ln in lines[i + 1 : j] if ln.strip()
        )
        if text_buf:
            out.append((False, "\n".join(text_buf)))
            text_buf = []
        block: dict[str, Any] = {"type": "video", "src": attrs.get("src", "")}
        if attrs.get("poster"):
            block["poster"] = attrs["poster"]
        if caption:
            block["caption"] = caption
        out.append((True, block))
        i = j + 1
    if text_buf:
        out.append((False, "\n".join(text_buf)))
    return out


def extract_video_blocks(body: str) -> list[dict[str, Any]]:
    """正文中的 :::video 块清单（pack/文章级媒体探测消费面——B3 物料包与
    队列徽章共用，不经完整 parse_article）。"""
    return [payload for is_video, payload in _split_video_containers(body) if is_video]


def article_media_info(article: "ParsedArticle") -> dict[str, Any]:
    """文章媒体画像（B3 发布中心徽章/适配器校验消费面，纯函数）：

    {"video": bool} —— frontmatter.media.kind=video **或** 正文含 :::video 块
    （设计 §3.2 双入口：frontmatter 声明视频文章 / 编辑器插入视频块）；
    {"images": int} —— 正文图片引用数（![](…) 行内形态，与真机②判例同口径
    ——解析器不产 image 块，按 raw_body 正则计）。"""
    media = article.metadata.get("media")
    is_video = (
        isinstance(media, dict) and str(media.get("kind") or "") == "video"
    ) or bool(extract_video_blocks(article.raw_body))
    images = len(re.findall(r"!\[[^\]]*\]\(", article.raw_body))
    return {"video": is_video, "images": images}


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
    """Parse a Markdown file with YAML frontmatter.

    B3 起 :::video 容器切段解析（见 _split_video_containers）——视频块与
    文本块按文档序进 blocks；raw_body 仍是完整原文（保存/回写零改动）。
    """
    path = Path(md_path)
    if not path.exists():
        raise FileNotFoundError(f"文章不存在: {path}")

    post = frontmatter.load(path)
    metadata = dict(post.metadata)
    body = post.content

    blocks = parse_blocks(body)
    return ParsedArticle(metadata=metadata, blocks=blocks, raw_body=body)


def parse_blocks(body: str) -> list[dict[str, Any]]:
    """Markdown 正文 → blocks（素材查看器批抽出：parse_article 的解析段同源，
    供纯文本（无 frontmatter/文件）场景复用——mistune 单一解析事实源）。"""
    blocks: list[dict[str, Any]] = []
    for is_video, payload in _split_video_containers(body):
        if is_video:
            blocks.append(payload)
            continue
        renderer = _BlockCollector()
        md = mistune.create_markdown(
            renderer=renderer,
            plugins=["table", "strikethrough"],
        )
        md(payload)
        blocks.extend(renderer.blocks)
    return blocks


def get_metadata_field(article: ParsedArticle, key: str, default: Any = None) -> Any:
    return article.metadata.get(key, default)
