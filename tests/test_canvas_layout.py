"""F70-T3 测量分行黄金三态 + layout_document 回填（core/visual/canvas/layout.py）。

三态：CJK 无空格逐字累积 / ASCII 词边界 / 显式 \\n 恒断行——每行
``font.getlength(line) <= max_width`` 是硬断言（超宽单词回退逐字硬拆后同样成立）。
"""
from __future__ import annotations

import pytest

from musespiral.core.visual.canvas.layout import (
    _DEFAULT_MARGIN,
    layout_document,
    load_font,
    relayout_vertical,
    wrap_text,
)
from musespiral.core.visual.canvas.model import CanvasDoc, Element, TextBlock

FONT = load_font(56)


def _assert_widths(lines: list[str], max_width: int, font=None) -> None:
    font = font or FONT
    for ln in lines:
        if ln:
            got = font.getlength(ln)
            assert got <= max_width, f"行超宽: {ln!r} ({got}>{max_width})"


# ── F70-T3 黄金三态 ──────────────────────────────────────────


def test_f70t3_cjk_30_chars_no_space() -> None:
    """态一：CJK 无空格 30 字 → 多行，每行 ≤ max_width，字符零丢失。"""
    text = "数" * 30
    max_w = 300
    lines = wrap_text(text, FONT, max_w)
    _assert_widths(lines, max_w)
    assert len(lines) > 1
    assert "".join(lines) == text  # 逐字累积零丢失


def test_f70t3_ascii_word_boundary() -> None:
    """态二：ASCII 词边界——词不拆散（断行只发生在空格处）。"""
    text = "the quick brown fox jumps over the lazy dog again and again"
    words = set(text.split())
    max_w = 260
    lines = wrap_text(text, FONT, max_w)
    _assert_widths(lines, max_w)
    tokens = [t for ln in lines for t in ln.split()]
    assert all(t in words for t in tokens)  # 词形零破坏（无半个词）
    assert " ".join(tokens) == text  # 词序零破坏
    assert len(lines) > 1


def test_f70t3_ascii_no_midword_break() -> None:
    """词边界专项：长词各自成行，不被拆成半词（宽度按最长词自适应）。"""
    text = "visualization infrastructure observability"
    words = set(text.split())
    font = load_font(20)
    max_w = round(max(font.getlength(w) for w in words) * 1.3)  # 单词必可整行容纳
    lines = wrap_text(text, font, max_w)
    _assert_widths(lines, max_w, font)
    tokens = [t for ln in lines for t in ln.split()]
    assert all(t in words for t in tokens)
    assert " ".join(tokens) == text
    assert len(lines) >= 3  # 三个长词不可能同行


def test_f70t3_explicit_newline_always_breaks() -> None:
    """态三：显式 \\n 恒为断行点（宽度充裕时不产生额外断行）。"""
    text = "第一行标题\nsecond line\n\n尾行"
    lines = wrap_text(text, FONT, 4000)
    assert lines == ["第一行标题", "second line", "", "尾行"]


def test_explicit_newline_breaks_even_when_narrow() -> None:
    """窄宽度下 \\n 仍是断行点（先按 \\n 分段再段内硬拆，两段永不混行）。"""
    lines = wrap_text("aaaa\nbbbb", FONT, 40)
    _assert_widths(lines, 40)
    a_idx = [i for i, ln in enumerate(lines) if ln and set(ln) <= {"a"}]
    b_idx = [i for i, ln in enumerate(lines) if ln and set(ln) <= {"b"}]
    assert a_idx and b_idx
    assert max(a_idx) < min(b_idx)  # \n 边界：a 段全部先于 b 段
    assert all(set(ln) <= {"a", "b"} or not ln for ln in lines)  # 无混行


def test_empty_text_yields_no_lines() -> None:
    assert wrap_text("", FONT, 100) == []


def test_overlong_single_word_hard_split() -> None:
    """单词宽于行宽 → 逐字硬拆（不溢出优先于词边界）。"""
    lines = wrap_text("internationalization", FONT, 60)
    _assert_widths(lines, 60)
    assert "".join(lines) == "internationalization"


def test_space_run_collapsed_at_breaks() -> None:
    lines = wrap_text("word one      two", FONT, 1000)
    assert lines == ["word one      two"]  # 行内空格原样保留
    lines = wrap_text("aaaa bb   cccc dd", FONT, 120)
    _assert_widths(lines, 120)


# ── layout_document 回填 ─────────────────────────────────────


def _doc_with_text(block: TextBlock, w: float = 400.0) -> CanvasDoc:
    return CanvasDoc(
        900, 383, elements=[Element("text", x=48, y=48, w=w, text=block)]
    )


def test_layout_document_fills_lines() -> None:
    block = TextBlock("数" * 30, font_size=56)
    doc = _doc_with_text(block, w=300)
    assert block.lines is None
    layout_document(doc)
    assert block.lines and len(block.lines) > 1
    font = load_font(56)
    for ln in block.lines:
        assert font.getlength(ln) <= 300


def test_layout_document_idempotent_and_force() -> None:
    block = TextBlock("abc def", font_size=24)
    doc = _doc_with_text(block)
    layout_document(doc)
    first = list(block.lines or [])
    block.text = "completely different text now"
    layout_document(doc)  # 已回填 → 跳过
    assert block.lines == first
    layout_document(doc, force=True)  # spec 切换重渲语义
    assert block.lines != first


def test_layout_max_width_priority() -> None:
    """max_width 取值序：block.max_width > 元素 w > 画布宽-2×边距。"""
    # 1) 显式 max_width 胜元素 w
    block = TextBlock("数" * 40, font_size=56, max_width=200)
    doc = _doc_with_text(block, w=800)
    layout_document(doc)
    font = load_font(56)
    assert all(font.getlength(ln) <= 200 for ln in block.lines or [])

    # 2) 元素 w 胜默认边距宽
    block2 = TextBlock("数" * 40, font_size=56)
    doc2 = CanvasDoc(2000, 500, elements=[Element("text", x=48, y=48, w=300, text=block2)])
    layout_document(doc2)
    assert all(font.getlength(ln) <= 300 for ln in block2.lines or [])

    # 3) 无 w → 画布宽 - 2×边距
    block3 = TextBlock("数" * 40, font_size=56)
    doc3 = CanvasDoc(900, 383, elements=[Element("text", x=0, y=0, w=0, text=block3)])
    layout_document(doc3)
    assert all(
        font.getlength(ln) <= 900 - 2 * _DEFAULT_MARGIN for ln in block3.lines or []
    )


def test_layout_walks_g_children() -> None:
    block = TextBlock("数" * 30, font_size=48)
    doc = CanvasDoc(
        900, 383,
        elements=[Element("g", children=[Element("text", x=0, y=0, w=200, text=block)])],
    )
    layout_document(doc)
    assert block.lines and len(block.lines) > 1


def test_layout_skips_non_text_elements() -> None:
    doc = CanvasDoc(100, 100, elements=[Element("rect", x=0, y=0, w=10, h=10)])
    layout_document(doc)  # 不炸不改动
    assert doc.elements[0].kind == "rect"


# ── D-1 垂直重排（relayout_vertical；验收报告 2026-11-17 §二）──


def _d1_doc(title_lines: list[str]) -> tuple[CanvasDoc, Element, Element]:
    """验收取证同形文档：xhs-3x4 标题 y=560 / 副文 y=732（报告 §二 D-1 数值）。

    显式 lines 绕开字体测量——断言不依赖环境字体（5 行=缺陷场景）。
    """
    title = Element("text", x=96, y=560, w=1050,
                    text=TextBlock("长标题", font_size=64, line_height=1.4,
                                   lines=list(title_lines)))
    hook = Element("text", x=96, y=732, w=1050,
                   text=TextBlock("钩子", font_size=35, line_height=1.5))
    return CanvasDoc(1242, 1656, elements=[title, hook]), title, hook


def test_d1_five_line_title_pushes_hook_below() -> None:
    """D-1 修复断言：5 行标题 → 副文 y ≥ 标题 y + 行数×fs×lh（无重叠）。"""
    doc, title, hook = _d1_doc([f"第{i}行" for i in range(1, 6)])
    relayout_vertical(doc)
    height = 5 * 64 * 1.4  # len(lines) × font_size × line_height = 448
    assert hook.y >= title.y + height  # 报告要求的不重叠断言
    assert hook.y == 560 + height  # 最小下压：恰抬到占用底（间距语义保序）


def test_d1_single_line_layout_unchanged() -> None:
    """D-1 回归锚：单行标题 → 标题/副文 y 逐值不变（版式不回归）。"""
    doc, title, hook = _d1_doc(["单行标题"])
    relayout_vertical(doc)
    assert title.y == 560 and hook.y == 732


def test_d1_non_text_elements_not_pushed() -> None:
    """保底约束：rect 不下推、也不作下推依据（版式骨架不动）。"""
    title = Element("text", x=96, y=560, w=1050,
                    text=TextBlock("长标题", font_size=64, line_height=1.4,
                                   lines=["行"] * 5))
    bar = Element("rect", x=96, y=700, w=96, h=6)
    hook = Element("text", x=96, y=732, w=1050,
                   text=TextBlock("钩子", font_size=35))
    doc = CanvasDoc(1242, 1656, elements=[title, bar, hook])
    relayout_vertical(doc)
    assert bar.y == 700  # rect 一律不动
    assert hook.y == 560 + 5 * 64 * 1.4  # rect 夹在中间不改变下压量（不作依据）


def test_d1_cross_column_element_not_pushed() -> None:
    """同列粗判：x 区间无重叠的右侧元素即使 y 落在标题带内也不下推。"""
    title = Element("text", x=96, y=560, w=300,
                    text=TextBlock("左", font_size=64, line_height=1.4,
                                   lines=["行"] * 5))
    side = Element("text", x=700, y=600, w=300,
                   text=TextBlock("右", font_size=32))
    doc = CanvasDoc(1242, 1656, elements=[title, side])
    relayout_vertical(doc)
    assert side.y == 600  # 跨列不下推


def test_d1_later_element_above_not_pushed() -> None:
    """文档序在后但 y 在前元素之上 → 不下推（只处理垂直堆叠向下）。"""
    doc, title, _hook = _d1_doc(["行"] * 5)
    top = Element("text", x=96, y=100, w=1050, text=TextBlock("顶", font_size=32))
    doc.elements.append(top)
    relayout_vertical(doc)
    assert top.y == 100


def test_d1_relayout_idempotent() -> None:
    """重跑稳定：第二次 relayout 不再位移（同一占用底自洽）。"""
    doc, _title, hook = _d1_doc(["行"] * 5)
    relayout_vertical(doc)
    first = hook.y
    relayout_vertical(doc)
    assert hook.y == first


# ── 复核判例回归（2026-11-17 复核）：初版下推过宽误伤两类版式 ──


def test_d1_different_width_column_not_pushed() -> None:
    """同列盒须 x 与 w 逐值相等：w 不同（窄标签 vs 整行标题）不同列盒，不推。

    points-three 标签 w=label_w 窄于标题整行宽——初版按 x 区间重叠误判为同列，
    致三标签被标题齐并（复核判例 finding 1a）。
    """
    title = Element("text", x=96, y=560, w=1050,
                    text=TextBlock("长标题", font_size=56, line_height=1.4,
                                   lines=["行"] * 5))
    label = Element("text", x=96, y=660, w=361,   # 同 x 但 w 远窄
                    text=TextBlock("标签", font_size=24))
    doc = CanvasDoc(1242, 700, elements=[title, label])
    relayout_vertical(doc)
    assert label.y == 660  # 不同列盒 → 保持自身对齐


def test_d1_overflow_target_dropped_not_pushed_offcanvas() -> None:
    """越界保护：下压目标 > 画布高时放弃下推（保留原 y，不制造新越界）。

    mp 封面 383px 高、5 行标题占用底已 > 383——把副文推到 431px 会让原本
    可见的副文整块不可见（复核判例 finding 1c：Pillow muted 像素 613→0）。
    """
    height = 383
    title = Element("text", x=70, y=155, w=760,
                    text=TextBlock("长标题", font_size=41, line_height=1.35,
                                   lines=["行"] * 6))  # 占用底 > 383
    hook = Element("text", x=70, y=221, w=760,
                   text=TextBlock("副文", font_size=29, line_height=1.5))
    doc = CanvasDoc(900, height, elements=[title, hook])
    relayout_vertical(doc)
    assert hook.y == 221  # 下压目标越界 → 不动（副文仍在画布内）
