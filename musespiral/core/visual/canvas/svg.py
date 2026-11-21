"""F70 SVG 序列化 — doc→SVG 唯一合法出口；白名单+转义执法（防注入）。

- 渲染前 validate_doc 复验（构造后篡改仍被拦——「凡经 API 透出的 SVG 字符串
  必出自本白名单序列化」的 F70 API 纪律执行点）；
- 文本节点经 html.escape（& < >），属性值经 quote=True 转义——载荷中的
  ``<script>`` / ``<img onerror=…>`` 只会以 ``&lt;`` 文本形态出现（F70-T2）。
  来源实体解码（``&quot;`` → ``"``）**不在本层**：已在文档构造边界
  （model.TextBlock，见 model.decode_text_entities）一次性完成——若此处再
  unescape，会把解码后的 ``&lt;``（源自 ``&amp;lt;``）二次解码成 ``<``，
  破坏「解码恰好一次」语义（验收报告 2026-11-17 复核判例 D-2 真根因）；
- 头部逐 spec 精确（F70-T4）：``<svg xmlns=… width="900" height="383"
  viewBox="0 0 900 383">``（xmlns 前置满足独立 .svg 文件的 XML 合法性，
  设计文档片段省略 xmlns 不影响尺寸断言语义）；
- 每元素一行输出（文档性/diff 友好是 SVG 选型论点 1 的兑现形态）。
"""
from __future__ import annotations

import html

from .model import CanvasDoc, Element, TextBlock, validate_doc

_ANCHOR = {"left": "start", "center": "middle", "right": "end"}


def _num(v: float) -> str:
    """数值格式：整数值去小数点，浮点保留两位去尾零。"""
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")


def _esc_text(s: str) -> str:
    """文本节点转义（& < >；引号保持——text 上下文合法）。

    来源实体已在 model.TextBlock 构造边界解码一次（D-2），此处只 escape，
    保证序列化面恒只含合法文本标记（F70-T2 注入红线）。
    """
    return html.escape(s, quote=False)


def _esc_attr(s: str) -> str:
    """属性值转义 + quote=True（引号转义，属性边界不可破）。"""
    return html.escape(s, quote=True)


def render_svg(doc: CanvasDoc) -> str:
    """CanvasDoc → SVG 字符串（白名单复验 + 转义执法后的唯一出口）。"""
    validate_doc(doc)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{doc.width}" '
        f'height="{doc.height}" viewBox="0 0 {doc.width} {doc.height}">',
    ]
    if doc.background:
        parts.append(
            f'  <rect x="0" y="0" width="{doc.width}" height="{doc.height}" '
            f'fill="{_esc_attr(doc.background)}"/>'
        )
    for e in doc.elements:
        parts.extend(_element_svg(e, 1))
    parts.append("</svg>")
    return "\n".join(parts)


def _attrs(e: Element) -> str:
    """公共样式属性串（fill/stroke/透明度/圆角/虚线/旋转）。"""
    out = ""
    if e.fill:
        out += f' fill="{_esc_attr(e.fill)}"'
    if e.stroke:
        out += f' stroke="{_esc_attr(e.stroke)}"'
    if e.stroke_width:
        out += f' stroke-width="{_num(e.stroke_width)}"'
    if e.opacity != 1.0:
        out += f' opacity="{_num(e.opacity)}"'
    if e.rx:
        out += f' rx="{_num(e.rx)}"'
    if e.dasharray:
        out += f' stroke-dasharray="{_esc_attr(e.dasharray)}"'
    if e.dashoffset:
        out += f' stroke-dashoffset="{_num(e.dashoffset)}"'
    if e.rotate is not None:
        out += f' transform="rotate({_num(e.rotate)} {_num(e.cx)} {_num(e.cy)})"'
    return out


def _element_svg(e: Element, indent: int) -> list[str]:
    pad = "  " * indent
    if e.kind == "g":
        out = [f"{pad}<g>"]
        for child in e.children:
            out.extend(_element_svg(child, indent + 1))
        out.append(f"{pad}</g>")
        return out
    if e.kind == "text":
        return _text_svg(e, pad)
    shapes = {
        "rect": f'<rect x="{_num(e.x)}" y="{_num(e.y)}" width="{_num(e.w)}" '
        f'height="{_num(e.h)}"{_attrs(e)}/>',
        "circle": f'<circle cx="{_num(e.cx)}" cy="{_num(e.cy)}" r="{_num(e.r)}"{_attrs(e)}/>',
        "line": f'<line x1="{_num(e.x1)}" y1="{_num(e.y1)}" x2="{_num(e.x2)}" '
        f'y2="{_num(e.y2)}"{_attrs(e)}/>',
        "path": f'<path d="{_esc_attr(e.d)}"{_attrs(e)}/>',
        "image": f'<image x="{_num(e.x)}" y="{_num(e.y)}" width="{_num(e.w)}" '
        f'height="{_num(e.h)}" href="{_esc_attr(e.href)}"{_attrs(e)}/>',
    }
    return [pad + shapes[e.kind]]


def _text_svg(e: Element, pad: str) -> list[str]:
    """text 元素：tspan 行结构（layout 固化的 lines；未测量时按单行原文）。"""
    b: TextBlock = e.text  # 校验保证 text 元素恒带 TextBlock
    lines = b.lines if b.lines is not None else ([b.text] if b.text else [])
    tx = {"left": e.x, "center": e.x + e.w / 2, "right": e.x + e.w}[b.align]
    step = round(b.font_size * b.line_height)
    out = [
        f'{pad}<text x="{_num(tx)}" y="{_num(e.y)}" font-size="{b.font_size}" '
        f'fill="{_esc_attr(b.color)}" text-anchor="{_ANCHOR[b.align]}">'
    ]
    for i, line in enumerate(lines):
        out.append(
            f'{pad}  <tspan x="{_num(tx)}" dy="{0 if i == 0 else step}">'
            f"{_esc_text(line)}</tspan>"
        )
    out.append(f"{pad}</text>")
    return out


__all__ = ["render_svg"]
