"""B2 视觉编辑 2.0 测试（三线扩展设计 §二，2026-11-26）。

覆盖面：
  - Element 层级四标量默认值兼容（存量构造零漂移）+ blend 白名单执法；
  - Background 模型：五形态字段校验（http 拒/stop 越界/kind 专属必填）+
    to_dict/from_dict 往返；
  - CanvasDoc.background 联合（str 存量语义/Background/None）+ layers()
    按 z 稳定排序；
  - 渲染器：SVG 背景五形态展开（defs/渐变/纹样/图+蒙纱）+ z 序 + visible
    跳过 + opacity/blend 序列化；Pillow 五背景尺寸恒精确 + 渐变方向断言 +
    合成路径（multiply/opacity）；
  - 模板注册表：6 新模板（params_schema 校验/背景允许种类/元素 z 序/
    image_slot 归一与执法）；article-long 高度自适应 clamp；
  - 服务层：design.json v2 往返（background/elements 执法+落盘形态）+
    v1→v2 读兼容（缺省补默认值）+ render/export 背景随行。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

from musespiral.core.visual.canvas.layout import layout_document, relayout_vertical
from musespiral.core.visual.canvas.model import (
    BACKGROUND_KINDS,
    BLEND_MODES,
    SPECS,
    Background,
    CanvasDoc,
    Element,
    TextBlock,
    validate_doc,
)
from musespiral.core.visual.canvas.pillow_render import render as render_pillow
from musespiral.core.visual.canvas.svg import render_svg
from musespiral.core.visual.canvas.templates import (
    REGISTRY,
    TemplateMeta,
    resolve,
    validate_params,
)
from musespiral.services import visual_service as v

NEW_TEMPLATE_IDS = (
    "article-long", "xhs-cover-pro", "interview-card",
    "list-three", "quote-poster", "chapter-divider",
)

COLORS = {
    "primary": "#2563eb", "text": "#1f2937", "muted": "#6b7280",
    "background": "#ffffff", "accent": "#dc2626", "border": "#e5e7eb",
    "code_bg": "#f3f4f6", "code_text": "#1f2937", "quote_bg": "#eff6ff",
    "quote_border": "#2563eb",
}


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def article(tmp_path: Path) -> Path:
    path = tmp_path / "demo.md"
    path.write_text(
        "---\ntitle: 长文试作\ndigest: 摘\n---\n\n首段钩子。\n\n## 甲\n内容\n",
        encoding="utf-8",
    )
    return path


# ── Element 层级扩展（向后兼容）──────────────────────────────


def test_element_layer_defaults_backward_compatible() -> None:
    """存量构造（只给 kind/几何/样式）→ z=0/opacity=1/locked=False/visible=
    True/blend=normal/fit=''/mask=''——旧模板产 doc 与 B2 前逐字段一致。"""
    e = Element("rect", x=1, y=2, w=3, h=4, fill="#00ff00")
    assert (e.z, e.opacity, e.locked, e.visible, e.blend, e.fit, e.mask) == (
        0, 1.0, False, True, "normal", "", "",
    )
    # 四模板 build 产物全默认（零 z 声明的存量语义）
    for tid in ("vermilion-slab", "indigo-slab", "xhs-card", "points-three"):
        meta = REGISTRY[tid]
        _, params = resolve(REGISTRY, tid, params={"title": "t"})
        doc = meta.build(params, COLORS, meta.specs[0])
        assert all(e.z == 0 and e.blend == "normal" for e in doc.elements), tid


def test_element_blend_whitelist_enforced() -> None:
    with pytest.raises(ValueError, match="混合模式.*normal/multiply/screen/overlay"):
        Element("rect", blend="difference")
    for mode in BLEND_MODES:
        Element("rect", blend=mode)  # 白名单内全过


def test_element_layer_flags_validation() -> None:
    """构造期与 validate_doc（构造后篡改）双门都拦 z/locked/visible 标量。"""
    e = Element("rect")
    e.z = "2"  # type: ignore[assignment]  # 构造后篡改
    with pytest.raises(ValueError, match=r"rect\.z"):
        validate_doc(CanvasDoc(10, 10, elements=[e]))
    e2 = Element("rect")
    e2.locked = "yes"  # type: ignore[assignment]
    with pytest.raises(ValueError, match="locked"):
        validate_doc(CanvasDoc(10, 10, elements=[e2]))
    with pytest.raises(ValueError, match="z.*整数"):
        Element("rect", z=True)  # bool 是 int 子类但非数值语义


def test_element_fit_mask_enums() -> None:
    with pytest.raises(ValueError, match="fit"):
        Element("image", href="a.png", w=1, h=1, fit="stretch")
    with pytest.raises(ValueError, match="mask"):
        Element("image", href="a.png", w=1, h=1, mask="star")


# ── Background 模型 ──────────────────────────────────────────


def test_background_rejects_http_image() -> None:
    """「光栅不触网」在背景层前置：http/https/协议相对一律拒（可读文案）。"""
    for bad in ("https://evil/x.jpg", "http://evil/x.jpg", "//evil/x.jpg"):
        with pytest.raises(ValueError, match="网络地址"):
            Background(kind="image", image=bad)
    # stops 色、overlay_alpha 越界、kind 枚举外
    with pytest.raises(ValueError, match="background.stops"):
        Background(kind="linear", stops=[(0.0, "white"), (1.0, "#000000")])
    with pytest.raises(ValueError, match="overlay_alpha"):
        Background(kind="image", image="a.jpg", overlay_alpha=1.5)
    with pytest.raises(ValueError, match="background.kind"):
        Background(kind="mesh")


def test_background_kind_specific_required_fields() -> None:
    """linear/radial 必给 stops（≥2 锚点）；image 必给 image——构造期即拒。"""
    with pytest.raises(ValueError, match="stops"):
        Background(kind="linear")
    with pytest.raises(ValueError, match="stops"):
        Background(kind="radial", color="#ffffff")
    with pytest.raises(ValueError, match="image"):
        Background(kind="image")


def test_background_dict_roundtrip() -> None:
    bg = Background(kind="linear", stops=[(0.0, "#ffffff"), (1.0, "#1e293b")], angle=90)
    d = bg.to_dict()
    assert d["stops"] == [[0.0, "#ffffff"], [1.0, "#1e293b"]]
    assert Background.from_dict(d).to_dict() == d
    # 未知键拒收（design.json v2 反序列化执法面）
    with pytest.raises(ValueError, match="未知键"):
        Background.from_dict({"kind": "solid", "evil": 1})


def test_canvasdoc_background_union() -> None:
    """str 存量语义/Background/None 三态：None=透明底（SVG 不发底 rect）。"""
    assert CanvasDoc(10, 10, "#123456").background == "#123456"
    bg = Background(kind="pattern", pattern="grid")
    assert CanvasDoc(10, 10, bg).background is bg
    assert CanvasDoc(10, 10, None).background is None
    with pytest.raises(ValueError, match="doc.background"):
        CanvasDoc(10, 10, background=123)  # type: ignore[arg-type]
    # 坏 Background 构造后塞 doc → validate_doc 拦
    doc = CanvasDoc(10, 10, Background(kind="pattern"))
    doc.background.kind = "mesh"  # type: ignore[misc]  # 构造后篡改
    with pytest.raises(ValueError, match="background.kind"):
        validate_doc(doc)


# ── z 排序（layers 稳定序）───────────────────────────────────


def test_layers_sorted_by_z_stable() -> None:
    """按 z 升序；同 z 保文档序（存量模板零 z → 输出恒等于 doc.elements）。"""
    a, b, c, d = (Element("rect", fill="#111111", z=2),
                  Element("rect", fill="#222222", z=0),
                  Element("rect", fill="#333333", z=2),
                  Element("rect", fill="#444444", z=-1))
    doc = CanvasDoc(10, 10, elements=[a, b, c, d])
    assert doc.layers() == [d, b, a, c]
    # 全零 z → 恒等（旧稿渲染序不变）
    flat = CanvasDoc(10, 10, elements=[a, b, c, d])
    for e in flat.elements:
        e.z = 0
    assert flat.layers() == flat.elements


# ── 渲染器：SVG 背景五形态 + z/visible/opacity/blend ──────────


def test_svg_background_solid_unchanged_v1() -> None:
    """str 背景序列化面逐字节旧形态（F72 锚不回归）。"""
    out = render_svg(CanvasDoc(100, 100, background="#123456"))
    assert out.splitlines()[1] == '  <rect x="0" y="0" width="100" height="100" fill="#123456"/>'


@pytest.mark.parametrize("bg,markers", [
    (Background(kind="linear", stops=[(0.0, "#ffffff"), (1.0, "#1e293b")], angle=135),
     ["<linearGradient", 'fill="url(#dvbg)"', "<stop "]),
    (Background(kind="radial", stops=[(0.0, "#ffffff"), (1.0, "#111111")]),
     ["<radialGradient", 'fill="url(#dvbg)"']),
    (Background(kind="pattern", pattern="waves", color="#eff6ff"),
     ["<pattern ", 'fill="#eff6ff"', 'fill="url(#dvbg)"']),
    (Background(kind="image", image="a/b.jpg", overlay_alpha=0.35, color="#000000"),
     ['href="a/b.jpg"', 'opacity="0.35"', 'fill="#000000"']),
])
def test_svg_background_four_kinds_expand(bg: Background, markers: list[str]) -> None:
    """Background 五形态（solid 同 v1）：defs 引用层 + 结构标记 class="dvbg"。"""
    out = render_svg(CanvasDoc(200, 150, background=bg))
    ET.fromstring(out)  # 良构
    for m in markers:
        assert m in out, (m, out[:300])
    # 背景节点打 class=dvbg（编辑器图层收集跳过面）
    assert 'class="dvbg"' in out


def test_svg_z_order_visible_opacity_blend() -> None:
    doc = CanvasDoc(100, 100, elements=[
        Element("rect", x=0, y=0, w=10, h=10, z=5, fill="#ff0000"),
        Element("rect", x=0, y=0, w=10, h=10, z=1, fill="#00ff00", visible=False),
        Element("text", x=0, y=20, w=50, z=3, text=TextBlock("a"),
                blend="multiply", opacity=0.5),
        Element("image", x=0, y=0, w=30, h=30, href="pic.png",
                fit="cover", mask="circle", z=4),
    ])
    out = render_svg(doc)
    ET.fromstring(out)
    assert "#00ff00" not in out  # visible=False 跳过
    assert "mix-blend-mode:multiply" in out and 'opacity="0.5"' in out
    assert 'preserveAspectRatio="xMidYMid slice"' in out
    assert 'clip-path="url(#dvmask)"' in out and "<clipPath" in out
    assert out.index("<text") < out.index("<image") < out.index('fill="#ff0000"')  # z 升序


def test_svg_solid_background_keeps_tag_whitelist() -> None:
    """产物过 verify_svg（B2 标签扩容后：defs/gradient/stop/pattern/clipPath 合法，
    script/foreignObject 仍拒——F72-T5 语义不变）。"""
    bg = Background(kind="linear", stops=[(0.0, "#ffffff"), (1.0, "#dbeafe")])
    svg = render_svg(CanvasDoc(120, 80, background=bg, elements=[
        Element("image", x=1, y=1, w=20, h=20, href="p.png", mask="rounded"),
    ]))
    assert v.verify_svg(svg) == svg
    bad = svg.replace("<defs", '<script>alert(1)</script><defs', 1)
    with pytest.raises(ValueError, match="SVG 元素越界"):
        v.verify_svg(bad)


# ── 渲染器：Pillow 背景五形态 + 合成 ─────────────────────────


@pytest.mark.parametrize("bg", [
    None, "#123456", Background(kind="solid", color="#abcdef"),
    Background(kind="linear", stops=[(0.0, "#000000"), (1.0, "#ffffff")], angle=90),
    Background(kind="radial", stops=[(0.0, "#ffffff"), (1.0, "#000000")]),
    Background(kind="pattern", pattern="dots"),
    Background(kind="image", image="missing.jpg"),  # 图缺失→底色兜底不崩
])
def test_pillow_background_size_exact(bg: object) -> None:
    """C 路背景五形态：尺寸恒 (w,h)（图缺失安全兜底）。"""
    im = render_pillow(CanvasDoc(120, 80, background=bg))  # type: ignore[arg-type]
    assert im.size == (120, 80) and im.mode == "RGB"


def test_pillow_linear_gradient_direction_by_angle() -> None:
    """angle=90 顶黑底白 / angle=0 左黑右白（平滑插值端点断言）。"""
    stops = [(0.0, "#000000"), (1.0, "#ffffff")]
    down = render_pillow(CanvasDoc(10, 40, background=Background(
        kind="linear", stops=stops, angle=90)))
    assert down.getpixel((5, 1))[0] < 40 and down.getpixel((5, 38))[0] > 215
    right = render_pillow(CanvasDoc(40, 10, background=Background(
        kind="linear", stops=stops, angle=0)))
    assert right.getpixel((1, 5))[0] < 40 and right.getpixel((38, 5))[0] > 215


def test_pillow_opacity_and_blend_composite() -> None:
    doc = CanvasDoc(60, 60, elements=[
        Element("rect", x=0, y=0, w=60, h=60, fill="#000000", z=0),
        Element("rect", x=10, y=10, w=20, h=20, fill="#ffffff", z=5, blend="multiply"),
        Element("rect", x=30, y=30, w=20, h=20, fill="#ffffff", z=1, visible=False),
    ])
    im = render_pillow(doc)
    assert im.getpixel((15, 15))[0] < 30  # multiply 白×黑=黑
    assert im.getpixel((40, 40))[0] < 30  # 隐藏元素不画
    # opacity=0.5 红 50% 叠白底 → 粉 (255,128,128)
    doc2 = CanvasDoc(20, 20, elements=[
        Element("rect", x=0, y=0, w=20, h=20, fill="#ff0000", opacity=0.5)])
    px = render_pillow(doc2).getpixel((10, 10))
    assert px[0] > 200 and 100 < px[1] < 160 and 100 < px[2] < 160


def test_pillow_image_fit_cover_and_mask() -> None:
    """fit=cover 缩放到声明矩形 + mask=circle 蒙版贴入（C 路视觉近似清单）。"""
    src = Image.new("RGB", (60, 20), (255, 0, 0))  # 3:1 横图
    import io
    import base64
    buf = io.BytesIO()
    src.save(buf, format="PNG")
    href = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    doc = CanvasDoc(40, 40, elements=[
        Element("image", x=0, y=0, w=40, h=40, href=href, fit="cover", mask="circle")])
    im = render_pillow(doc)
    assert im.size == (40, 40)
    assert im.getpixel((20, 20))[0] > 200  # 圆心红
    assert im.getpixel((1, 1))[2] > 200    # 角落被蒙版抠掉→白底


# ── 模板注册表：6 新模板 ─────────────────────────────────────


def test_registry_has_ten_templates() -> None:
    assert len(REGISTRY) == 10
    assert set(NEW_TEMPLATE_IDS) <= set(REGISTRY)
    for tid in NEW_TEMPLATE_IDS:
        meta = REGISTRY[tid]
        assert isinstance(meta, TemplateMeta)
        assert meta.backgrounds and set(meta.backgrounds) <= set(BACKGROUND_KINDS)


@pytest.mark.parametrize("tid", NEW_TEMPLATE_IDS)
def test_new_template_params_schema_validation(tid: str) -> None:
    """resolve 默认值全链（零 params）→ build → layout → svg 良构；
    schema 违例可读 ValueError（枚举/色槽）。"""
    meta = REGISTRY[tid]
    _m, params = resolve(REGISTRY, tid)
    doc = layout_document(meta.build(params, COLORS, meta.specs[0]))
    ET.fromstring(render_svg(doc))
    assert doc.elements
    # 违例面：background_slot 色槽限定（每模板都有该槽）
    with pytest.raises(ValueError, match="色槽"):
        resolve(REGISTRY, tid, params={"background_slot": "hotpink"})


def test_new_templates_declare_element_z_order() -> None:
    """新模板元素显式 z 声明（≥2 级层级——图层列表可调序的前提）。
    默认 params 即产全骨架（空文案也建元素——版式与内容分离）。"""
    for tid in NEW_TEMPLATE_IDS:
        meta = REGISTRY[tid]
        _m, params = resolve(REGISTRY, tid)
        doc = meta.build(params, COLORS, meta.specs[0])
        zs = {e.z for e in doc.elements}
        assert len(zs) >= 2, f"{tid} 元素未分层（z 集={zs}）"


def test_article_long_height_adaptive_clamped() -> None:
    """article-long：宽恒 1080；空内容下限 1400、长内容不越 8000 上限。"""
    meta = REGISTRY["article-long"]
    _, p0 = resolve(REGISTRY, "article-long")
    d0 = meta.build(p0, COLORS, "article-long")
    assert d0.width == 1080 and d0.height >= 1400
    long_params = {
        "title": "题" * 120,
        "subtitle": "副" * 200,
        "paragraphs": "\n\n".join("正文段落。" * 40 for _ in range(8)),
    }
    _, p1 = resolve(REGISTRY, "article-long", params=long_params)
    d1 = layout_document(meta.build(p1, COLORS, "article-long"))
    assert d1.height <= 8000
    assert SPECS["article-long"] == (1080, 8000)


def test_interview_card_circle_avatar_slot() -> None:
    """interview-card 头像槽：circle 蒙版 + 环描边；空槽出占位环。"""
    meta = REGISTRY["interview-card"]
    _, params = resolve(REGISTRY, "interview-card",
                        params={"title": "引语", "author": "作者", "role": "角色"})
    doc = meta.build(params, COLORS, meta.specs[0])
    circles = [e for e in doc.elements if e.kind == "circle"]
    assert circles and circles[0].stroke  # 环描边存在
    _, p2 = resolve(REGISTRY, "interview-card", params={
        "title": "引语", "avatar": {"src": "a/b.png", "fit": "cover", "mask": "circle"}})
    doc2 = meta.build(p2, COLORS, meta.specs[0])
    img = next(e for e in doc2.elements if e.kind == "image")
    assert img.mask == "circle" and img.fit == "cover"


# ── image_slot schema ────────────────────────────────────────


def test_image_slot_accepts_bare_string_and_object() -> None:
    """裸串与 {src,fit,mask} 对象两形态都收，归一为后者（规则默认兜底）。"""
    _, p = resolve(REGISTRY, "article-long", params={"hero_image": "a/b.jpg"})
    assert p["hero_image"] == {"src": "a/b.jpg", "fit": "cover", "mask": "rounded"}
    _, p2 = resolve(REGISTRY, "article-long", params={
        "hero_image": {"src": "data:image/png;base64,AAAA", "fit": "contain", "mask": "none"}})
    assert p2["hero_image"]["fit"] == "contain" and p2["hero_image"]["mask"] == "none"


def test_image_slot_rejects_http_and_bad_keys() -> None:
    with pytest.raises(ValueError, match="网络地址"):
        resolve(REGISTRY, "article-long", params={"hero_image": "https://x/y.jpg"})
    with pytest.raises(ValueError, match="未知键"):
        resolve(REGISTRY, "article-long", params={"hero_image": {"src": "a.png", "evil": 1}})
    with pytest.raises(ValueError, match="fit"):
        resolve(REGISTRY, "interview-card", params={"avatar": {"fit": "stretch"}})
    with pytest.raises(ValueError, match="mask"):
        resolve(REGISTRY, "interview-card", params={"avatar": {"mask": "star"}})
    with pytest.raises(ValueError, match="图片引用字符串"):
        resolve(REGISTRY, "article-long", params={"hero_image": 42})


def test_image_slot_schema_declares_bind_fit_mask() -> None:
    """模板 schema 的 image_slot 规则带 bind/fit/mask 声明（设计 §2.2 形态）。"""
    rule = REGISTRY["article-long"].params_schema["hero_image"]
    assert rule["type"] == "image_slot"
    assert rule["bind"] == "material.image"
    assert rule["fit"] == "cover" and rule["mask"] == "rounded"
    assert REGISTRY["interview-card"].params_schema["avatar"]["mask"] == "circle"


# ── 服务层：design.json v2 + v1 兼容 ─────────────────────────


def test_save_design_v2_roundtrip(article: Path) -> None:
    """v2 落盘形态 {version:2, background, elements, params, spec}；读回归一。"""
    r = v.save_design(
        article, kind="article", template="article-long", spec="article-long",
        params={"title": "标题", "paragraphs": "段一。"},
        background={"kind": "linear", "color": "#ffffff",
                    "stops": [[0.0, "#ffffff"], [1.0, "#dbeafe"]], "angle": 120},
        elements=[{"i": 0, "z": 2, "opacity": 0.8, "locked": True, "blend": "multiply",
                   "dx": 8, "dy": -4}],
    )
    raw = json.loads((Path(r["dir"]) / "design.json").read_text(encoding="utf-8"))
    assert raw["version"] == 2
    assert raw["background"]["kind"] == "linear" and raw["background"]["angle"] == 120
    assert raw["elements"][0]["blend"] == "multiply" and raw["elements"][0]["dx"] == 8
    got = v.load_design(article)
    assert got["version"] == 2
    assert got["elements"][0]["z"] == 2 and got["elements"][0]["visible"] is True


def test_load_design_v1_fills_defaults(article: Path) -> None:
    """v1 老稿（无 version 键）读侧补默认：version=1/background=None/elements=[]。"""
    d = v.design_dir(article)
    d.mkdir(parents=True)
    (d / "design.json").write_text(json.dumps({
        "kind": "social", "template": "xhs-card",
        "params": {"title": "老稿"}, "spec": "xhs-3x4", "svg": "social-1.svg",
    }, ensure_ascii=False), encoding="utf-8")
    got = v.load_design(article)
    assert got["version"] == 1
    assert got["background"] is None and got["elements"] == []
    assert got["params"]["title"] == "老稿"


def test_save_design_v2_rejects_bad_layer_and_background(article: Path) -> None:
    with pytest.raises(ValueError, match="blend"):
        v.save_design(article, kind="article", template="article-long",
                      spec="article-long", params={}, elements=[{"i": 0, "blend": "xor"}])
    with pytest.raises(ValueError, match="未知键"):
        v.save_design(article, kind="article", template="article-long",
                      spec="article-long", params={}, elements=[{"i": 0, "evil": 1}])
    with pytest.raises(ValueError, match="网络地址"):
        v.save_design(article, kind="social", template="xhs-cover-pro",
                      spec="square", params={},
                      background={"kind": "image", "image": "https://x/y.jpg"})
    # 模板允许集：vermilion-slab 只 solid
    with pytest.raises(ValueError, match="不允许背景种类"):
        v.save_design(article, kind="cover", template="vermilion-slab", spec="mp",
                      params={}, background={"kind": "linear",
                                             "stops": [[0, "#fff"], [1, "#000"]]})


def test_layer_fill_roundtrip_and_hex_gate(article: Path) -> None:
    """图层行 fill（文字客户端改色）往返 + hex 形态执法（编辑器 dvLayersOut 会
    带 fill 落键——服务端不能把它当未知键拒掉）。"""
    r = v.save_design(article, kind="social", template="xhs-card", spec="xhs-3x4",
                      params={"title": "色"},
                      elements=[{"i": 0, "z": 0, "fill": "#dc2626"}])
    got = v.load_design(article)
    assert got["elements"][0]["fill"] == "#dc2626"
    with pytest.raises(ValueError, match="fill.*hex"):
        v.save_design(article, kind="social", template="xhs-card", spec="xhs-3x4",
                      params={}, elements=[{"i": 0, "fill": "red"}])
    assert Path(r["dir"]).exists()  # 违例零落盘前的首稿仍在


def test_render_with_background_param(article: Path) -> None:
    """render(background=…) → SVG 展开五形态背景；None=模板槽色底（v1 语义）。"""
    svg, _ = v.render(template="article-long", spec="article-long",
                      params={"title": "带底"},
                      background={"kind": "pattern", "pattern": "dots", "color": "#eff6ff"})
    assert "<pattern " in svg and 'fill="#eff6ff"' in svg
    svg2, _ = v.render(template="article-long", spec="article-long", params={"title": "无底"})
    assert "<pattern " not in svg2  # 缺省=槽色底
    # 允许集执法（render 面同 save 面）
    with pytest.raises(ValueError, match="不允许背景种类"):
        v.render(template="vermilion-slab", spec="mp", params={},
                  background={"kind": "image", "image": "x.jpg"})


def test_export_design_carries_background(article: Path) -> None:
    """export 按设计稿背景重建（v2 背景随行——切规格/导出丢背景属回归）。"""
    v.save_design(article, kind="social", template="xhs-cover-pro", spec="square",
                  params={"title": "底色"},
                  background={"kind": "radial",
                              "stops": [[0.0, "#ffffff"], [1.0, "#dbeafe"]]})
    ex = v.export_design(article, kind="social", route="pillow")
    assert ex["route"] == "C" and ex["size"] == [1080, 1080]
    # 渲染产物真含径向渐变（读 design.json 的 background 重建面）
    svg, _ = v.switch_spec(article, new_spec="xhs-3x4")
    assert "<radialGradient" in svg


def test_export_file_name_article_kind(tmp_path: Path) -> None:
    art = tmp_path / "x.md"
    art.write_text("x", encoding="utf-8")
    assert v.export_file_name(art, "article") == "x.article.png"


def test_templates_api_payload_ten_items() -> None:
    """GET /api/visual/templates 数据面（visual_payload）：10 模板 + backgrounds。"""
    from musespiral.studio.visual_payload import _visual_templates_payload

    payload = _visual_templates_payload()
    ids = {row["id"] for row in payload["items"]}
    assert len(payload["items"]) == 10
    assert set(ids) == set(REGISTRY)
    for row in payload["items"]:
        assert row["backgrounds"] and set(row["backgrounds"]) <= set(BACKGROUND_KINDS)
    old = {row["id"]: row for row in payload["items"]}
    assert old["vermilion-slab"]["backgrounds"] == ["solid"]
    assert "image" in old["xhs-cover-pro"]["backgrounds"]


def test_svg_render_rejects_smuggled_network_style() -> None:
    """verify_svg 的 URL 面覆盖 clip-path（B2 新引用面）——网络引用拒。"""
    bad = ('<svg xmlns="http://www.w3.org/2000/svg"><image '
           'clip-path="url(http://evil/x.svg#f)"/></svg>')
    with pytest.raises(ValueError):
        v.verify_svg(bad)
