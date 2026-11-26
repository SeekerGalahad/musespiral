"""F70 C 路兜底 — doc→Pillow 逐元素重绘（无浏览器保底；cover 兜底语义平移）。

- 尺寸恒精确 + RGB 白底（F70-T5；_to_rgb 白底纪律同 cover）；
- 视觉近似清单（C 路定位是「尺寸保底」非「像素一致」）：dasharray 圆弧按
  弧长比例近似为分段 arc；path 仅支持 M/L/Z 折线子集（模板域基元不产
  path，复杂 path 跳过不报错）；
- 图片图层：dataURI 解码或本地文件经 PIL 加载（Pillow 白名单例外），
  http(s) 已在 model 层拒收——C 路不触网红线的两层执法之一；
- B2（2026-11-26）：①背景五形态（solid 同旧；linear/radial 平滑插值——
  逐条垂线/同心环绘制，非逐像素；pattern 48px tile 平铺四纹样；image
  本地图 cover 适配 + overlay_alpha 蒙纱）；②元素按 z 稳定序（doc.layers）
  且 visible=False 跳过；③opacity<1 或 blend≠normal 经透明图层合成
  （ImageChops multiply/screen/overlay 同名实现——与 SVG mix-blend-mode
  共用 model.BLEND_MODES 白名单）；④image 元素 fit=cover/contain 缩放
  + mask 蒙版（rounded/circle；arch 以椭圆近似）。
"""
from __future__ import annotations

import base64
import binascii
import io
import math
import re

from PIL import Image, ImageChops, ImageDraw

from .layout import load_font, wrap_text
from .model import Background, CanvasDoc, Element

_ANCHOR = {"left": "la", "center": "ma", "right": "ra"}


def _hex_rgba(value: str) -> tuple[int, int, int, int]:
    """hex（3/6/8 位）→ RGBA。渲染层内部工具（输入已过 model 色校验）。"""
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) == 8:
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), int(v[6:8], 16))
    return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16), 255)


def _lerp(c0: tuple[int, int, int], c1: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c0, c1))  # type: ignore[return-value]


def render(doc: CanvasDoc) -> Image.Image:
    """CanvasDoc → RGB PIL Image（C 路兜底：尺寸恒 (doc.width, doc.height)）。"""
    img = _paint_background(doc)
    for e in doc.layers():
        if not e.visible:
            continue
        if e.opacity < 1.0 or e.blend != "normal":
            _draw_composited(img, e)
        else:
            _draw_element(img, ImageDraw.Draw(img), e)
    return img.convert("RGB")


# ── B2 背景（内部恒 RGBA 工作面；render 出口统一转 RGB）────────────────


def _paint_background(doc: CanvasDoc) -> Image.Image:
    bg = doc.background
    size = (doc.width, doc.height)
    if bg is None or (isinstance(bg, str) and not bg):
        return Image.new("RGBA", size, (255, 255, 255, 255))
    if isinstance(bg, str):
        return Image.new("RGBA", size, _hex_rgba(bg))
    if bg.kind == "solid":
        return Image.new("RGBA", size, _hex_rgba(bg.color))
    if bg.kind == "linear":
        return _linear_gradient(bg, size)
    if bg.kind == "radial":
        return _radial_gradient(bg, size)
    if bg.kind == "pattern":
        return _pattern_background(bg, size)
    return _image_background(bg, size)


def _bg_colors(bg: Background) -> list[tuple[float, tuple[int, int, int]]]:
    return [(float(p), _hex_rgba(c)[:3]) for p, c in (bg.stops or [])]


def _linear_gradient(bg: Background, size: tuple[int, int]) -> Image.Image:
    """线性渐变：沿角度方向的逐条垂线（每条一次 C 级 draw.line——平滑插值且
    免逐像素 Python 循环；1080×8000 长图实测 ~9000 次线绘制，C 路可承受）。"""
    w, h = size
    colors = _bg_colors(bg)
    base = _hex_rgba(bg.color)
    if not colors:
        colors = [(0.0, base[:3]), (1.0, base[:3])]
    img = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    rad = math.radians(bg.angle)
    dx, dy = math.cos(rad), math.sin(rad)
    length = abs(dx) * w + abs(dy) * h or 1.0
    diag = math.hypot(w, h)
    for i in range(int(length) + 1):
        t = i / length
        # 首尾锚点间线性插值（多锚点按 offset 分段）
        col = _stop_at(colors, t)
        cx, cy = dx * (i - length / 2) + w / 2, dy * (i - length / 2) + h / 2
        px, py = -dy, dx  # 垂直方向
        draw.line(
            [(cx - px * diag, cy - py * diag), (cx + px * diag, cy + py * diag)],
            fill=col + (255,), width=2,
        )
    return img


def _stop_at(colors: list[tuple[float, tuple[int, int, int]]], t: float) -> tuple[int, int, int]:
    if t <= colors[0][0]:
        return colors[0][1]
    if t >= colors[-1][0]:
        return colors[-1][1]
    for (p0, c0), (p1, c1) in zip(colors, colors[1:]):
        if p0 <= t <= p1:
            span = (p1 - p0) or 1.0
            return _lerp(c0, c1, (t - p0) / span)
    return colors[-1][1]


def _radial_gradient(bg: Background, size: tuple[int, int]) -> Image.Image:
    """径向渐变：256×256 低分辨率同心环绘制后放大（平滑插值 + 性能折中——
    C 路「视觉近似」定位下的标准做法，主像素一致由 B 路（浏览器）保证）。"""
    colors = _bg_colors(bg)
    if not colors:
        base = _hex_rgba(bg.color)
        colors = [(0.0, base[:3]), (1.0, base[:3])]
    small = Image.new("RGBA", (256, 256), colors[-1][1] + (255,))
    draw = ImageDraw.Draw(small)
    center, max_r = 128, 181  # 128√2 ≈ 181（对角全覆盖）
    for r in range(max_r, 0, -2):
        t = r / max_r
        draw.ellipse([center - r, center - r, center + r, center + r],
                     fill=_stop_at(colors, t) + (255,))
    return small.resize(size, Image.BILINEAR)


def _pattern_background(bg: Background, size: tuple[int, int]) -> Image.Image:
    """纹样背景：48px tile（四内置纹样与 svg._pattern_tile 同参数族）平铺。"""
    img = Image.new("RGBA", size, _hex_rgba(bg.color))
    tile = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    ink = (15, 23, 42)
    if bg.pattern == "dots":
        d.ellipse([9, 9, 15, 15], fill=ink + (46,))
        d.ellipse([33, 33, 39, 39], fill=ink + (46,))
    elif bg.pattern == "grid":
        d.line([(48, 0), (0, 0), (0, 48)], fill=ink + (31,), width=1)
    elif bg.pattern == "waves":
        d.line([(0, 24), (6, 18), (12, 24), (18, 30), (24, 24), (30, 18), (36, 24), (42, 30), (48, 24)],
               fill=ink + (46,), width=2)
    else:  # paper：细密点阵
        for x, y in ((10, 10), (26, 18), (18, 30), (34, 38), (40, 14), (6, 34)):
            d.ellipse([x - 1, y - 1, x + 1, y + 1], fill=ink + (23,))
    for yy in range(0, size[1], 48):
        for xx in range(0, size[0], 48):
            img.alpha_composite(tile, (xx, yy))
    return img


def _image_background(bg: Background, size: tuple[int, int]) -> Image.Image:
    """图片背景：本地图 cover 适配铺满 + color 蒙纱（overlay_alpha）。"""
    img = Image.new("RGBA", size, _hex_rgba(bg.color))
    layer = _load_image(bg.image)
    if layer is not None:
        fitted = _cover_fit(layer, size)
        img.alpha_composite(fitted)
    if bg.overlay_alpha > 0:
        alpha = round(bg.overlay_alpha * 255)
        scrim = Image.new("RGBA", size, _hex_rgba(bg.color)[:3] + (alpha,))
        img.alpha_composite(scrim)
    return img


def _cover_fit(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    """cover 语义：等比放大至覆盖画布后中心裁剪（image 背景与 fit=cover 共用）。"""
    sw, sh = size
    ratio = max(sw / im.width, sh / im.height)
    nw, nh = max(round(im.width * ratio), 1), max(round(im.height * ratio), 1)
    scaled = im.convert("RGBA").resize((nw, nh))
    left, top = (nw - sw) // 2, (nh - sh) // 2
    return scaled.crop((left, top, left + sw, top + sh))


# ── 元素绘制（存量直绘路径 + B2 合成路径）──────────────────────


def _draw_composited(img: Image.Image, e: Element) -> None:
    """opacity/blend 合成路径：元素先画进透明层，再按 opacity 调 alpha、按
    blend 混合回底图（ImageChops multiply/screen/overlay 与 SVG
    mix-blend-mode 同名同义——两路渲染共用 model.BLEND_MODES 一张白名单）。"""
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _draw_element(lay, ImageDraw.Draw(lay), e)
    if e.opacity < 1.0:
        alpha = lay.getchannel("A").point(lambda v, o=e.opacity: int(v * o))
        lay.putalpha(alpha)
    if e.blend == "normal":
        img.alpha_composite(lay)
        return
    base_rgb = img.convert("RGB")
    lay_rgb = lay.convert("RGB")
    if e.blend == "multiply":
        mixed = ImageChops.multiply(base_rgb, lay_rgb)
    elif e.blend == "screen":
        mixed = ImageChops.screen(base_rgb, lay_rgb)
    else:  # overlay
        mixed = ImageChops.overlay(base_rgb, lay_rgb)
    img.paste(mixed, (0, 0), lay.getchannel("A"))


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
            _paste_image(img, layer, e)
    elif e.kind == "text":
        _draw_text(draw, e)


def _paste_image(img: Image.Image, layer: Image.Image, e: Element) -> None:
    """B2 image 元素：fit/mask 生效（空=旧行为：原尺寸贴 (x,y)）；cover/contain
    先缩放，mask=rounded/circle/arch 经 L 蒙版贴入（arch 以椭圆近似——C 路
    视觉近似清单）。"""
    if not e.fit and not e.mask:
        img.paste(layer, (round(e.x), round(e.y)))
        return
    w, h = max(round(e.w), 1), max(round(e.h), 1)
    if e.fit == "cover":
        fitted = _cover_fit(layer, (w, h))
    elif e.fit == "contain":
        ratio = min(w / layer.width, h / layer.height)
        nw, nh = max(round(layer.width * ratio), 1), max(round(layer.height * ratio), 1)
        fitted = layer.convert("RGBA").resize((nw, nh))
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(fitted, ((w - nw) // 2, (h - nh) // 2))
        fitted = canvas
    else:
        fitted = layer.convert("RGBA").resize((w, h))
    if e.mask in ("rounded", "circle", "arch"):
        m = Image.new("L", (w, h), 0)
        md = ImageDraw.Draw(m)
        if e.mask == "rounded":
            md.rounded_rectangle([0, 0, w - 1, h - 1], radius=min(w, h) * 0.12, fill=255)
        else:  # circle / arch：椭圆蒙版（arch 的拱形近似）
            md.ellipse([0, 0, w - 1, h - 1], fill=255)
        img.paste(fitted, (round(e.x), round(e.y)), m)
    else:
        img.alpha_composite(fitted, (round(e.x), round(e.y)))


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
