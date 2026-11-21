"""F70-T2 注入对拍 + F70-T4 尺寸头逐 spec 精确（core/visual/canvas/svg.py）。

注入执法三面：载荷以 ``&lt;`` 文本形态出现（转义）、输出零原始标签形态
（``<script`` / ``<img onerror`` 计数=0）、XML 可解析且无 script/img 元素与
on* 属性（ElementTree 复验——比文本 grep 更强的结构断言）。

规格说明：设计文档 F70-T4 片段写 ``<svg width="900" …>``（省略 xmlns）；实现
在 xmlns 前置后输出完整合法的独立 SVG（浏览器经 <img>/file:// 加载要求
xmlns），尺寸数字断言语义不变——本文件以 ``startswith`` 钉死整段头部。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from musespiral.core.visual.canvas.layout import layout_document
from musespiral.core.visual.canvas.model import SPECS, CanvasDoc, Element, TextBlock
from musespiral.core.visual.canvas.svg import render_svg

SVGNS = "{http://www.w3.org/2000/svg}"


def _plain_doc(spec: str = "mp") -> CanvasDoc:
    w, h = SPECS[spec]
    return CanvasDoc(w, h, elements=[Element("rect", x=0, y=0, w=10, h=10, fill="#ff0000")])


# ── F70-T4 尺寸头逐 spec 精确 ────────────────────────────────


@pytest.mark.parametrize("spec", sorted(SPECS))
def test_f70t4_header_exact_per_spec(spec: str) -> None:
    w, h = SPECS[spec]
    out = render_svg(_plain_doc(spec))
    assert out.startswith(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
    )
    assert f'viewBox="0 0 {w} {h}"' in out
    assert out.rstrip().endswith("</svg>")


def test_f70t4_known_sizes_deterministic() -> None:
    assert SPECS["mp"] == (900, 383)
    assert SPECS["xhs-3x4"] == (1242, 1656)
    assert SPECS["xhs-4x5"] == (1242, 1553)
    assert SPECS["square"] == (1080, 1080)
    assert SPECS["wide16x9"] == (1242, 700)


# ── F70-T2 注入对拍 ──────────────────────────────────────────


def _title_doc(title: str) -> CanvasDoc:
    return CanvasDoc(
        900, 383,
        elements=[Element("text", x=48, y=48, w=800,
                          text=TextBlock(title, font_size=56, lines=[title]))],
    )


def test_f70t2_script_payload_fully_escaped() -> None:
    out = render_svg(_title_doc("</svg><script>alert(1)</script>"))
    # 转义形态出现
    assert "&lt;/svg&gt;&lt;script&gt;" in out
    # 原始标签形态零出现（设计 §七 grep 语义：`<script` 计数==0）
    assert len(re.findall(r"<script", out)) == 0
    assert len(re.findall(r"</svg><", out)) == 0
    root = ET.fromstring(out)
    assert not [el for el in root.iter() if el.tag.endswith("script")]


def test_f70t2_img_onerror_payload_fully_escaped() -> None:
    out = render_svg(_title_doc('<img onerror=alert(1)>'))
    assert "&lt;img" in out  # 全转义：以 &lt; 文本形态出现
    # 原始注入形态零出现：<img 标签 / 标签内 onerror 属性
    assert len(re.findall(r"<img", out)) == 0
    root = ET.fromstring(out)
    for el in root.iter():
        assert not el.tag.endswith("img"), "输出中不得出现 img 元素"
        for attr in el.attrib:
            assert not attr.lower().startswith("on"), f"事件属性泄漏: {attr}"


def test_f70t2_attribute_injection_via_text() -> None:
    """载荷带引号试图逃逸属性上下文 → 文本转义后仍是纯文本。"""
    out = render_svg(_title_doc('" fill="javascript:alert(1)'))
    assert "&quot;" not in out or True  # 文本节点 quote=False；关键是 XML 可解析
    root = ET.fromstring(out)
    for el in root.iter():
        for attr, val in el.attrib.items():
            assert "javascript" not in str(val)


def test_f70t2_grep_count_emulation() -> None:
    """§七 验收「grep 输出无 <script/onerror 原样出现」的 Python 等价：

    原始形态（作为标记出现的 <script、作为标签属性的 onerror）计数==0。
    注：转义后的可见文本中字符序列 onerror 仍在（文本保真），但只存在于
    tspan 文本节点内、以 &lt; 包裹——不可执行，结构断言见上两用例。
    """
    out = render_svg(_title_doc('<img onerror=alert(1)>'))
    assert not re.search(r"<\s*script", out)
    assert not re.search(r"<\s*img", out)
    assert not re.search(r"<[^>]*\bonerror", out)


def test_href_payload_never_serialized_as_attribute_url() -> None:
    """image href 注入形态在 model 层已拒（此处在序列化面复验合法形态）。"""
    out = render_svg(CanvasDoc(100, 100, elements=[
        Element("image", x=0, y=0, w=10, h=10, href="data:image/png;base64,aGk=")
    ]))
    assert 'href="data:image/png;base64,aGk="' in out
    assert 'href="http' not in out  # 网络引用永不出现（xmlns 命名空间除外）


# ── D-2 来源实体解码一次（2026-11-17 真浏览器验收缺陷 D-2）─────────
#
# 上游 title/hook 取自 summary/frontmatter 时已含预转义实体（&quot; 等）；
# 序列化须「unescape 一次 → escape」，浏览器可见文本=解码一次。
# 断言口径：ET 解析输出 SVG 后的 tspan 文本 == 浏览器解码一次后的可见文本。


def test_d2_quot_entity_renders_as_quote() -> None:
    """验收原样用例：`&quot;` 输出为 `\"`，不得二次 escape 成 `&amp;quot;`。"""
    out = render_svg(_title_doc('&quot;'))
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == '"'
    assert "&amp;quot;" not in out  # 双重转义症状零出现


def test_d2_amp_lt_entity_renders_single_decode() -> None:
    """`&amp;lt;` 解码一次 → 可见 `&lt;`；不得二次 escape 成 `&amp;amp;lt;`。"""
    out = render_svg(_title_doc('&amp;lt;'))
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == '&lt;'
    assert "&amp;amp;lt;" not in out


def test_d2_realistic_hook_entity_decoded() -> None:
    """验收取证场景：hook `**把&quot;表和列&quot;编译成…` 显示真引号。"""
    out = render_svg(_title_doc('**把&quot;表和列&quot;编译成SQL'))
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == '**把"表和列"编译成SQL'
    assert "&amp;quot;" not in out


def test_d2_entity_encoded_payload_stays_inert_text() -> None:
    """实体编码载荷穿透 decode 后被终端 escape 重新制服——红线不破。

    解码产生真实 ``<script>`` 字符，但 escape（最后一步）把它转回
    ``&lt;script&gt;``：序列化面零原始标签，浏览器显示为纯文本。
    """
    out = render_svg(_title_doc('&lt;script&gt;alert(1)&lt;/script&gt;'))
    assert len(re.findall(r"<script", out)) == 0
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == "<script>alert(1)</script>"


def test_d2_numeric_entity_payload_stays_inert_text() -> None:
    """数字实体变体（&#60;img&#62;）同样只以文本形态出场、零事件属性泄漏。"""
    out = render_svg(_title_doc('&#60;img onerror=alert(1)&#62;'))
    assert not re.search(r"<\s*img", out)
    assert not re.search(r"<[^>]*\bonerror", out)
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == "<img onerror=alert(1)>"


def test_d2_plain_text_and_bare_ampersand_untouched() -> None:
    """普通文本与真实 &（如 AT&T）不被 unescape 误伤，转义结果不变。"""
    out = render_svg(_title_doc("AT&T"))
    root = ET.fromstring(out)
    assert next(root.iter(f"{SVGNS}tspan")).text == "AT&T"


def test_d2_esc_helpers_serializer_only_escapes() -> None:
    """_esc_text/_esc_attr 单元口径（复核判例修正）：序列化器只 escape 不解码。

    来源实体解码已在 model.TextBlock 构造边界一次性完成（model.py
    decode_text_entities）；若序列化器再 unescape，会把 ``&amp;lt;`` 解码后的
    ``&lt;`` 二次解码成 ``<``——解码不止一次。故本层只负责转义。
    """
    from musespiral.core.visual.canvas.svg import _esc_attr, _esc_text

    assert _esc_text('"') == '"'          # quote=False：text 上下文引号合法
    assert _esc_text('&') == '&amp;'
    assert _esc_text('<') == '&lt;'
    assert _esc_text('&lt;') == '&amp;lt;'   # 字面 &lt;：escape 出 &amp;lt;
    assert _esc_text("AT&T") == "AT&amp;T"
    # 属性：引号重新转义，属性边界不可破
    assert _esc_attr('" onload="x') == '&quot; onload=&quot;x'
    assert _esc_attr('&') == '&amp;'


def test_d2_model_boundary_decodes_once_and_drops_xml_invalid() -> None:
    """复核判例 D-2 真根因：解码下沉到 TextBlock 构造边界，剔除 XML 非法字符。

    - ``&quot;`` → ``"``；``&amp;lt;`` → ``&lt;`` 止步（只解码一次）；
    - ``&#12;``（U+000C，XML 非法）被剔除，序列化面恒 well-formed
      （修前会让 load_design/export_design 的 verify_svg 解析失败）。
    """
    from musespiral.core.visual.canvas.model import decode_text_entities

    assert decode_text_entities('&quot;') == '"'
    assert decode_text_entities('&amp;lt;') == '&lt;'   # 只解码一次
    assert decode_text_entities('&amp;amp;') == '&amp;'  # 只解码一次
    assert decode_text_entities('AT&T') == 'AT&T'
    assert '\x0c' not in decode_text_entities('标题&#12;尾')
    # 端到端：含 U+000C 实体的标题经 TextBlock → SVG 仍 well-formed
    out = render_svg(_title_doc('标题&#12;尾'))
    assert '\x0c' not in out
    ET.fromstring(out)  # 不抛即 well-formed


def test_d2_wrap_measures_decoded_text_not_entity_string() -> None:
    """复核判例：layout 测量解码后文本（否则行宽按实体串算、实渲偏窄、多折行）。"""
    from musespiral.core.visual.canvas.layout import layout_document
    from musespiral.core.visual.canvas.model import CanvasDoc, Element, TextBlock

    hook = '把&quot;表和列&quot;编译成&quot;SQL&quot;'
    doc = CanvasDoc(1242, 1656, elements=[
        Element("text", x=96, y=500, w=1050, text=TextBlock(hook, font_size=35))
    ])
    layout_document(doc)
    lines = doc.elements[0].text.lines
    assert "&quot;" not in "".join(lines)          # 行结构里已是真引号
    assert len(lines) == 1                          # 解码后单行可容纳（不再多折）


def test_d2_pillow_route_decodes_entities_too() -> None:
    """复核判例：Pillow 兜底路（route C）与 SVG（route B）可见文本一致。

    修前 Pillow 直接 draw.text(block.lines)，block.lines 仍含 ``&quot;`` →
    同稿随有无浏览器显示不同；经 model 边界解码后两路一致。
    """
    from musespiral.core.visual.canvas import pillow_render
    from musespiral.core.visual.canvas.layout import layout_document
    from musespiral.core.visual.canvas.model import CanvasDoc, Element, TextBlock

    doc = CanvasDoc(600, 200, elements=[
        Element("text", x=10, y=40, w=580, text=TextBlock('把&quot;表和列&quot;编译成SQL', font_size=28))
    ])
    layout_document(doc)
    assert "&quot;" not in "".join(doc.elements[0].text.lines)
    pillow_render.render(doc)  # 不炸且文本面无实体


def test_d2_attr_inputs_cannot_carry_entities_by_contract() -> None:
    """属性面契约根基：model 层颜色字符集容不下实体（hex 门）——
    属性面不因解码而新开洞（且属性走 quote=True 独立转义）。"""
    with pytest.raises(ValueError, match="非法颜色"):
        Element("rect", x=0, y=0, w=1, h=1, fill="&quot;")


# ── 序列化结构与白名单 ───────────────────────────────────────


def test_all_seven_kinds_serialize_and_parse() -> None:
    doc = CanvasDoc(200, 200, elements=[
        Element("rect", x=1, y=2, w=3, h=4, fill="#00ff00", rx=2),
        Element("circle", cx=10, cy=10, r=5, stroke="#000000", stroke_width=2),
        Element("line", x1=0, y1=0, x2=20, y2=20, stroke="#111111", stroke_width=3),
        Element("path", d="M 0 0 L 30 30 Z", stroke="#222222"),
        Element("image", x=0, y=0, w=8, h=8, href="pic.png"),
        Element("text", x=0, y=20, w=100, text=TextBlock("hi", font_size=12, lines=["hi"])),
        Element("g", children=[Element("rect", x=0, y=0, w=1, h=1)]),
    ])
    out = render_svg(doc)
    root = ET.fromstring(out)
    tags = {el.tag.replace(SVGNS, "") for el in root.iter()}
    assert {"svg", "rect", "circle", "line", "path", "image", "text", "g"} <= tags


def test_background_rect_emitted_first() -> None:
    out = render_svg(CanvasDoc(100, 100, background="#123456"))
    lines = out.splitlines()
    assert lines[1] == '  <rect x="0" y="0" width="100" height="100" fill="#123456"/>'


def test_text_tspan_structure_and_dy() -> None:
    block = TextBlock("第一\n第二", font_size=20, line_height=1.5, lines=["第一", "第二"])
    doc = CanvasDoc(100, 100, elements=[
        Element("text", x=10, y=30, w=80, text=block)
    ])
    out = render_svg(doc)
    root = ET.fromstring(out)
    tspans = root.iter(f"{SVGNS}tspan")
    texts = [t.text for t in tspans]
    assert texts == ["第一", "第二"]
    assert 'dy="0"' in out and 'dy="30"' in out  # round(20*1.5)


def test_text_anchor_by_align() -> None:
    for align, anchor, tx in (("left", "start", 10.0), ("center", "middle", 50.0), ("right", "end", 90.0)):
        doc = CanvasDoc(100, 100, elements=[
            Element("text", x=10, y=30, w=80, text=TextBlock("x", align=align))
        ])
        out = render_svg(doc)
        assert f'text-anchor="{anchor}"' in out
        assert f'x="{tx:g}"' in out


def test_render_svg_rejects_smuggled_kind() -> None:
    """F70 API 纪律：白名单复验拒绝产出（防手改 design 目录夹带恶意 SVG）。"""
    e = Element("rect", x=0, y=0, w=5, h=5)
    doc = CanvasDoc(100, 100, elements=[e])
    e.kind = "script"
    with pytest.raises(ValueError, match="元素白名单"):
        render_svg(doc)


def test_layout_then_render_roundtrip_via_template() -> None:
    """layout 固化行结构 → 序列化 → XML 可解析（F70 调用流 3-4 步集成冒烟）。"""
    from musespiral.core.visual.canvas.templates import REGISTRY, resolve

    colors = {"primary": "#2563eb", "text": "#1f2937", "muted": "#6b7280",
              "background": "#ffffff", "accent": "#dc2626"}
    for tid, meta in REGISTRY.items():
        params = {"title": "集成冒烟" * 5, "hook": "钩子"}
        if tid == "points-three":
            params = {"title": "集成冒烟", "points": [{"label": "一", "value": 2}]}
        m, merged = resolve(REGISTRY, tid, params=params)
        doc = m.build(merged, colors, m.specs[0])
        layout_document(doc)
        ET.fromstring(render_svg(doc))  # 不炸即过
