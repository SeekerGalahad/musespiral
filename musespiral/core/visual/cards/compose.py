"""F22 切卡组版 — build_cards_html（§十八 F22 切卡规则，实施唯一依据）。

两条路径：
  ① 变体路径（adapt xhs 产物）：frontmatter tags → 结尾卡话题区；正文按
     「空行分段」取段，首段钩子句 → 封面卡副标；其余每 1-2 段成一内容卡
     （卡标题取段首句 ≤12 字）；
  ② 文章路径（无变体）：h2 小节每节一卡（节标题即卡标题），>6 节合并
     相邻短节；首段做封面卡副标；frontmatter tags 收尾。
两路径共用：卡数 3-10（<3 复制内容段补足，>10 合并尾部）；标签数 >6 截前 6。

内容保底：文章路径的列表/引用/代码块转为卡的正文段（代码走 code_bg 样式；
首个 h2 之前导语区的代码并入导语卡，h3+ 小节标题不切卡、标题文本并入所在
节正文）；变体路径首段钩子句之外的余文并入后续内容卡——内容不静默丢失。
图片/表格/hr 不入卡（卡组为纯文本形态；零外部资源红线不嵌图）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter

from ...content.parser import ParsedArticle, parse_article
from . import templates

# 两路径共用的卡数/标签边界（§十八 F22）
MIN_CARDS = 3
MAX_CARDS = 10
MAX_TAGS = 6
# 文章路径：>6 节触发相邻短节合并，收到 ≤6 节
MERGE_SECTIONS_ABOVE = 6
# 变体路径「每 1-2 段」的 1 段侧：超长段独占一卡，避免单卡文字溢出
LONG_PARAGRAPH_CHARS = 120
# 内容卡标题取段首句的上限字数
TITLE_MAX_CHARS = 12

_SENTENCE_END_RE = re.compile(r"[。！？!?…]")
# 纯话题行（adapt xhs 产物把话题行留在正文，便于整篇粘贴）——不成卡
_TOPIC_LINE_RE = re.compile(r"^(?:#[^\s#]+\s*)+$")

# 内容卡内部形态：(卡标题, 正文段列表, 代码文本)
Card = tuple[str, list[str], str]


def build_cards_html(
    article_path: str | Path,
    *,
    variant: str | Path | None = None,
    theme: str = "lapis",
    ratio: str = "3:4",
) -> tuple[str, int]:
    """文章（或其 adapt 变体）→ 卡组单文件 HTML，返回 (html, 卡数)。

    - variant 显式传入时必须存在（FileNotFoundError，拒绝静默回退原文，
      与 pack 同语义）；切卡走变体路径，否则走文章路径；
    - theme 为 themes 主题名（色板注入见 templates.load_palette）；
    - ratio "3:4"（1242×1656）或 "4:5"（1242×1553），未知规格 ValueError。
    """
    article_file = Path(article_path)
    article = parse_article(article_file)
    size = templates.card_size(ratio)  # 未知 ratio 在此抛 ValueError
    palette = templates.load_palette(theme)

    if variant is not None:
        var_path = Path(variant)
        if not var_path.exists():
            raise FileNotFoundError(
                f"--variant 路径不存在: {variant}（拒绝静默回退原文，请检查路径）"
            )
        post = frontmatter.load(var_path)
        title, hook, cards, tags, pool = _from_variant(post, article, var_path.stem)
    else:
        title, hook, cards, tags, pool = _from_article(article, article_file.stem)

    tags = tags[:MAX_TAGS]  # 标签数 >6 截前 6
    cards = _pad_cards(cards, pool, title)
    cards = _merge_tail(cards)

    sections = [templates.cover_card(title, hook, size=size, palette=palette)]
    sections += [
        templates.content_card(t, ps, code=c, size=size, palette=palette)
        for t, ps, c in cards
    ]
    sections.append(templates.ending_card(tags, size=size, palette=palette))
    return templates.document(sections, title=title), len(sections)


# ── 路径①：变体（adapt xhs 产物）────────────────────────────


def _from_variant(
    post: frontmatter.Post, article: ParsedArticle, fallback_stem: str
) -> tuple[str, str, list[Card], list[str], list[str]]:
    """frontmatter tags → 结尾卡；空行分段，首段钩子 → 封面副标（段内余文
    并入内容卡流，不静默丢弃）；其余 1-2 段/卡。"""
    title = str(
        post.metadata.get("title") or article.metadata.get("title") or fallback_stem
    )
    tags = _normalize_tags(post.metadata.get("tags")) or _normalize_tags(
        article.metadata.get("tags")
    )
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", post.content.strip())
        if p.strip()
    ]
    paragraphs = [p for p in paragraphs if not _TOPIC_LINE_RE.match(p)]
    hook, lead_rest = _hook_and_rest(paragraphs[0]) if paragraphs else ("", "")
    # 首段余文并入内容卡流：钩子句之外的文字不得静默丢失（对齐文章路径导语处理）
    rest_paras = ([lead_rest] if lead_rest else []) + paragraphs[1:]
    return title, hook, _group_paragraphs(rest_paras), tags, paragraphs


def _group_paragraphs(paragraphs: list[str]) -> list[Card]:
    """每 1-2 段成一内容卡：短段两两成卡；超长段（>LONG_PARAGRAPH_CHARS）独占。"""
    cards: list[Card] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            cards.append((_card_title(buf[0]), list(buf), ""))
            buf.clear()

    for para in paragraphs:
        if len(para) > LONG_PARAGRAPH_CHARS:
            flush()  # 前面攒的短段先成卡，超长段独占一卡
            cards.append((_card_title(para), [para], ""))
        else:
            buf.append(para)
            if len(buf) == 2:
                flush()
    flush()
    return cards


# ── 路径②：文章（h2 切节）──────────────────────────────────


def _from_article(
    article: ParsedArticle, fallback_stem: str
) -> tuple[str, str, list[Card], list[str], list[str]]:
    """h2 小节每节一卡（>6 节合并相邻短节）；首段做封面副标；tags 收尾。"""
    title = str(article.metadata.get("title") or fallback_stem)
    tags = _normalize_tags(article.metadata.get("tags"))

    lead: list[str] = []
    lead_code: list[str] = []
    sections: list[Card] = []
    cur_title: str | None = None
    cur_paras: list[str] = []
    cur_code: list[str] = []

    def close_section() -> None:
        nonlocal cur_title, cur_paras, cur_code
        if cur_title is not None:
            sections.append((cur_title, cur_paras, "\n\n".join(cur_code)))
        elif cur_code:
            # 导语区（首 h2 之前）的代码无节可归：并入 lead_code，不静默丢弃
            lead_code.extend(cur_code)
        cur_title, cur_paras, cur_code = None, [], []

    for block in article.blocks:
        kind = block.get("type")
        if kind == "heading":
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            if block.get("level", 2) <= 2:
                close_section()  # h1/h2 开新节（标题主要在 frontmatter，h1 少见兜底）
                cur_title = text
            else:
                # h3+ 不切卡：标题文本并入当前节正文（导语区并入 lead），不丢弃
                (cur_paras if cur_title is not None else lead).append(text)
        elif kind == "paragraph":
            text = str(block.get("text", "")).strip()
            if not text:
                continue
            if cur_title is None:
                lead.append(text)
            else:
                cur_paras.append(text)
        elif kind == "list":
            lines = "\n".join(f"· {item}" for item in block.get("items", []))
            if lines:
                (cur_paras if cur_title is not None else lead).append(lines)
        elif kind == "quote":
            text = str(block.get("text", "")).strip()
            if text:
                (cur_paras if cur_title is not None else lead).append(text)
        elif kind == "code":
            cur_code.append(str(block.get("code", "")).rstrip())
        # image/table/hr 不入卡：卡组纯文本形态，且零外部资源红线不嵌图
    close_section()

    sections = _merge_sections(sections)
    # 首段做封面副标；导语其余段落/导语区代码并成一卡，避免内容静默丢失
    hook = lead[0] if lead else ""
    lead_rest = lead[1:]
    if lead_rest or lead_code:
        code = "\n\n".join(lead_code)
        title_src = lead_rest[0] if lead_rest else lead_code[0]
        lead_cards = [(_card_title(title_src), lead_rest, code)]
    else:
        lead_cards = []
    pool = lead + lead_code + [p for _, ps, _ in sections for p in ps]
    return title, hook, lead_cards + sections, tags, pool


def _merge_sections(sections: list[Card]) -> list[Card]:
    """>6 节合并相邻短节：反复合并内容量最小的相邻对，收到 ≤6 节。"""

    def weight(card: Card) -> int:
        return sum(len(p) for p in card[1]) + len(card[2])

    merged = list(sections)
    while len(merged) > MERGE_SECTIONS_ABOVE:
        best = min(
            range(len(merged) - 1),
            key=lambda i: weight(merged[i]) + weight(merged[i + 1]),
        )
        a, b = merged[best], merged[best + 1]
        code = "\n\n".join(c for c in (a[2], b[2]) if c)
        merged[best : best + 2] = [(a[0], a[1] + b[1], code)]
    return merged


# ── 两路径共用：卡数边界 3-10 / 标签 / 首句─────────────────


def _pad_cards(cards: list[Card], pool: list[str], title: str) -> list[Card]:
    """卡数 <3（即零内容卡）时复制内容段补足一张内容卡，总卡数达到 3。

    pool 为两路径收集的全部正文段（首段已被封面副标消费的也在内）——
    「复制内容段」即取其第一段；全文无段时退回标题。
    """
    if cards:
        return cards
    text = pool[0] if pool else (title or "（无正文）")
    return [(_card_title(text), [text], "")]


def _merge_tail(cards: list[Card]) -> list[Card]:
    """卡数 >10 时合并尾部：内容卡收到 8 张（+封面+结尾=10），尾卡并进末位保留卡。"""
    limit = MAX_CARDS - 2
    if len(cards) <= limit:
        return cards
    head, tail = cards[: limit - 1], cards[limit - 1 :]
    paragraphs = [p for _, ps, _ in tail for p in ps]
    code = "\n\n".join(c for _, _, c in tail if c)
    return head + [(tail[0][0], paragraphs, code)]


def _first_sentence(text: str) -> str:
    """取首个完整句（到首个句末标点）；无句末标点则取整段。"""
    return _hook_and_rest(text)[0]


def _hook_and_rest(text: str) -> tuple[str, str]:
    """首段 →（钩子句, 段内余文）：钩子口径同 _first_sentence（首个完整句，
    无句末标点取首行）；余文=首段其余全部文字，供并入后续内容卡——
    钩子句之外的段落内容不得静默丢失（对齐文章路径的导语保真处理）。
    """
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    m = _SENTENCE_END_RE.search(first_line)
    if m:
        return first_line[: m.end()].strip(), stripped[m.end() :].lstrip()
    return first_line.strip(), "\n".join(stripped.splitlines()[1:]).strip()


def _card_title(paragraph: str) -> str:
    """卡标题 = 段首句截前 TITLE_MAX_CHARS 字。"""
    sentence = _first_sentence(paragraph)
    if not sentence:
        sentence = paragraph.strip()[:TITLE_MAX_CHARS]
    return sentence[:TITLE_MAX_CHARS]


def _normalize_tags(raw: Any) -> list[str]:
    """frontmatter.tags 归一为字符串列表（单个字符串视为一个标签；语义同 pack）。"""
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, (list, tuple)):
        return [str(t) for t in raw if str(t).strip()]
    return []
