"""F74 视觉模板系统 — 注册表+四内置+params schema 校验（L2 纯层）。

- ``TemplateMeta.build(params, colors, spec) -> CanvasDoc``：colors=服务层经
  themes 解析后的语义色槽 hex（themes 是全 visual 域唯一色板事实源——本层
  只收色值不读主题文件，纯度纪律）；
- 四级合并 ``resolve``：default < preset.params < params < params_overrides
  （F74-T2；overrides 胜出——§1.1 读序）；
- schema 子集：str/enum/int/color_slot/list（list=F73 数组槽，数据行
  {label, value}）；违例抛可读 ValueError 含槽名与合法枚举清单（F74-T3）；
- 内置四模板：vermilion-slab/indigo-slab（封面×2，F71）、xhs-card（社媒，
  F72 默认稿）、points-three（三要点卡信息图，F73）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import shapes
from .model import SEMANTIC_COLOR_SLOTS, SPECS, CanvasDoc, Element, TextBlock

_ALIGN = ("left", "center", "right")
_FONT_ENUM = ("S", "M", "L")
# 字号基准（1242 宽的像素基准，其余 spec 按宽等比——cover.py 56/40 比例族同源）
FONT_STEPS = {"S": 40, "M": 56, "L": 72}
HOOK_STEPS = {"S": 28, "M": 40, "L": 52}


@dataclass(frozen=True)
class TemplateMeta:
    """模板注册元数据：build(params, colors, spec) 产白名单 CanvasDoc。"""

    id: str
    kind: str
    specs: tuple[str, ...]
    params_schema: dict[str, dict[str, Any]]
    slot_colors: dict[str, str]
    build: Callable[[dict[str, Any], dict[str, str], str], CanvasDoc]


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


def validate_params(schema: dict[str, dict[str, Any]], params: dict[str, Any],
                    *, where: str = "params") -> dict[str, Any]:
    """schema 校验：类型/枚举/色槽/数据行违例 → 可读 ValueError（含槽名+合法值）。"""
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
_ALL_SPECS = tuple(SPECS)

REGISTRY: dict[str, TemplateMeta] = {
    m.id: m
    for m in (
        TemplateMeta(
            id="vermilion-slab", kind="cover", specs=_ALL_SPECS, params_schema=_COVER_SCHEMA,
            slot_colors=_SLOTS,
            build=lambda p, c, s: _build_slab(p, c, s, bar_color_key="accent", bar_bottom=False),
        ),
        TemplateMeta(
            id="indigo-slab", kind="cover", specs=_ALL_SPECS, params_schema=_COVER_SCHEMA,
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
    )
}


__all__ = ["FONT_STEPS", "HOOK_STEPS", "REGISTRY", "TemplateMeta", "resolve", "validate_params"]
