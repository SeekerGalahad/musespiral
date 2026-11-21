"""F70-T1 元素白名单与文档模型校验（core/visual/canvas/model.py）。

覆盖：白名单构造期拒收（文案含「元素白名单」）、渲染前复验拦构造后篡改、
颜色/href/路径数据/透明度执法、SPECS 规格表对既有常量的复用断言。
"""
from __future__ import annotations

import pytest

from musespiral.core.visual.canvas.model import (
    ELEMENT_WHITELIST,
    SPECS,
    CanvasDoc,
    Element,
    TextBlock,
    spec_size,
    validate_doc,
)
from musespiral.platforms.profiles import PLIMITS
from musespiral.core.visual.cards.templates import RATIOS


# ── F70-T1 元素白名单 ─────────────────────────────────────────


def test_whitelist_membership_exact() -> None:
    assert ELEMENT_WHITELIST == ("text", "rect", "circle", "line", "path", "image", "g")


def test_f70t1_reject_script_kind_at_construction() -> None:
    """F70-T1：构造 `<script>` 伪子元素 → ValueError 文案含「元素白名单」。"""
    with pytest.raises(ValueError, match="元素白名单"):
        Element("script")


@pytest.mark.parametrize("kind", ["svg", "iframe", "foreignObject", "style", "", "TEXT"])
def test_f70t1_reject_non_whitelist_kinds(kind: str) -> None:
    with pytest.raises(ValueError, match="元素白名单"):
        Element(kind)


def test_f70t1_validate_doc_rejects_post_mutation() -> None:
    """构造后篡改 kind（绕过 post_init）→ validate_doc 第二道门拦截。"""
    e = Element("rect", x=0, y=0, w=10, h=10, fill="#ff0000")
    doc = CanvasDoc(100, 100, elements=[e])
    e.kind = "script"  # 篡改（dataclass 无 frozen）
    with pytest.raises(ValueError, match="元素白名单"):
        validate_doc(doc)


def test_children_only_on_g() -> None:
    with pytest.raises(ValueError, match="g"):
        Element("rect", children=[Element("circle", r=1)])


def test_textblock_only_on_text_and_required() -> None:
    with pytest.raises(ValueError, match="TextBlock"):
        Element("rect", text=TextBlock("x"))
    with pytest.raises(ValueError, match="TextBlock"):
        Element("text", x=0, y=0, w=10)  # text=None


def test_g_recursive_validation_of_mutated_child() -> None:
    child = Element("circle", r=5)
    g = Element("g", children=[child])
    doc = CanvasDoc(100, 100, elements=[g])
    child.kind = "iframe"
    with pytest.raises(ValueError, match="元素白名单"):
        validate_doc(doc)


# ── 第二道门标量覆盖（构造后篡改的标量一律拦）──────────────
#
# 设计稿载入路径只过 verify_svg 与 params schema，**不经 TextBlock 重建**——
# validate_doc 是该路径的唯一防线。漏检的标量会以未捕获 TypeError/KeyError
# 崩在序列化层（500 面），而非设计承诺的可读 ValueError；若某标量走 f-string
# 直拼，还存在属性逃逸风险（`48" onload="alert(1)`）。故第二道门必须覆盖
# **全部进入序列化的标量**，不只是 kind/color/d/href。


def _mutated_text_doc() -> CanvasDoc:
    return CanvasDoc(100, 100, elements=[
        Element("text", x=10, y=10, w=80, text=TextBlock("hi", font_size=48, color="#111111"))
    ])


@pytest.mark.parametrize("attr,value", [
    ("font_size", '48" onload="alert(1)'),
    ("align", 'x" onload="alert(1)'),
    ("line_height", '1.4" onload="alert(1)'),
    ("max_width", '80" onload="alert(1)'),
    ("color", 'red" onload="alert(1)'),
])
def test_f70t2_validate_doc_rejects_mutated_textblock_scalars(attr: str, value: str) -> None:
    """篡改 TextBlock 标量 → validate_doc 拦（可读 ValueError，非 500 面崩溃）。"""
    doc = _mutated_text_doc()
    setattr(doc.elements[0].text, attr, value)
    with pytest.raises(ValueError):
        validate_doc(doc)


@pytest.mark.parametrize("attr,value", [
    ("x", '1" onload="alert(1)'),
    ("y", '1" onload="alert(1)'),
    ("w", '1" onload="alert(1)'),
    ("h", '1" onload="alert(1)'),
    ("cx", '1" onload="alert(1)'),
    ("r", '1" onload="alert(1)'),
    ("stroke_width", '1" onload="alert(1)'),
    ("opacity", '1" onload="alert(1)'),
    ("dashoffset", '1" onload="alert(1)'),
    ("rotate", '5" onload="alert(1)'),
    ("dasharray", '1" onload="alert(1)'),
])
def test_f70t2_validate_doc_rejects_mutated_element_scalars(attr: str, value: str) -> None:
    """篡改元素几何/样式标量 → validate_doc 拦（全部进入序列化，须为有限数值）。"""
    doc = CanvasDoc(100, 100, elements=[Element("rect", x=0, y=0, w=10, h=10)])
    setattr(doc.elements[0], attr, value)
    with pytest.raises(ValueError):
        validate_doc(doc)


def test_f70t2_validate_doc_rejects_non_finite_numbers() -> None:
    """NaN / Infinity 也是非法数值（序列化会产生 nan/inf 字面量）。"""
    doc = CanvasDoc(100, 100, elements=[Element("rect", x=0, y=0, w=10, h=10)])
    doc.elements[0].x = float("nan")
    with pytest.raises(ValueError):
        validate_doc(doc)
    doc2 = CanvasDoc(100, 100, elements=[Element("rect", x=0, y=0, w=10, h=10)])
    doc2.elements[0].x = float("inf")
    with pytest.raises(ValueError):
        validate_doc(doc2)


def test_f70t2_validate_doc_rejects_mutated_textblock_missing() -> None:
    """篡改 text 元素的 TextBlock 为 None → 拦（序列化会 AttributeError）。"""
    doc = _mutated_text_doc()
    doc.elements[0].text = None
    with pytest.raises(ValueError, match="TextBlock"):
        validate_doc(doc)


# ── 颜色 / 对齐 / 透明度执法 ──────────────────────────────────


@pytest.mark.parametrize("bad", ["red", "2563eb", "#12345", "#gggggg", 42, "javascript:x"])
def test_invalid_color_rejected(bad) -> None:
    with pytest.raises(ValueError, match="非法颜色"):
        Element("rect", fill=bad)


def test_valid_color_forms_accepted() -> None:
    assert Element("rect", fill="#abc").fill == "#abc"
    assert Element("rect", fill="#2563eb").fill == "#2563eb"
    assert Element("rect", fill="#2563eb80").fill == "#2563eb80"


def test_textblock_bad_align_and_font() -> None:
    with pytest.raises(ValueError, match="align"):
        TextBlock("x", align="middle")
    with pytest.raises(ValueError, match="font_size"):
        TextBlock("x", font_size=0)
    with pytest.raises(ValueError, match="颜色"):
        TextBlock("x", color="hotpink")


def test_opacity_range() -> None:
    with pytest.raises(ValueError, match="opacity"):
        Element("circle", r=1, opacity=1.5)
    with pytest.raises(ValueError, match="opacity"):
        Element("circle", r=1, opacity=-0.1)
    Element("circle", r=1, opacity=0.0)  # 边界合法


def test_dasharray_must_be_numeric() -> None:
    with pytest.raises(ValueError, match="dasharray"):
        Element("line", x1=0, y1=0, x2=10, y2=10, dasharray="a b")
    Element("line", x1=0, y1=0, x2=10, y2=10, dasharray="6.28 18.85")


# ── href 执法（光栅不触网前置）───────────────────────────────


@pytest.mark.parametrize("bad", [
    "http://evil.com/a.png",
    "https://evil.com/a.png",
    "//evil.com/a.png",
    "ftp://x/y.png",
    "javascript:alert(1)",
    'data:text/html,<script>',
])
def test_href_network_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        Element("image", href=bad)


def test_href_datauri_and_local_accepted() -> None:
    Element("image", href="data:image/png;base64,aGVsbG8=")
    Element("image", href="cards/article-01.png")
    Element("image", href="sub/dir/img-2.webp")


# ── 路径数据字符集执法 ────────────────────────────────────────


def test_path_data_charset() -> None:
    with pytest.raises(ValueError, match="路径"):
        Element("path", d='M0 0 L10 10"><script')
    with pytest.raises(ValueError, match="路径"):
        Element("path", d="M0 0 L100 100; alert(1)")
    Element("path", d="M 0 0 L 100 100 Z")


# ── SPECS 规格表（唯一事实源复用断言）────────────────────────


def test_specs_exact_values() -> None:
    assert SPECS["mp"] == (900, 383)
    assert SPECS["xhs-3x4"] == (1242, 1656)
    assert SPECS["xhs-4x5"] == (1242, 1553)
    assert SPECS["square"] == (1080, 1080)
    assert SPECS["wide16x9"] == (1242, 700)


def test_specs_reuse_existing_constants() -> None:
    """沿用既有常量不复制数值逻辑（§1.3）：与 PLIMITS/RATIOS 恒等。"""
    assert SPECS["mp"] == (PLIMITS["mp"]["cover_w"], PLIMITS["mp"]["cover_h"])
    assert SPECS["xhs-3x4"] == RATIOS["3:4"]
    assert SPECS["xhs-4x5"] == RATIOS["4:5"]


def test_spec_size_unknown() -> None:
    assert spec_size("mp") == (900, 383)
    with pytest.raises(ValueError, match="未知规格"):
        spec_size("a4")


def test_doc_dimensions_validated() -> None:
    with pytest.raises(ValueError, match="width"):
        CanvasDoc(0, 100)
    with pytest.raises(ValueError, match="height"):
        CanvasDoc(100, -5)
    with pytest.raises(ValueError, match="background"):
        CanvasDoc(100, 100, background="white")


def test_doc_elements_type_checked() -> None:
    with pytest.raises(ValueError, match="Element"):
        CanvasDoc(100, 100, elements=["<svg>"])
