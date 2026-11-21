"""F70 C 路兜底 — doc→Pillow 逐元素重绘（无浏览器保底；cover 兜底语义平移）。

- 尺寸恒精确 + RGB 白底（F70-T5；_to_rgb 白底纪律同 cover）；
- 视觉近似清单（C 路定位是「尺寸保底」非「像素一致」）：dasharray 圆弧按
  弧长比例近似为分段 arc；path 仅支持 M/L/Z 折线子集（模板域基元不产
  path，复杂 path 跳过不报错）；opacity 不合成（基元域未用）；
- 图片图层：dataURI 解码或本地文件经 PIL 加载（Pillow 白名单例外），
  http(s) 已在 model 层拒收——C 路不触网红线的两层执法之一。
"""
from __future__ import annotations

import base64
import binascii
import io
import math
import re

from PIL import Image, ImageDraw

from .layout import load_font, wrap_text
from .model import CanvasDoc, Element

_ANCHOR = {"left": "la", "center": "ma", "right": "ra"}


def render(doc: CanvasDoc) -> Image.Image:
    """CanvasDoc → RGB PIL Image（C 路兜底：尺寸恒 (doc.width, doc.height)）。"""
    bg = doc.background or "#ffffff"
    img = Image.new("RGB", (doc.width, doc.height), bg)
    draw = ImageDraw.Draw(img)
    for e in doc.elements:
        _draw_element(img, draw, e)
    return img


def _stroke_w(e: Element) -> int:
    return max(1, round(e.stroke_width))


def _draw_element(img: Image.Image, draw: ImageDraw.ImageDraw, e: Element) -> None:
    if e.kind == "g":
        for child in e.children:
            _draw_element(img, draw, child)
        return
    if e.kind == "rect":
        box = [e.x, e.y, e.x + e.w, e.y + e.h]
        kw = dict(fill=e.fill or None, outline=e.stroke or None, width=_stroke_w(e))
        if e.rx > 0:
            draw.rounded_rectangle(box, radius=e.rx, **kw)
        else:
            draw.rectangle(box, **kw)
    elif e.kind == "circle":
        box = [e.cx - e.r, e.cy - e.r, e.cx + e.r, e.cy + e.r]
        if e.dasharray:
            for start, end in _dash_arcs(e):
                draw.arc(box, start, end, fill=e.stroke, width=_stroke_w(e))
        else:
            draw.ellipse(box, fill=e.fill or None, outline=e.stroke or None, width=_stroke_w(e))
    elif e.kind == "line":
        draw.line([e.x1, e.y1, e.x2, e.y2], fill=e.stroke, width=_stroke_w(e))
    elif e.kind == "path":
        pts = _polyline(e.d)
        if len(pts) >= 2:
            draw.line(pts, fill=e.stroke, width=_stroke_w(e))
            if e.fill and e.d.upper().rstrip().endswith("Z"):
                draw.polygon(pts, fill=e.fill)
    elif e.kind == "image":
        layer = _load_image(e.href)
        if layer is not None:
            img.paste(layer, (round(e.x), round(e.y)))
    elif e.kind == "text":
        _draw_text(draw, e)


def _draw_text(draw: ImageDraw.ImageDraw, e: Element) -> None:
    """文本逐行绘制：y 语义对齐 SVG 基线（top 锚点 + ascent 补偿）。

    未测量的元素现场分行（兜底一致性：与 layout_document 同 wrap_text）。
    """
    b = e.text
    font = load_font(b.font_size)
    lines = b.lines if b.lines is not None else wrap_text(b.text, font, max(round(e.w), 1))
    try:
        ascent, _ = font.getmetrics()
    except AttributeError:  # pragma: no cover - 位图字体兜底
        ascent = 0
    x = {"left": e.x, "center": e.x + e.w / 2, "right": e.x + e.w}[b.align]
    step = round(b.font_size * b.line_height)
    for i, line in enumerate(lines):
        draw.text((x, e.y + i * step - ascent), line, font=font, fill=b.color,
                  anchor=_ANCHOR[b.align])


def _dash_arcs(e: Element) -> list[tuple[float, float]]:
    """circle+dasharray → arc 起止角（shapes.ring 约定：dashoffset=-前弧累计）。"""
    circ = 2 * math.pi * e.r
    if circ <= 0:
        return []
    try:
        arc = float(e.dasharray.replace(",", " ").split()[0])
    except (ValueError, IndexError):
        return []
    base_deg = e.rotate or 0.0
    start = base_deg + 360 * (-e.dashoffset / circ)
    return [(start, start + 360 * arc / circ)]


def _polyline(d: str) -> list[tuple[float, float]]:
    """path M/L/Z 折线子集 → 坐标点列（曲线指令出现即放弃该 path）。"""
    if re.search(r"[CcSsQqTtAa]", d):
        return []
    vals = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)]
    if len(vals) < 4 or not re.search(r"[MLml]", d):
        return []
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals) - 1, 2)]


def _load_image(href: str) -> Image.Image | None:
    """image 图层加载：dataURI 解码 / 本地路径经 PIL（失败跳过——兜底不崩）。"""
    try:
        if href.startswith("data:"):
            b64 = href.split(",", 1)[1]
            data = base64.b64decode(b64)
            im = Image.open(io.BytesIO(data))
        else:
            im = Image.open(href)
        return im.convert("RGB")
    except (OSError, ValueError, binascii.Error):
        return None


__all__ = ["render"]
