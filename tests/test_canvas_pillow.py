"""F70-T5 C 路尺寸恒精确 + RGB 白底（core/visual/canvas/pillow_render.py）。

F70-T6（B vs C 像素抽样对拍）为 @pytest.mark.browser 用例——缺浏览器
（playwright / infra 光栅模块）时 importorskip 呈 skip 非 fail。
"""
from __future__ import annotations

import pytest

from musespiral.core.visual.canvas.layout import layout_document
from musespiral.core.visual.canvas.model import SPECS, CanvasDoc, Element, TextBlock
from musespiral.core.visual.canvas.pillow_render import render
from musespiral.core.visual.canvas.shapes import ring
from musespiral.core.visual.canvas.templates import REGISTRY, resolve


# ── F70-T5 尺寸恒精确 + RGB ──────────────────────────────────


@pytest.mark.parametrize("spec", ["mp", "xhs-3x4", "xhs-4x5", "square", "wide16x9"])
def test_f70t5_size_exact_and_rgb(spec: str) -> None:
    w, h = SPECS[spec]
    doc = CanvasDoc(w, h, elements=[Element("rect", x=0, y=0, w=w, h=h, fill="#ff0000")])
    img = render(doc)
    assert img.size == (w, h)
    assert img.mode == "RGB"


def test_default_background_is_white() -> None:
    img = render(CanvasDoc(50, 50))
    assert img.getpixel((0, 0)) == (255, 255, 255)  # _to_rgb 白底纪律同 cover


def test_custom_background_pixel() -> None:
    img = render(CanvasDoc(50, 50, background="#102030"))
    assert img.getpixel((25, 25)) == (16, 32, 48)


def test_rect_fill_pixel() -> None:
    img = render(CanvasDoc(100, 100, elements=[
        Element("rect", x=10, y=10, w=50, h=50, fill="#ff0000")
    ]))
    assert img.getpixel((35, 35)) == (255, 0, 0)


def test_circle_fill_pixel() -> None:
    img = render(CanvasDoc(100, 100, elements=[
        Element("circle", cx=50, cy=50, r=30, fill="#00ff00")
    ]))
    assert img.getpixel((50, 50)) == (0, 255, 0)


def test_line_pixel() -> None:
    img = render(CanvasDoc(100, 100, elements=[
        Element("line", x1=10, y1=50, x2=90, y2=50, stroke="#0000ff", stroke_width=3)
    ]))
    assert img.getpixel((50, 50)) == (0, 0, 255)


def test_text_drawn_visible() -> None:
    doc = CanvasDoc(200, 100, elements=[
        Element("text", x=10, y=60, w=180, text=TextBlock("Hello Pillow", font_size=40))
    ])
    layout_document(doc)
    img = render(doc)
    colors = {img.getpixel((x, y)) for x in range(5, 195, 3) for y in range(5, 95, 3)}
    assert (255, 255, 255) in colors
    assert len(colors) > 1  # 有非背景像素（文字墨迹）


def test_g_children_drawn() -> None:
    img = render(CanvasDoc(100, 100, elements=[
        Element("g", children=[Element("rect", x=20, y=20, w=60, h=60, fill="#123456")])
    ]))
    assert img.getpixel((50, 50)) == (18, 52, 86)


def test_ring_dasharray_drawn_on_circumference() -> None:
    """ring 弧段（dasharray 约定）在 C 路落墨：首段起点=12 点方向。"""
    els = ring([0.25], cx=50, cy=50, r=30, thickness=8)
    img = render(CanvasDoc(100, 100, elements=els))
    assert img.getpixel((50, 20)) != (255, 255, 255)  # 12 点方向圆环上有墨
    assert img.getpixel((50, 50)) == (255, 255, 255)  # 环心透空


def test_unsupported_path_skipped_not_crash() -> None:
    img = render(CanvasDoc(50, 50, elements=[
        Element("path", d="M 0 0 C 10 10 20 20 30 30", stroke="#000000")
    ]))
    assert img.size == (50, 50)  # 曲线 path 跳过，渲染不崩


# ── F70-T6 B vs C 像素抽样对拍（@browser；缺浏览器 skip 非 fail）──


@pytest.mark.browser
def test_f70t6_browser_vs_pillow_pixel_tolerance() -> None:
    pytest.importorskip("playwright")
    raster = pytest.importorskip("musespiral.infra.visual.raster")

    from musespiral.core.visual.canvas.svg import render_svg

    colors = {"primary": "#2563eb", "text": "#1f2937", "muted": "#6b7280",
              "background": "#ffffff", "accent": "#dc2626"}
    for spec in ("mp", "xhs-3x4", "square"):
        w, h = SPECS[spec]
        meta, params = resolve(REGISTRY, "vermilion-slab", params={"title": "对拍标题", "hook": "钩子行"})
        doc = layout_document(meta.build(params, colors, spec))
        png = raster.rasterize_svg(render_svg(doc))  # B 路（infra 层提供）
        from PIL import Image
        import io as _io

        with Image.open(_io.BytesIO(png)) as b_img:
            b = b_img.convert("RGB")
        c = render(doc)
        assert b.size == c.size == (w, h)
        for gx in range(5):
            for gy in range(5):
                x, y = round(gx * (w - 1) / 4), round(gy * (h - 1) / 4)
                bb, cc = b.getpixel((x, y)), c.getpixel((x, y))
                assert all(abs(p - q) <= 8 for p, q in zip(bb, cc)), (
                    f"{spec}@({x},{y}): B={bb} C={cc} 超容差 8/通道"
                )
