"""F74 视觉模板系统 — 注册表+四内置+params schema 校验（L2 纯层）。

- ``TemplateMeta.build(params, colors, spec) -> CanvasDoc``：colors=服务层经
  themes 解析后的语义色槽 hex（themes 是全 visual 域唯一色板事实源——本层
  只收色值不读主题文件，纯度纪律）；
- 四级合并 ``resolve``：default < preset.params < params < params_overrides
  （F74-T2；overrides 胜出——§1.1 读序）；
- schema 子集：str/enum/int/color_slot/list/image_slot（list=F73 数组槽，数据行
  {label, value}；image_slot=B2 图片槽——值为裸引用串或 {src,fit,mask} 对象，
  归一为后者，引用面经 model.check_image_ref 拒 http）；违例抛可读
  ValueError 含槽名与合法枚举清单（F74-T3）；
- 内置四模板：vermilion-slab/indigo-slab（封面×2，F71）、xhs-card（社媒，
  F72 默认稿）、points-three（三要点卡信息图，F73）；
- B2（2026-11-26，三线扩展设计 §2.3）新增六模板：article-long（图文长页，
  kind=article，自适应高≤8000）/ xhs-cover-pro（小红书封面）/ interview-card
  （访谈卡，圆形头像槽）/ list-three（三点卡，points-three 升级版）/
  quote-poster（金句海报）/ chapter-divider（章节分隔）。每模板经
  ``TemplateMeta.backgrounds`` 声明允许背景种类、元素 z 序在 build 内显式。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from . import shapes
from .model import (
    IMAGE_FITS,
    IMAGE_MASKS,
    SEMANTIC_COLOR_SLOTS,
    SPECS,
    CanvasDoc,
    Element,
    TextBlock,
    check_image_ref,
)

_ALIGN = ("left", "center", "right")
_FONT_ENUM = ("S", "M", "L")
# 字号基准（1242 宽的像素基准，其余 spec 按宽等比——cover.py 56/40 比例族同源）
FONT_STEPS = {"S": 40, "M": 56, "L": 72}
HOOK_STEPS = {"S": 28, "M": 40, "L": 52}


@dataclass(frozen=True)
class TemplateMeta:
    """模板注册元数据：build(params, colors, spec) 产白名单 CanvasDoc。

    B2：``backgrounds`` 声明该模板允许的背景种类（Background.kind 白名单子集
    ——编辑器右栏背景面板据此渲染可切换类型；服务层 _apply_background 执法）。
    """

    id: str
    kind: str
    specs: tuple[str, ...]
    params_schema: dict[str, dict[str, Any]]
    slot_colors: dict[str, str]
    build: Callable[[dict[str, Any], dict[str, str], str], CanvasDoc]
    backgrounds: tuple[str, ...] = ("solid",)


def _check_points(value: object, where: str) -> list[dict[str, Any]]:
    """list 数据槽（F73 数组槽）：数据行 {label, value}，value 数值。"""
    if not isinstance(value, list):
        raise ValueError(f"{where}: 应为数据行列表，得到 {type(value).__name__}")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or "label" not in item or "value" not in item:
            raise ValueError(f"{where}[{i}]: 数据行应为 {{label, value}}，得到 {item!r}")
        v = item["value"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
            raise ValueError(f"{where}[{i}].value: 应为非负数值，得到 {v!r}")
        out.append({"label": str(item["label"]), "value": float(v)})
    return out


def _check_image_slot(value: object, rule: dict[str, Any], where: str) -> dict[str, Any]:
    """image_slot 槽值执法（B2）。

    值形态两可：裸引用串（CLI/旧稿友好）或 ``{src, fit, mask}`` 对象（编辑器
    往返）；一律归一为后者——fit/mask 缺省取规则默认（模板 schema 声明的
    ``bind/fit/mask`` 键即默认值面）。引用面（src）经 model.check_image_ref
    拒 http（「光栅不触网」单一执法面，与 Element.href 同源不复制）。
    """
    default_fit = str(rule.get("fit") or "cover")
    default_mask = str(rule.get("mask") or "none")
    src: str
    fit: str = default_fit
    mask: str = default_mask
    if isinstance(value, str):
        src = value
    elif isinstance(value, dict):
        unknown = [k for k in value if k not in ("src", "fit", "mask")]
        if unknown:
            raise ValueError(f"{where}: 未知键 {unknown}（image_slot 只接受 src/fit/mask）")
        raw_src = value.get("src", "")
        if not isinstance(raw_src, str):
            raise ValueError(f"{where}.src: 应为图片引用字符串（dataURI/本地相对路径）")
        src = raw_src
        if "fit" in value:
            fit = str(value["fit"])
        if "mask" in value:
            mask = str(value["mask"])
    else:
        raise ValueError(
            f"{where}: 应为图片引用字符串或 {{src,fit,mask}} 对象，"
            f"得到 {type(value).__name__}"
        )
    if src:
        check_image_ref(src, f"{where}.src")
    if fit not in IMAGE_FITS:
        raise ValueError(f"{where}.fit: 非法填充 {fit!r}（可选: {'/'.join(IMAGE_FITS)}）")
    if mask not in IMAGE_MASKS:
        raise ValueError(f"{where}.mask: 非法蒙版 {mask!r}（可选: {'/'.join(IMAGE_MASKS)}）")
    return {"src": src, "fit": fit, "mask": mask}


def validate_params(schema: dict[str, dict[str, Any]], params: dict[str, Any],
                    *, where: str = "params") -> dict[str, Any]:
    """schema 校验：类型/枚举/色槽/数据行/图片槽违例 → 可读 ValueError（含槽名+合法值）。"""
    out: dict[str, Any] = {}
    for slot, rule in schema.items():
        if slot not in params:
            if rule.get("required"):
                raise ValueError(f"{where}: 缺必填槽 {slot!r}")
            continue
        value = params[slot]
        kind = rule.get("type", "str")
        if kind == "enum":
            legal = tuple(rule.get("enum", ()))
            if value not in legal:
                raise ValueError(
                    f"{where}.{slot}: 非法值 {value!r}（合法枚举: {'/'.join(map(str, legal))}）"
                )
        elif kind == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{where}.{slot}: 应为整数，得到 {value!r}")
        elif kind == "color_slot":
            if value not in SEMANTIC_COLOR_SLOTS:
                raise ValueError(
                    f"{where}.{slot}: 非法色槽 {value!r}"
                    f"（合法色槽: {'/'.join(SEMANTIC_COLOR_SLOTS)}）"
                )
        elif kind == "list":
            out[slot] = _check_points(value, f"{where}.{slot}")
            continue
        elif kind == "image_slot":
            out[slot] = _check_image_slot(value, rule, f"{where}.{slot}")
            continue
        elif not isinstance(value, str):  # str
            raise ValueError(f"{where}.{slot}: 应为字符串，得到 {type(value).__name__}")
        out[slot] = value
    unknown = [k for k in params if k not in schema]
    if unknown:
        raise ValueError(f"{where}: 未知槽 {unknown}（本模板槽位: {'/'.join(schema)}）")
    return out


def resolve(registry: dict[str, TemplateMeta], template: str, *,
            preset_params: dict[str, Any] | None = None,
            params: dict[str, Any] | None = None,
            overrides: dict[str, Any] | None = None) -> tuple[TemplateMeta, dict[str, Any]]:
    """四级合并 default < preset < params < overrides → (meta, 校验后最终 params)。"""
    meta = registry.get(template)
    if meta is None:
        raise ValueError(f"未知模板: {template!r}（注册表: {'/'.join(sorted(registry))}）")
    merged: dict[str, Any] = {
        slot: rule["default"] for slot, rule in meta.params_schema.items() if "default" in rule
    }
    for layer in (preset_params, params, overrides):
        if layer:
            merged.update(layer)
    return meta, validate_params(meta.params_schema, merged, where=f"模板 {meta.id}")


# ── 构造器（每枚 ≤50 行；色值经 colors 注入、规格经 SPECS 取——零平行表）──


def _font_px(step: int, spec_w: int) -> int:
    return max(12, round(spec_w * step / 1242))


def _build_slab(p: dict[str, Any], colors: dict[str, str], spec: str, *,
                bar_color_key: str, bar_bottom: bool) -> CanvasDoc:
    """封面厚板版式（vermilion/indigo 共用）：强调条 + 大标题 + 钩子副标。"""
    w, h = SPECS[spec]
    margin = max(48, round(w * 96 / 1242))
    bar_w, bar_h = round(w * 96 / 1242), max(4, round(w * 12 / 1242))
    fs = _font_px(FONT_STEPS[p["font_size"]], w)
    hs = _font_px(HOOK_STEPS[p["font_size"]], w)
    bar_y = h - margin - bar_h if bar_bottom else margin
    title_y = margin if bar_bottom else margin + bar_h + round(margin * 0.5)
    els = [
        Element("rect", x=margin, y=bar_y, w=bar_w, h=bar_h, rx=bar_h / 2,
                fill=colors[bar_color_key]),
        Element("text", x=margin, y=title_y + fs, w=w - 2 * margin,
                text=TextBlock(p["title"], font_size=fs, color=colors["text"],
                               align=p["align"], line_height=1.35)),
    ]
    if p["hook"]:
        els.append(Element("text", x=margin, y=title_y + round(fs * 2.6), w=w - 2 * margin,
                           text=TextBlock(p["hook"], font_size=hs, color=colors["muted"],
                                          align=p["align"], line_height=1.5)))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_xhs_card(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """小红书社媒卡：话题药丸 + 大标题 + 钩子 + 底部强调条。"""
    w, h = SPECS[spec]
    margin = round(w * 96 / 1242)
    fs = _font_px(FONT_STEPS[p["font_size"]] + 8, w)
    pill_w, pill_h = round(w * 0.30), round(fs * 0.72)
    bar_h = max(4, round(w * 12 / 1242))
    els = [
        Element("rect", x=margin, y=margin, w=pill_w, h=pill_h, rx=pill_h / 2,
                fill=colors["accent"]),
        Element("text", x=margin, y=margin + round(pill_h * 0.72), w=pill_w,
                text=TextBlock(p["tag"], font_size=round(fs * 0.42), color="#ffffff",
                               align="center")),
        Element("text", x=margin, y=h * 0.38, w=w - 2 * margin,
                text=TextBlock(p["title"], font_size=fs, color=colors["text"],
                               align=p["align"], line_height=1.4)),
        Element("text", x=margin, y=h * 0.38 + round(fs * 2.2), w=w - 2 * margin,
                text=TextBlock(p["hook"], font_size=round(fs * 0.55), color=colors["muted"],
                               align=p["align"])),
        Element("rect", x=margin, y=h - margin - bar_h, w=round(w * 96 / 1242), h=bar_h,
                rx=bar_h / 2, fill=colors["primary"]),
    ]
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_points_three(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """三要点卡信息图：标题 + 左标签右条形（bar 宽∝value）+ 脚注；空数据空态。"""
    w, h = SPECS[spec]
    margin = round(w * 96 / 1242)
    els = [Element("text", x=margin, y=margin + round(w * 56 / 1242), w=w - 2 * margin,
                   text=TextBlock(p["title"], font_size=_font_px(56, w), color=colors["text"]))]
    points = p["points"]
    if not points:  # F73-T4：全零/空数据 → 合法空态（empty 基元+提示文案）
        els.extend(shapes.empty_state(x=margin, y=h * 0.40, w=w - 2 * margin,
                                      h=h * 0.24, color=colors["muted"]))
    else:
        shown = points[:3]
        top, row_h = h * 0.26, min(128.0, h * 0.5 / max(len(shown), 1))
        label_w = round(w * 0.30)
        els.extend(shapes.bar([pt["value"] for pt in shown], x=margin + label_w, y=top,
                              w=w - 2 * margin - label_w, h=row_h * len(shown),
                              gap=row_h * 0.3, bar_color=colors["accent"],
                              track_color=colors["muted"]))
        for i, pt in enumerate(shown):
            els.append(Element("text", x=margin, y=top + i * row_h + round(row_h * 0.6),
                               w=label_w - 12,
                               text=TextBlock(pt["label"], font_size=_font_px(24, w),
                                              color=colors["text"], line_height=1.3)))
    if p["footer"]:
        els.append(Element("text", x=margin, y=h - margin, w=w - 2 * margin,
                           text=TextBlock(p["footer"], font_size=_font_px(20, w),
                                          color=colors["muted"])))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


_COVER_SCHEMA: dict[str, dict[str, Any]] = {
    "title": {"type": "str", "default": ""},
    "hook": {"type": "str", "default": ""},
    "font_size": {"type": "enum", "enum": _FONT_ENUM, "default": "M"},
    "align": {"type": "enum", "enum": _ALIGN, "default": "left"},
    "background_slot": {"type": "color_slot", "default": "background"},
}
_SLOTS = {s: s for s in SEMANTIC_COLOR_SLOTS}
# B2 前的规格全集（article-long 加入 SPECS 后，旧四模板 specs **钉死原五档**
# ——封面/社媒卡宣称长图规格属语义漂移，非本批意图）
_COVER_SPECS = ("mp", "xhs-3x4", "xhs-4x5", "square", "wide16x9")
_ALL_BG = ("solid", "linear", "radial", "pattern", "image")


def _img_slot(p: dict[str, Any], key: str) -> dict[str, Any]:
    """取 image_slot 槽值（validate_params 归一后恒 {src,fit,mask}；str 兜底）。"""
    raw = p.get(key) or {}
    return raw if isinstance(raw, dict) else {"src": str(raw), "fit": "cover", "mask": "none"}


def _est_lines(text: str, fs: int, text_w: int) -> int:
    """行数估算（build 期高度预算用；真实行数由 layout_document 测量回填——
    估算取 CJK 字宽≈fs 的保守上界，宁可画布偏高不可文本溢出）。"""
    if not text:
        return 0
    return max(1, math.ceil(len(text) * fs / max(text_w, 1)))

# ── B2 新六模板构造器（2026-11-26，设计 §2.3；每枚元素 z 序显式声明）──────
# 布局约定：z=0/1 底层（背景图/卡底）、z=2/3 中层（图形装饰）、z=4/5 文案、
# z=6 角标/落款——编辑器图层列表与模板产出同一序。


def _build_article_long(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """图文长页（公众号正文图/小红书多页）：头图+标题+副题+段落族+分隔+落款。

    高度自适应：按内容估行（_est_lines 保守上界）clamp 到 [1400, 8000]
    （SPECS['article-long'] 钉的是上限——自适应是本模板的职责而非规格表）。"""
    w, max_h = SPECS["article-long"]
    m = 72
    tw = w - 2 * m
    hero_h = 430
    fs_t, fs_s, fs_p = 64, 34, 30
    paras = [seg.strip() for seg in (p["paragraphs"] or "").split("\n\n") if seg.strip()]
    est = m + hero_h + 40
    est += _est_lines(p["title"], fs_t, tw) * round(fs_t * 1.35) + 20
    est += _est_lines(p["subtitle"], fs_s, tw) * round(fs_s * 1.45) + 16
    for seg in paras[:8]:
        est += _est_lines(seg, fs_p, tw) * round(fs_p * 1.6) + 30
    est += 90 if p["divider"] else 0
    est += 46 if p["footer"] else 0
    est += m
    h = max(1400, min(max_h, est))
    els: list[Element] = []
    hero = _img_slot(p, "hero_image")
    if hero["src"]:
        els.append(Element("image", x=m, y=m, w=tw, h=hero_h, href=hero["src"],
                           fit=hero["fit"], mask=hero["mask"], z=1))
    else:  # 空槽占位（虚线框+提示——选中后右栏选图即替换）
        els.append(Element("rect", x=m, y=m, w=tw, h=hero_h, rx=18, z=1,
                           stroke=colors.get("border") or "#e5e7eb", stroke_width=2, dasharray="14 10"))
        els.append(Element("text", x=m, y=m + hero_h / 2, w=tw, z=2,
                           text=TextBlock("头图位 · 点击右栏从素材库选图", font_size=30,
                                          color=colors["muted"], align="center")))
    y = m + hero_h + 40
    els.append(Element("text", x=m, y=y + fs_t, w=tw, z=5,
                       text=TextBlock(p["title"], font_size=fs_t, color=colors["text"],
                                      line_height=1.35)))
    y += _est_lines(p["title"], fs_t, tw) * round(fs_t * 1.35) + 20
    if p["subtitle"]:
        els.append(Element("text", x=m, y=y + fs_s, w=tw, z=5,
                           text=TextBlock(p["subtitle"], font_size=fs_s, color=colors["muted"],
                                          line_height=1.45)))
        y += _est_lines(p["subtitle"], fs_s, tw) * round(fs_s * 1.45) + 16
    for seg in paras[:8]:
        els.append(Element("text", x=m, y=y + fs_p, w=tw, z=5,
                           text=TextBlock(seg, font_size=fs_p, color=colors["text"],
                                          line_height=1.6)))
        y += _est_lines(seg, fs_p, tw) * round(fs_p * 1.6) + 30
    if p["divider"]:
        els.append(Element("text", x=m, y=min(y + 30, h - 120), w=tw, z=4,
                           text=TextBlock(p["divider"], font_size=34, color=colors["accent"],
                                          align="center")))
    if p["footer"]:
        els.append(Element("text", x=m, y=h - m, w=tw, z=6,
                           text=TextBlock(p["footer"], font_size=24, color=colors["muted"])))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_xhs_cover_pro(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """小红书封面 Pro：大标题+钩子+角标药丸+底部强调条（叠图背景时文字仍最上）。"""
    w, h = SPECS[spec]
    m = round(w * 96 / 1242)
    fs = _font_px(FONT_STEPS[p["font_size"]] + 8, w)
    els: list[Element] = []
    if p["tag"]:
        pill_w, pill_h = round(w * 0.34), round(fs * 0.66)
        els.append(Element("rect", x=m, y=m, w=pill_w, h=pill_h, rx=pill_h / 2,
                           fill=colors["accent"], z=3))
        els.append(Element("text", x=m, y=m + round(pill_h * 0.7), w=pill_w, z=4,
                           text=TextBlock(p["tag"], font_size=round(fs * 0.4), color="#ffffff",
                                          align="center")))
    els.append(Element("text", x=m, y=round(h * 0.34) + fs, w=w - 2 * m, z=5,
                       text=TextBlock(p["title"], font_size=fs, color=colors["text"],
                                      align=p["align"], line_height=1.35)))
    if p["hook"]:
        els.append(Element("text", x=m, y=round(h * 0.34) + round(fs * 2.4), w=w - 2 * m, z=5,
                           text=TextBlock(p["hook"], font_size=round(fs * 0.5),
                                          color=colors["muted"], align=p["align"])))
    bar_h = max(4, round(w * 14 / 1242))
    els.append(Element("rect", x=m, y=h - m - bar_h, w=round(w * 0.42), h=bar_h,
                       rx=bar_h / 2, fill=colors["primary"], z=2))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_interview_card(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """访谈卡：引号装饰+引语主文+圆形头像槽+署名/头衔。"""
    w, h = SPECS[spec]
    m = round(w * 0.08)
    fs = _font_px(44, w)
    avatar_d = round(w * 0.22)
    els: list[Element] = [
        Element("text", x=m, y=round(h * 0.10), w=w - 2 * m, z=1,
                text=TextBlock("\u201c", font_size=round(fs * 2.6), color=colors["accent"])),
        Element("text", x=m, y=round(h * 0.16) + fs, w=w - 2 * m, z=4,
                text=TextBlock(p["title"], font_size=fs, color=colors["text"], line_height=1.55)),
    ]
    avatar = _img_slot(p, "avatar")
    ax, ay = (w - avatar_d) / 2, h * 0.62
    if avatar["src"]:
        els.append(Element("image", x=ax, y=ay, w=avatar_d, h=avatar_d, href=avatar["src"],
                           fit="cover", mask="circle", z=2))
    els.append(Element("circle", cx=w / 2, cy=ay + avatar_d / 2, r=avatar_d / 2 + 4, z=3,
                       stroke=colors["primary"], stroke_width=3))
    els.append(Element("text", x=m, y=ay + avatar_d + 44, w=w - 2 * m, z=5,
                       text=TextBlock(p["author"] or "——", font_size=_font_px(26, w),
                                      color=colors["text"], align="center")))
    if p["role"]:
        els.append(Element("text", x=m, y=ay + avatar_d + 86, w=w - 2 * m, z=5,
                           text=TextBlock(p["role"], font_size=_font_px(20, w),
                                          color=colors["muted"], align="center")))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_list_three(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """三点卡（points-three 升级）：三块圆角卡底+左标签右条形+标题+角标。"""
    w, h = SPECS[spec]
    m = round(w * 96 / 1242)
    els: list[Element] = [Element("text", x=m, y=m + round(w * 56 / 1242), w=w - 2 * m, z=5,
                                  text=TextBlock(p["title"], font_size=_font_px(56, w),
                                                 color=colors["text"]))]
    if p["tag"]:
        pill_h = round(w * 0.045)
        els.append(Element("rect", x=w - m - round(w * 0.22), y=m, w=round(w * 0.22),
                           h=pill_h, rx=pill_h / 2, fill=colors["accent"], z=3))
        els.append(Element("text", x=w - m - round(w * 0.22), y=m + round(pill_h * 0.7),
                           w=round(w * 0.22), z=4,
                           text=TextBlock(p["tag"], font_size=_font_px(20, w),
                                          color="#ffffff", align="center")))
    points = p["points"]
    if not points:
        els.extend(shapes.empty_state(x=m, y=h * 0.40, w=w - 2 * m, h=h * 0.24,
                                      color=colors["muted"]))
    else:
        shown = points[:3]
        top, row_h = h * 0.26, min(128.0, h * 0.5 / max(len(shown), 1))
        label_w = round(w * 0.30)
        for i in range(len(shown)):  # 卡底（B2 升级：三块圆角底板垫在数据行下）
            els.append(Element("rect", x=m, y=top + i * row_h - 10, w=w - 2 * m,
                               h=row_h - 8, rx=14, z=1, fill=colors.get("quote_bg") or "#eff6ff"))
        els.extend(shapes.bar([pt["value"] for pt in shown], x=m + label_w, y=top,
                              w=w - 2 * m - label_w, h=row_h * len(shown),
                              gap=row_h * 0.3, bar_color=colors["accent"],
                              track_color=colors["muted"]))
        for i, pt in enumerate(shown):
            els.append(Element("text", x=m + 16, y=top + i * row_h + round(row_h * 0.6),
                               w=label_w - 12, z=3,
                               text=TextBlock(pt["label"], font_size=_font_px(24, w),
                                              color=colors["text"], line_height=1.3)))
    if p["footer"]:
        els.append(Element("text", x=m, y=h - m, w=w - 2 * m, z=6,
                           text=TextBlock(p["footer"], font_size=_font_px(20, w),
                                          color=colors["muted"])))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_quote_poster(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """金句海报：大字引语+作者+装饰线（行高 1.6 长文呼吸感）。"""
    w, h = SPECS[spec]
    m = round(w * 0.10)
    fs = _font_px(FONT_STEPS[p["font_size"]] + 8, w)
    els: list[Element] = [
        Element("line", x1=m, y1=h * 0.22, x2=m + round(w * 0.16), y2=h * 0.22, z=2,
                stroke=colors["accent"], stroke_width=6),
        Element("text", x=m, y=round(h * 0.26) + fs, w=w - 2 * m, z=4,
                text=TextBlock(p["title"], font_size=fs, color=colors["text"],
                               align=p["align"], line_height=1.6)),
    ]
    if p["author"]:
        els.append(Element("text", x=m, y=round(h * 0.78), w=w - 2 * m, z=5,
                           text=TextBlock("—— " + p["author"], font_size=_font_px(26, w),
                                          color=colors["muted"], align=p["align"])))
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


def _build_chapter_divider(p: dict[str, Any], colors: dict[str, str], spec: str) -> CanvasDoc:
    """章节分隔：大章节号+标题+装饰线（极简；article-long 内嵌分段用）。"""
    w, h = SPECS[spec]
    if spec == "article-long":
        h = 520  # 分隔页在长图流里是短页（上限内自适应）
    m = round(w * 0.12)
    els: list[Element] = [
        Element("text", x=m, y=round(h * 0.52), w=w - 2 * m, z=1,
                text=TextBlock(p["number"] or "01", font_size=_font_px(120, w),
                               color=colors.get("quote_bg") or "#eff6ff")),
        Element("line", x1=m, y1=round(h * 0.56), x2=m + round(w * 0.30), y2=round(h * 0.56),
                z=2, stroke=colors["accent"], stroke_width=4),
        Element("text", x=m, y=round(h * 0.56) + _font_px(52, w), w=w - 2 * m, z=4,
                text=TextBlock(p["title"], font_size=_font_px(52, w), color=colors["text"])),
    ]
    return CanvasDoc(w, h, colors[p["background_slot"]], els)


_ARTICLE_LONG_SCHEMA: dict[str, dict[str, Any]] = {
    "title": {"type": "str", "default": ""},
    "subtitle": {"type": "str", "default": ""},
    "paragraphs": {"type": "str", "default": ""},
    "hero_image": {"type": "image_slot", "bind": "material.image",
                   "fit": "cover", "mask": "rounded", "default": ""},
    "divider": {"type": "str", "default": "❖"},
    "footer": {"type": "str", "default": ""},
    "background_slot": {"type": "color_slot", "default": "background"},
}
_INTERVIEW_SCHEMA: dict[str, dict[str, Any]] = {
    "title": {"type": "str", "default": ""},  # 引语主文案（主槽位沿用 title 命名）
    "author": {"type": "str", "default": ""},
    "role": {"type": "str", "default": ""},
    "avatar": {"type": "image_slot", "bind": "material.image",
               "fit": "cover", "mask": "circle", "default": ""},
    "background_slot": {"type": "color_slot", "default": "background"},
}
_LIST_THREE_SCHEMA: dict[str, dict[str, Any]] = {
    "title": {"type": "str", "default": ""},
    "tag": {"type": "str", "default": ""},
    "points": {"type": "list", "default": []},
    "footer": {"type": "str", "default": ""},
    "background_slot": {"type": "color_slot", "default": "background"},
}
_QUOTE_SCHEMA: dict[str, dict[str, Any]] = {
    **_COVER_SCHEMA,
    "author": {"type": "str", "default": ""},
}
_CHAPTER_SCHEMA: dict[str, dict[str, Any]] = {
    "number": {"type": "str", "default": "01"},
    "title": {"type": "str", "default": ""},
    "background_slot": {"type": "color_slot", "default": "background"},
}


REGISTRY: dict[str, TemplateMeta] = {
    m.id: m
    for m in (
        TemplateMeta(
            id="vermilion-slab", kind="cover", specs=_COVER_SPECS, params_schema=_COVER_SCHEMA,
            slot_colors=_SLOTS,
            build=lambda p, c, s: _build_slab(p, c, s, bar_color_key="accent", bar_bottom=False),
        ),
        TemplateMeta(
            id="indigo-slab", kind="cover", specs=_COVER_SPECS, params_schema=_COVER_SCHEMA,
            slot_colors=_SLOTS,
            build=lambda p, c, s: _build_slab(p, c, s, bar_color_key="primary", bar_bottom=True),
        ),
        TemplateMeta(
            id="xhs-card", kind="social", specs=("xhs-3x4", "xhs-4x5", "square"),
            params_schema={
                **_COVER_SCHEMA,
                "tag": {"type": "str", "default": "# 研读笔记"},
            },
            slot_colors=_SLOTS, build=_build_xhs_card,
        ),
        TemplateMeta(
            id="points-three", kind="infographic", specs=("wide16x9", "square", "xhs-3x4"),
            params_schema={
                "title": {"type": "str", "default": ""},
                "points": {"type": "list", "default": []},
                "footer": {"type": "str", "default": ""},
                "background_slot": {"type": "color_slot", "default": "background"},
            },
            slot_colors=_SLOTS, build=_build_points_three,
        ),
        # ── B2 视觉编辑 2.0 新六模板（2026-11-26，设计 §2.3）──────────
        TemplateMeta(
            id="article-long", kind="article", specs=("article-long",),
            params_schema=_ARTICLE_LONG_SCHEMA, slot_colors=_SLOTS,
            build=_build_article_long, backgrounds=("solid", "linear", "radial", "pattern"),
        ),
        TemplateMeta(
            id="xhs-cover-pro", kind="social",
            specs=("xhs-3x4", "xhs-4x5", "square"),
            params_schema={**_COVER_SCHEMA, "tag": {"type": "str", "default": ""}},
            slot_colors=_SLOTS, build=_build_xhs_cover_pro, backgrounds=_ALL_BG,
        ),
        TemplateMeta(
            id="interview-card", kind="social", specs=("square", "xhs-3x4"),
            params_schema=_INTERVIEW_SCHEMA, slot_colors=_SLOTS,
            build=_build_interview_card, backgrounds=("solid", "linear", "radial", "pattern"),
        ),
        TemplateMeta(
            id="list-three", kind="infographic",
            specs=("wide16x9", "square", "xhs-3x4"),
            params_schema=_LIST_THREE_SCHEMA, slot_colors=_SLOTS,
            build=_build_list_three, backgrounds=_ALL_BG,
        ),
        TemplateMeta(
            id="quote-poster", kind="social", specs=("square", "xhs-3x4"),
            params_schema=_QUOTE_SCHEMA, slot_colors=_SLOTS,
            build=_build_quote_poster, backgrounds=("solid", "linear", "radial", "pattern"),
        ),
        TemplateMeta(
            id="chapter-divider", kind="article",
            specs=("article-long", "square"),
            params_schema=_CHAPTER_SCHEMA, slot_colors=_SLOTS,
            build=_build_chapter_divider,
            backgrounds=("solid", "linear", "radial", "pattern"),
        ),
    )
}


__all__ = ["FONT_STEPS", "HOOK_STEPS", "REGISTRY", "TemplateMeta", "resolve", "validate_params"]
