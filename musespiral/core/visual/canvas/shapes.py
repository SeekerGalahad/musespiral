"""F73 图表基元 — bar/ring/stat/timeline 纯函数（零图表库；产白名单元素组）。

- 返回 ``list[Element]`` 交由 CanvasDoc 组装——不产裸 SVG 字符串，白名单与
  转义执法统一收口在 svg.py（基元永不绕过序列化门）；
- ring 用 circle+stroke-dasharray 表达弧段（svg.py 逐值还原弧长——F73-T2
  断言挂序列化产物；pillow_render 按同弧长比例近似 arc）；
- 空数据零元素、负值/占比和>1 可读拒收（F73-T4 全零/空态在模板层收口）。
"""
from __future__ import annotations

import math

from .model import Element, TextBlock

# 0 值最小条宽（F73-T1：0 值宽=最小描边宽不消失）
MIN_BAR_WIDTH = 2.0


def _num_list(values, where: str) -> list[float]:
    out: list[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{where}: 应为数值，得到 {v!r}")
        if v < 0:
            raise ValueError(f"{where}: 负值不可视化 {v!r}（条宽∝值，负宽无意义）")
        out.append(float(v))
    return out


def bar(
    values: list[float],
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    gap: float = 12.0,
    bar_color: str = "#2563eb",
    track_color: str = "#e5e7eb",
) -> list[Element]:
    """水平条形组：track 全宽打底 + 值条宽 ∝ value/max（F73-T1 像素断言锚）。

    - 空 values → []；全零 max 无意义 → 全部退最小条宽（不除零不消失）；
    - 每行两组 rect（track+value），行高 = h/len(values) - gap。
    """
    vals = _num_list(values, "bar.values")
    if not vals:
        return []
    mx = max(vals) or 1.0  # 全零兜底：max=0 时归一基准取 1（全部退最小宽）
    row_h = h / len(vals)
    bar_h = max(2.0, row_h - gap)
    out: list[Element] = []
    for i, v in enumerate(vals):
        top = y + i * row_h
        out.append(Element("rect", x=x, y=top, w=w, h=bar_h, fill=track_color))
        bw = w * v / mx
        out.append(Element("rect", x=x, y=top, w=max(bw, MIN_BAR_WIDTH), h=bar_h, fill=bar_color))
    return out


def ring(
    proportions: list[float],
    *,
    cx: float,
    cy: float,
    r: float,
    thickness: float = 18.0,
    colors: tuple[str, ...] = ("#2563eb", "#dc2626", "#16a34a"),
    track_color: str = "#e5e7eb",
    start_at_top: bool = True,
) -> list[Element]:
    """占比环：track 整环 + 每占比一段弧（dasharray 首值=占比×2πr——F73-T2）。

    占比和 >1（容差 1e-9）拒收——环装不下；段起点按前弧累计偏移
    （dashoffset=-累计），rotate=-90 从 12 点起（pillow_render 同约定）。
    """
    props = _num_list(proportions, "ring.proportions")
    total = sum(props)
    if total > 1 + 1e-9:
        raise ValueError(
            f"ring.proportions: 占比和 {total:.4g} > 1（环无法容纳——检查数据归一）"
        )
    circ = 2 * math.pi * r
    out = [Element("circle", cx=cx, cy=cy, r=r, stroke=track_color, stroke_width=thickness)]
    used = 0.0
    for i, p in enumerate(props):
        if p > 0:
            out.append(Element(
                "circle", cx=cx, cy=cy, r=r,
                stroke=colors[i % len(colors)], stroke_width=thickness,
                dasharray=f"{p * circ:.2f} {circ:.2f}",
                dashoffset=-used,
                rotate=-90.0 if start_at_top else 0.0,
            ))
        used += p * circ
    return out


def stat(
    value: object,
    label: str,
    *,
    x: float,
    y: float,
    w: float = 0.0,
    value_color: str = "#1f2937",
    label_color: str = "#6b7280",
    value_size: int = 64,
    label_size: int = 24,
) -> list[Element]:
    """大数字卡：value 主字 + label 副字（两 text 元素）。"""
    return [
        Element("text", x=x, y=y, w=w,
                text=TextBlock(str(value), font_size=value_size, color=value_color)),
        Element("text", x=x, y=y + round(value_size * 1.35), w=w,
                text=TextBlock(str(label), font_size=label_size, color=label_color)),
    ]


def timeline(
    items: list[str],
    *,
    x: float,
    y: float,
    w: float,
    item_h: float = 64.0,
    line_color: str = "#d1d5db",
    dot_color: str = "#2563eb",
    text_color: str = "#1f2937",
    label_size: int = 24,
) -> list[Element]:
    """纵向时间线：轴线 1 + 每项 圆点+文本；空 items → []（空态由模板收口）。"""
    if not items:
        return []
    out = [Element("line", x1=x, y1=y, x2=x, y2=y + (len(items) - 1) * item_h,
                   stroke=line_color, stroke_width=2.0)]
    for i, label in enumerate(items):
        cy = y + i * item_h
        out.append(Element("circle", cx=x, cy=cy, r=6.0, fill=dot_color))
        out.append(Element("text", x=x + 20.0, y=cy + round(label_size * 0.35),
                           w=max(w - 20.0, 1.0),
                           text=TextBlock(str(label), font_size=label_size, color=text_color)))
    return out


def empty_state(
    *,
    x: float,
    y: float,
    w: float,
    h: float,
    hint: str = "暂无有效数据",
    color: str = "#9ca3af",
) -> list[Element]:
    """空态占位（F73-T4：全零/空数据产合法空态而非异常）。"""
    return [
        Element("rect", x=x, y=y, w=w, h=h, stroke=color, stroke_width=2.0),
        Element("text", x=x, y=y + h / 2 + 10.0, w=w,
                text=TextBlock(hint, font_size=28, color=color, align="center")),
    ]


__all__ = ["MIN_BAR_WIDTH", "bar", "empty_state", "ring", "stat", "timeline"]
