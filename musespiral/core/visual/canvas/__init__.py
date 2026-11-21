"""F70-F74 画布纯层 — 文档模型/测量/序列化/兜底重绘/基元/模板。

L2 纪律（设计 §三）：零 I/O 零网络（Pillow 测量/重绘白名单例外）；
一切 doc→SVG 产出必经 svg.render_svg 白名单序列化（防注入收口唯一出口）。
"""
from __future__ import annotations

from .layout import layout_document, load_font, relayout_vertical, wrap_text
from .model import (
    ELEMENT_WHITELIST,
    SEMANTIC_COLOR_SLOTS,
    SPECS,
    SQUARE,
    WIDE16X9,
    CanvasDoc,
    Element,
    TextBlock,
    spec_size,
    validate_doc,
)
from .pillow_render import render as render_pillow
from .shapes import MIN_BAR_WIDTH, bar, empty_state, ring, stat, timeline
from .svg import render_svg
from .templates import FONT_STEPS, HOOK_STEPS, REGISTRY, TemplateMeta, resolve, validate_params

__all__ = [
    "ELEMENT_WHITELIST", "SEMANTIC_COLOR_SLOTS", "SPECS", "SQUARE", "WIDE16X9",
    "CanvasDoc", "Element", "TextBlock", "spec_size", "validate_doc",
    "layout_document", "load_font", "relayout_vertical", "wrap_text",
    "render_svg", "render_pillow",
    "MIN_BAR_WIDTH", "bar", "empty_state", "ring", "stat", "timeline",
    "FONT_STEPS", "HOOK_STEPS", "REGISTRY", "TemplateMeta", "resolve", "validate_params",
]
