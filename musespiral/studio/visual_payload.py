"""Studio /api/visual 族 GET 载荷（F70-F74；契约 §3.1 新增三条）。

- GET /api/visual/design?article=&kind=：读 design.json + SVG（SVG 载入重过
  白名单——F70 API 纪律）；缺失 → 404；恶意 SVG → 200+{ok:false}（F72-T5）。
- GET /api/visual/templates：注册表元数据（id/kind/specs/params_schema/
  slot_colors/color_slots + 顶层 slot_hexes（F-B 2026-11-18）；零 secret 字段）。
- GET /api/visual/presets：预设清单（id/label/template/kind）。
"""
from __future__ import annotations

from typing import Any

from ..core.visual.canvas.model import SEMANTIC_COLOR_SLOTS
from ..core.visual.canvas.templates import REGISTRY
from ..services import visual_service

from .security import _HttpError, _StudioContext, _error_payload, _query_str, _resolve_file


def _visual_design_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/visual/design：设计稿全文（design.json + 白名单复验后的 SVG）。"""
    rel = _query_str(query, "article")
    if not rel:
        raise _HttpError(400, "缺少必填参数 article（root 内相对路径）")
    article = _resolve_file(ctx.root, rel)
    try:
        design = visual_service.load_design(article)
    except ValueError as exc:  # 含恶意 SVG 复验拒绝（F72-T5）
        return _error_payload(exc, ctx.root)
    if design is None:
        raise _HttpError(404, "无设计稿（缺少 design.json），先存稿")
    svg_path = visual_service.design_dir(article) / str(
        design.get("svg") or visual_service._SVG_NAME.get(design["kind"], "cover.svg")  # noqa: SLF001
    )
    svg = svg_path.read_text(encoding="utf-8") if svg_path.exists() else ""
    return {
        "article": rel,
        "kind": design["kind"],
        "template": design["template"],
        "params": design["params"],
        "params_overrides": design.get("params_overrides") or {},
        "preset_id": design.get("preset_id"),
        "spec": design["spec"],
        "updated_at": design.get("updated_at"),
        "svg": svg,
    }


def _visual_templates_payload() -> dict[str, Any]:
    """GET /api/visual/templates：内置注册表元数据（零 secret）。

    F-B 色板升级（2026-11-18）：顶层 slot_hexes={槽→hex}——theme_colors()
    对默认主题一次映射（色块化 chip 的 style background 数据面）。"""
    items: list[dict[str, Any]] = []
    for tid, meta in sorted(REGISTRY.items()):
        items.append({
            "id": tid,
            "kind": meta.kind,
            "specs": list(meta.specs),
            "params_schema": meta.params_schema,
            "slot_colors": dict(meta.slot_colors),
            "color_slots": list(SEMANTIC_COLOR_SLOTS),
        })
    return {"items": items, "slot_hexes": dict(visual_service.theme_colors())}


def _visual_presets_payload() -> dict[str, Any]:
    """GET /api/visual/presets：预设清单（README 级元数据，无 params 回显面）。"""
    return {"items": visual_service.list_presets()}


__all__ = [
    "_visual_design_payload",
    "_visual_presets_payload",
    "_visual_templates_payload",
]
