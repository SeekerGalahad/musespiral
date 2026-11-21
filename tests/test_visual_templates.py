"""F74 模板系统测试（core/visual/canvas/templates.py）——mock theme 色板注入。

- T1 注册表完整性：四内置齐、元数据字段齐、键集=id 集；
- T2 四级合并优先级：default < preset < params < overrides 逐级回退；
- T3 schema 违例可读错误（含槽名与合法枚举清单）；F72-T2 色槽限定同执法。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from musespiral.core.visual.canvas.layout import layout_document, relayout_vertical
from musespiral.core.visual.canvas.model import ELEMENT_WHITELIST, SPECS
from musespiral.core.visual.canvas.svg import render_svg
from musespiral.core.visual.canvas.templates import (
    REGISTRY,
    TemplateMeta,
    resolve,
    validate_params,
)

# mock theme 色板（服务层 load_theme 解析后的形态——本层只收 hex）
COLORS = {
    "primary": "#2563eb",
    "text": "#1f2937",
    "muted": "#6b7280",
    "background": "#ffffff",
    "accent": "#dc2626",
}

BUILTIN_IDS = {"vermilion-slab", "indigo-slab", "xhs-card", "points-three"}


# ── F74-T1 注册表完整性 ──────────────────────────────────────


def test_f74t1_registry_has_four_builtins() -> None:
    assert set(REGISTRY) == BUILTIN_IDS
    assert set(REGISTRY) == {m.id for m in REGISTRY.values()}  # 键集=id 集


def test_f74t1_metadata_complete() -> None:
    for tid, meta in REGISTRY.items():
        assert isinstance(meta, TemplateMeta)
        assert meta.id == tid
        assert meta.kind in ("cover", "social", "infographic")
        assert meta.specs and all(s in SPECS for s in meta.specs)
        assert isinstance(meta.params_schema, dict) and meta.params_schema
        assert meta.slot_colors
        assert callable(meta.build)


def test_f74t1_build_callable_produces_doc() -> None:
    for tid, meta in REGISTRY.items():
        _, params = resolve(REGISTRY, tid, params={"title": f"{tid} 标题"})
        doc = meta.build(params, COLORS, meta.specs[0])
        assert (doc.width, doc.height) == SPECS[meta.specs[0]]
        assert doc.elements
        assert all(e.kind in ELEMENT_WHITELIST for e in doc.elements)


# ── F74-T2 四级合并优先级 ────────────────────────────────────


def test_f74t2_merge_priority_overrides_wins() -> None:
    _, p = resolve(
        REGISTRY, "vermilion-slab",
        preset_params={"align": "right"},
        params={"align": "center"},
        overrides={"align": "left"},
    )
    assert p["align"] == "left"  # overrides 胜出


def test_f74t2_merge_priority_fallback_chain() -> None:
    # 去 overrides → params 胜
    _, p1 = resolve(REGISTRY, "vermilion-slab",
                    preset_params={"align": "right"}, params={"align": "center"})
    assert p1["align"] == "center"
    # 去 params → preset 胜
    _, p2 = resolve(REGISTRY, "vermilion-slab", preset_params={"align": "right"})
    assert p2["align"] == "right"
    # 去 preset → 模板 default（left）
    _, p3 = resolve(REGISTRY, "vermilion-slab")
    assert p3["align"] == "left"
    # 每级四层值各异的完整序
    assert (p3["align"], p2["align"], p1["align"]) == ("left", "right", "center")


def test_f74t2_partial_layer_merge() -> None:
    """各层只覆盖自己给的槽，未给槽逐槽独立取高层值。"""
    _, p = resolve(
        REGISTRY, "xhs-card",
        preset_params={"font_size": "L"},
        params={"align": "center"},
        overrides={"tag": "# 覆写"},
    )
    assert p["font_size"] == "L"
    assert p["align"] == "center"
    assert p["tag"] == "# 覆写"
    assert p["title"] == ""  # 无人给 → default


# ── F74-T3 schema 违例可读错误 ───────────────────────────────


def test_f74t3_enum_violation_readable_error() -> None:
    """align="middle"（枚举外）→ ValueError 文案含槽名 align 与合法枚举清单。"""
    with pytest.raises(ValueError) as ei:
        resolve(REGISTRY, "vermilion-slab", params={"align": "middle"})
    msg = str(ei.value)
    assert "align" in msg
    for legal in ("left", "center", "right"):
        assert legal in msg


def test_f74t3_font_size_enum_violation() -> None:
    with pytest.raises(ValueError, match="font_size.*S/M/L"):
        resolve(REGISTRY, "indigo-slab", params={"font_size": "XXL"})


def test_f72t2_color_slot_restriction() -> None:
    """F72-T2 色槽限定：任意色值（hotpink）拒收，文案含合法色槽清单。"""
    with pytest.raises(ValueError) as ei:
        resolve(REGISTRY, "xhs-card", params={"background_slot": "hotpink"})
    msg = str(ei.value)
    assert "background_slot" in msg and "hotpink" in msg
    for slot in ("primary", "text", "muted", "background", "accent"):
        assert slot in msg


def test_unknown_template_rejected_with_registry_list() -> None:
    with pytest.raises(ValueError, match="注册表"):
        resolve(REGISTRY, "no-such-template")


def test_unknown_slot_rejected() -> None:
    with pytest.raises(ValueError, match="未知槽"):
        resolve(REGISTRY, "vermilion-slab", params={"evil": 1})


def test_validate_params_int_and_required() -> None:
    schema = {
        "count": {"type": "int"},
        "name": {"type": "str", "required": True},
    }
    with pytest.raises(ValueError, match="缺必填槽.*name"):
        validate_params(schema, {"count": 3})
    with pytest.raises(ValueError, match="count.*整数"):
        validate_params(schema, {"name": "x", "count": "3"})


def test_validate_params_list_slot() -> None:
    schema = {"points": {"type": "list", "default": []}}
    ok = validate_params(schema, {"points": [{"label": "a", "value": 1}]})
    assert ok["points"] == [{"label": "a", "value": 1.0}]
    with pytest.raises(ValueError, match="points.*列表"):
        validate_params(schema, {"points": "nope"})
    with pytest.raises(ValueError, match=r"points\[0\]"):
        validate_params(schema, {"points": [{"label": "a"}]})
    with pytest.raises(ValueError, match=r"points\[0\].value"):
        validate_params(schema, {"points": [{"label": "a", "value": -2}]})


# ── 构造与渲染行为 ───────────────────────────────────────────


def test_build_unknown_spec_rejected_and_specs_metadata() -> None:
    """meta.specs 是模板宣称面（渲染任何 SPECS 内规格都合法——一稿多规格卖点）；
    SPECS 外规格（如 a4）在构造期即 KeyError。"""
    assert "mp" not in REGISTRY["xhs-card"].specs  # 社媒卡不宣称封面规格
    _, params = resolve(REGISTRY, "xhs-card")
    doc = REGISTRY["xhs-card"].build(params, COLORS, "mp")  # 宣言外仍可渲（无拉伸）
    assert (doc.width, doc.height) == (900, 383)
    with pytest.raises(KeyError):
        REGISTRY["xhs-card"].build(params, COLORS, "a4")


def test_slab_templates_structurally_distinct() -> None:
    """vermilion=顶部条/accent 色；indigo=底部条/primary 色（同 schema 双形态）。"""
    _, params = resolve(REGISTRY, "vermilion-slab", params={"title": "t"})
    v = REGISTRY["vermilion-slab"].build(params, COLORS, "mp")
    i = REGISTRY["indigo-slab"].build(params, COLORS, "mp")
    v_bar = v.elements[0]
    i_bar = i.elements[0]
    assert v_bar.y < i_bar.y  # vermilion 顶 / indigo 底
    assert v_bar.fill == COLORS["accent"] and i_bar.fill == COLORS["primary"]


def test_font_size_steps_scale_with_spec_width() -> None:
    for step in ("S", "M", "L"):
        _, params = resolve(REGISTRY, "vermilion-slab", params={"font_size": step})
        doc = REGISTRY["vermilion-slab"].build(params, COLORS, "mp")
        title = next(e for e in doc.elements if e.kind == "text")
        small = title.text.font_size
        doc2 = REGISTRY["vermilion-slab"].build(params, COLORS, "xhs-3x4")
        big = next(e for e in doc2.elements if e.kind == "text").text.font_size
        assert big > small  # 1242 宽 > 900 宽 → 等比放大
    _, p_s = resolve(REGISTRY, "vermilion-slab", params={"font_size": "S"})
    _, p_l = resolve(REGISTRY, "vermilion-slab", params={"font_size": "L"})
    d_s = REGISTRY["vermilion-slab"].build(p_s, COLORS, "mp")
    d_l = REGISTRY["vermilion-slab"].build(p_l, COLORS, "mp")
    f_s = next(e for e in d_s.elements if e.kind == "text").text.font_size
    f_l = next(e for e in d_l.elements if e.kind == "text").text.font_size
    assert f_l > f_s


def test_background_slot_switch_resolves_to_palette() -> None:
    _, params = resolve(REGISTRY, "vermilion-slab",
                        params={"background_slot": "primary"})
    doc = REGISTRY["vermilion-slab"].build(params, COLORS, "mp")
    assert doc.background == COLORS["primary"]  # 槽名 → 注入色板值


def test_points_three_bars_proportional() -> None:
    _, params = resolve(REGISTRY, "points-three", params={
        "points": [{"label": "甲", "value": 3}, {"label": "乙", "value": 1}]
    })
    doc = REGISTRY["points-three"].build(params, COLORS, "wide16x9")
    values = [e for e in doc.elements
              if e.kind == "rect" and e.fill == COLORS["accent"]]
    assert len(values) == 2
    assert values[0].w == 3 * values[1].w  # 模板组装后 bar 比例仍执法


def test_f73t4_points_three_empty_data_empty_state() -> None:
    """F73-T4：空/全零数据 → 合法空态（提示文案）而非异常。"""
    _, params = resolve(REGISTRY, "points-three", params={"title": "无数据"})
    doc = REGISTRY["points-three"].build(params, COLORS, "square")
    texts = [e.text.text for e in doc.elements if e.kind == "text"]
    assert "暂无有效数据" in texts
    layout_document(doc)
    ET.fromstring(render_svg(doc))  # 全链合法 SVG


def test_f73t4_points_three_all_zero_data() -> None:
    _, params = resolve(REGISTRY, "points-three", params={
        "points": [{"label": "甲", "value": 0}, {"label": "乙", "value": 0}]
    })
    doc = REGISTRY["points-three"].build(params, COLORS, "square")  # 不除零不炸
    layout_document(doc)
    ET.fromstring(render_svg(doc))


@pytest.mark.parametrize("tid", sorted(BUILTIN_IDS))
def test_full_chain_all_specs_valid_svg(tid: str) -> None:
    """四内置 × 各自全部 spec：resolve→build→layout→svg 全链 XML 合法。"""
    params = {"title": "全链冒烟" * 6, "hook": "钩子"}
    if tid == "points-three":
        params = {"title": "全链冒烟", "points": [{"label": "一", "value": 2}]}
    meta, merged = resolve(REGISTRY, tid, params=params)
    for spec in meta.specs:
        doc = layout_document(meta.build(merged, COLORS, spec))
        svg = render_svg(doc)
        assert svg.startswith(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{doc.width}" '
            f'height="{doc.height}"'
        )
        ET.fromstring(svg)


# ── D-1 垂直重排回归（验收报告 2026-11-17 §二 D-1）──────────


def _text_of(doc, prefix: str):
    return next(e for e in doc.elements
                if e.kind == "text" and e.text.text.startswith(prefix))


def _rects_of(doc) -> list[tuple[float, float, float, float]]:
    return [(e.x, e.y, e.w, e.h) for e in doc.elements if e.kind == "rect"]


def test_d1_xhs_card_long_title_hook_pushed_below_title_band() -> None:
    """D-1 修复断言：5+ 行标题 → hook.y ≥ title.y + 行数×fs×lh（无重叠）。

    复现取证同报告 §二：修复前 hook 落在标题占用带内；修复后抬到带底。
    """
    _, params = resolve(REGISTRY, "xhs-card",
                        params={"title": "重" * 90, "hook": "钩子副文"})
    before = layout_document(REGISTRY["xhs-card"].build(params, COLORS, "xhs-3x4"))
    after = relayout_vertical(
        layout_document(REGISTRY["xhs-card"].build(params, COLORS, "xhs-3x4")))
    n = len(_text_of(after, "重").text.lines)
    assert n >= 5  # 触发 D-1 场景（fs=64/1050px 宽下 90 字 ≥ 5 行）
    height = n * _text_of(after, "重").text.font_size * _text_of(after, "重").text.line_height
    # 修复前：缺陷复现——副文在标题占用带内（报告实测重叠 280px 同源）
    assert _text_of(before, "钩子副文").y < _text_of(before, "重").y + height
    # 修复后：报告要求的不重叠断言
    assert _text_of(after, "钩子副文").y >= _text_of(after, "重").y + height
    # 标题自身不下移；版式骨架（药丸/底条 rect）逐值不动
    assert _text_of(after, "重").y == _text_of(before, "重").y
    assert _rects_of(after) == _rects_of(before)


@pytest.mark.parametrize("tid,params", [
    ("vermilion-slab", {"title": "短标题", "hook": "钩子"}),
    ("indigo-slab", {"title": "短标题", "hook": "钩子"}),
    ("xhs-card", {"title": "社媒标题", "hook": "钩子"}),
    ("points-three", {"title": "信息图", "points": [{"label": "甲", "value": 2}],
                      "footer": "脚注"}),
])
def test_d1_single_line_layout_identical_without_relayout(tid: str, params: dict) -> None:
    """D-1 回归锚：单行标题时 relayout 前后全部元素 (kind, x, y) 逐值一致。"""
    meta, merged = resolve(REGISTRY, tid, params=params)
    a = layout_document(meta.build(merged, COLORS, meta.specs[0]))
    b = relayout_vertical(layout_document(meta.build(merged, COLORS, meta.specs[0])))
    assert [(e.kind, e.x, e.y) for e in a.elements] == \
           [(e.kind, e.x, e.y) for e in b.elements]
