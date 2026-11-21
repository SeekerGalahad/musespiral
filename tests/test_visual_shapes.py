"""F73 图表基元几何断言（core/visual/canvas/shapes.py）——像素级比例执法。

- T1：bar 宽 ∝ 值（[3,1] → 宽比精确 3:1）；0 值=最小描边宽不消失；
- T2：ring 弧长=占比×2πr（序列化 dasharray 首值 ±0.5）；占比和>1 拒收；
- T4：空数据零元素/全零不除零；空态基元（empty_state）合法产出。
"""
from __future__ import annotations

import math

import pytest

from musespiral.core.visual.canvas.model import CanvasDoc, Element
from musespiral.core.visual.canvas.shapes import (
    MIN_BAR_WIDTH,
    bar,
    empty_state,
    ring,
    stat,
    timeline,
)
from musespiral.core.visual.canvas.svg import render_svg


def _value_rects(els: list[Element]) -> list[Element]:
    """挑出值条（fill=bar 色）——track 色区分。"""
    return [e for e in els if e.kind == "rect" and e.fill == "#2563eb"]


# ── F73-T1 bar ───────────────────────────────────────────────


def test_f73t1_bar_width_proportional_3_to_1() -> None:
    """值 [3,1] → 两值条 width 比=3:1（像素断言）。"""
    els = bar([3, 1], x=0, y=0, w=300, h=100, bar_color="#2563eb", track_color="#e5e7eb")
    rects = _value_rects(els)
    assert len(rects) == 2
    w1, w2 = rects[0].w, rects[1].w
    assert w1 == 300.0 and w2 == 100.0
    assert abs(w1 - 3 * w2) < 1e-9


def test_f73t1_zero_value_keeps_min_width() -> None:
    """0 值宽=最小描边宽不消失。"""
    els = bar([5, 0], x=0, y=0, w=300, h=100)
    rects = _value_rects(els)
    assert rects[1].w == MIN_BAR_WIDTH
    assert rects[1].w > 0


def test_bar_empty_returns_no_elements() -> None:
    assert bar([], x=0, y=0, w=100, h=50) == []


def test_bar_all_zero_no_division_error() -> None:
    """全零数据：max=0 不除零，全部退最小宽（F73-T4 相邻面）。"""
    els = bar([0, 0, 0], x=0, y=0, w=300, h=90)
    rects = _value_rects(els)
    assert len(rects) == 3
    assert all(r.w == MIN_BAR_WIDTH for r in rects)


@pytest.mark.parametrize("bad", [[-1, 2], [1, "x"], [True, 1]])
def test_bar_invalid_values_rejected(bad) -> None:
    with pytest.raises(ValueError):
        bar(bad, x=0, y=0, w=100, h=50)


def test_bar_track_and_value_pairs() -> None:
    els = bar([1, 2], x=0, y=0, w=200, h=80, bar_color="#111111", track_color="#eeeeee")
    tracks = [e for e in els if e.fill == "#eeeeee"]
    assert len(tracks) == 2
    assert all(t.w == 200.0 for t in tracks)  # track 恒全宽


# ── F73-T2 ring ──────────────────────────────────────────────


def test_f73t2_ring_arc_length_exact() -> None:
    """占比 0.25 → 序列化 stroke-dasharray 首值=2πr×0.25（±0.5）。"""
    r = 100.0
    els = ring([0.25], cx=150, cy=150, r=r)
    arc_els = [e for e in els if e.dasharray]
    assert len(arc_els) == 1
    first = float(arc_els[0].dasharray.split()[0])
    expected = 2 * math.pi * r * 0.25
    assert abs(first - expected) <= 0.5
    # 序列化产物同值（svg.py 逐值还原）
    svg = render_svg(CanvasDoc(300, 300, elements=els))
    assert f"stroke-dasharray=\"{first:.2f} " in svg


def test_f73t2_ring_multi_segment_offsets_cumulative() -> None:
    """多段：dashoffset=前弧累计（负偏移），段间不重叠。"""
    els = ring([0.25, 0.5], cx=100, cy=100, r=50)
    arcs = [e for e in els if e.dasharray]
    assert len(arcs) == 2
    assert arcs[0].dashoffset == 0.0
    first_arc = float(arcs[0].dasharray.split()[0])
    assert abs(arcs[1].dashoffset + first_arc) < 0.05


def test_f73t2_ring_sum_over_one_rejected() -> None:
    with pytest.raises(ValueError, match="占比和"):
        ring([0.6, 0.6], cx=50, cy=50, r=30)


def test_f73t2_ring_sum_exactly_one_accepted() -> None:
    els = ring([0.5, 0.5], cx=50, cy=50, r=30)  # 边界：恰=1 不拒
    assert len([e for e in els if e.dasharray]) == 2


def test_ring_track_present_and_negative_rejected() -> None:
    els = ring([0.2], cx=50, cy=50, r=20)
    assert els[0].kind == "circle" and not els[0].dasharray  # 轨道整环
    with pytest.raises(ValueError):
        ring([-0.2, 0.3], cx=50, cy=50, r=20)


def test_ring_empty_proportions_only_track() -> None:
    els = ring([], cx=50, cy=50, r=20)
    assert len(els) == 1  # 仅轨道


# ── stat / timeline / empty_state ────────────────────────────


def test_stat_two_text_elements() -> None:
    els = stat("87%", "完读率", x=10, y=20, w=200)
    assert [e.kind for e in els] == ["text", "text"]
    assert els[0].text.text == "87%"
    assert els[1].text.text == "完读率"
    assert els[1].y > els[0].y


def test_timeline_structure() -> None:
    els = timeline(["立项", "内测", "发布"], x=20, y=30, w=300)
    assert sum(1 for e in els if e.kind == "line") == 1
    assert sum(1 for e in els if e.kind == "circle") == 3
    texts = [e for e in els if e.kind == "text"]
    assert [t.text.text for t in texts] == ["立项", "内测", "发布"]


def test_timeline_empty_returns_nothing() -> None:
    assert timeline([], x=0, y=0, w=100) == []


def test_f73t4_empty_state_elements() -> None:
    """空态基元：虚线框 + 居中提示文案（F73-T4 空数据产合法空态）。"""
    els = empty_state(x=10, y=10, w=200, h=100, hint="暂无有效数据")
    assert els[0].kind == "rect" and els[0].stroke
    assert els[1].kind == "text" and els[1].text.text == "暂无有效数据"
    assert els[1].text.align == "center"
