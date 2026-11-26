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
- 每元素一行输出（文档性/diff 友好是 SVG 选型论点 1 的兑现形态）；
- B2（2026-11-26）：①背景五形态（solid 同旧 rect；linear/radial 渐变经
  defs+linearGradient/radialGradient；pattern 经 defs+pattern 四内置纹样；
  image 经 image+蒙纱 rect）；②元素按 z 稳定序绘制、visible=False 跳过；
  ③blend 经 style="mix-blend-mode:…"（默认 normal 不发——存量产物零变化）；
  ④image 元素 fit/mask（preserveAspectRatio slice/meet + defs clipPath）。
"""
from __future__ import annotations

import html
import math

from .model import Background, CanvasDoc, Element, TextBlock, validate_doc

_ANCHOR = {"left": "start", "center": "middle", "right": "end"}
# 纹样墨色（serializer 内部常量——非用户输入，不走 Element 色校验；
# 8 位 hex 含 alpha，verify_svg 只拦网络引用不拦形态）
_INK = "#0f172a"


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
    """CanvasDoc → SVG 字符串（白名单复验 + 转义执法后的唯一出口）。

    B2：背景 str 形态与旧逐字节同；Background 五形态展开为 defs+底 rect；
    元素取 doc.layers()（z 稳定序）且 visible=False 跳过。
    """
    validate_doc(doc)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{doc.width}" '
        f'height="{doc.height}" viewBox="0 0 {doc.width} {doc.height}">',
    ]
    bg = doc.background
    if isinstance(bg, str) and bg:
        parts.append(
            f'  <rect x="0" y="0" width="{doc.width}" height="{doc.height}" '
            f'fill="{_esc_attr(bg)}"/>'
        )
    elif isinstance(bg, Background):
        parts.extend(_background_svg(bg, doc.width, doc.height))
    for e in doc.layers():
        if not e.visible:
            continue
        parts.extend(_element_svg(e, 1))
    parts.append("</svg>")
    return "\n".join(parts)


# ── B2 背景五形态（输出标签恒出自本函数：defs/linearGradient/radialGradient/
# stop/pattern/clipPath——visual_service._SVG_TAGS 白名单已同步扩容）──────


def _background_svg(bg: Background, w: int, h: int) -> list[str]:
    """Background → SVG 行（底色层 + defs 引用层；image 带蒙纱）。"""
    # class="dvbg"：编辑器（ui.js dvIsBgNode）跳过背景结构节点不进图层列表；
    # v1 str 背景不打 class（序列化面逐字节旧形态——test_background_rect_emitted_first 锚）。
    rect = (f'  <rect x="0" y="0" width="{w}" height="{h}" class="dvbg"')
    if bg.kind == "solid":
        return [f'{rect} fill="{_esc_attr(bg.color)}"/>']
    if bg.kind == "linear":
        # 角度→渐变向量（CSS 语义近似：0=→、90=↓；中心对称取半向量）
        rad = math.radians(bg.angle)
        cx = cy = 0.5
        dx, dy = math.cos(rad) / 2, math.sin(rad) / 2
        stops = "".join(
            f'<stop offset="{_num(p)}" stop-color="{_esc_attr(c)}"/>'
            for p, c in (bg.stops or [])
        )
        return [
            '  <defs class="dvbg">',
            f'    <linearGradient id="dvbg" x1="{_num(cx - dx)}" y1="{_num(cy - dy)}" '
            f'x2="{_num(cx + dx)}" y2="{_num(cy + dy)}">{stops}</linearGradient>',
            '  </defs>',
            f'{rect} fill="url(#dvbg)"/>',
        ]
    if bg.kind == "radial":
        stops = "".join(
            f'<stop offset="{_num(p)}" stop-color="{_esc_attr(c)}"/>'
            for p, c in (bg.stops or [])
        )
        return [
            '  <defs class="dvbg">',
            '    <radialGradient id="dvbg" cx="0.5" cy="0.5" r="0.7">'
            f'{stops}</radialGradient>',
            '  </defs>',
            f'{rect} fill="url(#dvbg)"/>',
        ]
    if bg.kind == "pattern":
        tile = _pattern_tile(bg.pattern)
        return [
            '  <defs class="dvbg">',
            f'    <pattern id="dvbg" width="48" height="48" '
            f'patternUnits="userSpaceOnUse">{tile}</pattern>',
            '  </defs>',
            f'{rect} fill="{_esc_attr(bg.color)}"/>',
            f'{rect} fill="url(#dvbg)"/>',
        ]
    # image：本地图铺满（slice=cover 语义）+ color 蒙纱（overlay_alpha>0 才叠）
    out = [
        f'  <image x="0" y="0" width="{w}" height="{h}" class="dvbg" '
        f'href="{_esc_attr(bg.image)}" preserveAspectRatio="xMidYMid slice"/>',
    ]
    if bg.overlay_alpha > 0:
        out.append(
            f'{rect} fill="{_esc_attr(bg.color)}" opacity="{_num(bg.overlay_alpha)}"/>'
        )
    return out


def _pattern_tile(pattern: str) -> str:
    """内置四纹样的 48×48 tile（墨色低 alpha——叠在底色上的「纸纹」感）。"""
    if pattern == "dots":
        return (
            '<circle cx="12" cy="12" r="3" fill="#0f172a2e"/>'
            '<circle cx="36" cy="36" r="3" fill="#0f172a2e"/>'
        )
    if pattern == "grid":
        return '<path d="M48 0H0V48" fill="none" stroke="#0f172a1f" stroke-width="1"/>'
    if pattern == "waves":
        return (
            '<path d="M0 24 Q12 12 24 24 T48 24" fill="none" '
            'stroke="#0f172a2e" stroke-width="2"/>'
        )
    # paper：细密点阵（两级错位，视觉「纸面颗粒」）
    return (
        '<circle cx="10" cy="10" r="1.2" fill="#0f172a17"/>'
        '<circle cx="26" cy="18" r="1.2" fill="#0f172a17"/>'
        '<circle cx="18" cy="30" r="1.2" fill="#0f172a17"/>'
        '<circle cx="34" cy="38" r="1.2" fill="#0f172a17"/>'
        '<circle cx="40" cy="14" r="1.2" fill="#0f172a17"/>'
        '<circle cx="6" cy="34" r="1.2" fill="#0f172a17"/>'
    )


def _attrs(e: Element) -> str:
    """公共样式属性串（fill/stroke/透明度/圆角/虚线/旋转/混合模式）。"""
    out = ""
    if e.fill:
        out += f' fill="{_esc_attr(e.fill)}"'
    if e.stroke:
        out += f' stroke="{_esc_attr(e.stroke)}"'
    if e.stroke_width:
        out += f' stroke-width="{_num(e.stroke_width)}"'
    if e.opacity != 1.0:
        out += f' opacity="{_num(e.opacity)}"'
    if e.blend != "normal":
        # mix-blend-mode 走 style（SVG 无独立属性）；verify_svg 的 style 面只拦
        # url()/@import——本值恒出自 BLEND_MODES 白名单，无引用面。
        out += f' style="mix-blend-mode:{_esc_attr(e.blend)}"'
    if e.rx:
        out += f' rx="{_num(e.rx)}"'
    if e.dasharray:
        out += f' stroke-dasharray="{_esc_attr(e.dasharray)}"'
    if e.dashoffset:
        out += f' stroke-dashoffset="{_num(e.dashoffset)}"'
    if e.rotate is not None:
        out += f' transform="rotate({_num(e.rotate)} {_num(e.cx)} {_num(e.cy)})"'
    return out


def _image_svg(e: Element, pad: str) -> list[str]:
    """image 元素（B2 fit/mask）：cover→slice、contain→meet；mask 经 defs
    clipPath（rounded/circle/arch 三形）。fit/mask 空=存量形态零变化。"""
    par = "xMidYMid slice" if e.fit == "cover" else ("xMidYMid meet" if e.fit == "contain" else None)
    clip = ""
    if e.mask in ("rounded", "circle", "arch"):
        rr = min(e.w, e.h) * 0.5
        if e.mask == "rounded":
            shape = f'<rect x="{_num(e.x)}" y="{_num(e.y)}" width="{_num(e.w)}" ' \
                    f'height="{_num(e.h)}" rx="{_num(min(e.w, e.h) * 0.12)}"/>'
        elif e.mask == "circle":
            shape = f'<circle cx="{_num(e.x + e.w / 2)}" cy="{_num(e.y + e.h / 2)}" ' \
                    f'r="{_num(rr)}"/>'
        else:  # arch：上半圆 + 直角的拱形（路径）
            shape = (
                f'<path d="M {_num(e.x)} {_num(e.y + e.h)} L {_num(e.x)} {_num(e.y + rr)} '
                f'A {_num(rr)} {_num(rr)} 0 0 1 {_num(e.x + e.w)} {_num(e.y + rr)} '
                f'L {_num(e.x + e.w)} {_num(e.y + e.h)} Z"/>'
            )
        clip = f' clip-path="url(#dvmask)"'
    out: list[str] = []
    if clip:
        out.append(f"{pad}<defs class=\"dvclip\"><clipPath id=\"dvmask\">{shape}</clipPath></defs>")
    img = f'{pad}<image x="{_num(e.x)}" y="{_num(e.y)}" width="{_num(e.w)}" ' \
          f'height="{_num(e.h)}" href="{_esc_attr(e.href)}"'
    if par:
        img += f' preserveAspectRatio="{par}"'
    img += f'{clip}{_attrs(e)}/>'
    out.append(img)
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
    if e.kind == "image":
        return _image_svg(e, pad)
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
    """text 元素：tspan 行结构（layout 固化的 lines；未测量时按单行原文）。

    B2：opacity/blend 挂 <text> 开标签（_attrs 不适用于 text——其属性面
    自成一套 font-size/fill/text-anchor）。"""
    b: TextBlock = e.text  # 校验保证 text 元素恒带 TextBlock
    lines = b.lines if b.lines is not None else ([b.text] if b.text else [])
    tx = {"left": e.x, "center": e.x + e.w / 2, "right": e.x + e.w}[b.align]
    step = round(b.font_size * b.line_height)
    extra = ""
    if e.opacity != 1.0:
        extra += f' opacity="{_num(e.opacity)}"'
    if e.blend != "normal":
        extra += f' style="mix-blend-mode:{_esc_attr(e.blend)}"'
    out = [
        f'{pad}<text x="{_num(tx)}" y="{_num(e.y)}" font-size="{b.font_size}" '
        f'fill="{_esc_attr(b.color)}" text-anchor="{_ANCHOR[b.align]}"{extra}>'
    ]
    for i, line in enumerate(lines):
        out.append(
            f'{pad}  <tspan x="{_num(tx)}" dy="{0 if i == 0 else step}">'
            f"{_esc_text(line)}</tspan>"
        )
    out.append(f"{pad}</text>")
    return out


__all__ = ["render_svg"]
