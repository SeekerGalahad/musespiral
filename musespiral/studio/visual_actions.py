"""Studio /api/visual 族动作（F70-F74；契约 §3.2 新增四条 POST）。

- POST /api/visual/render：同步瞬时纯计算（零落盘）→ {ok, svg, params?}
- POST /api/visual/save：设计稿落盘（版本池先行+原子写）→ {ok, dir, files, updated_at}
- POST /api/visual/export：进 job（kind=visual_export）→ {ok, job_id}；job.result=
  {png, size:[w,h], route}
- POST /api/visual/preset：预设保存（纯 JSON 落盘）→ {ok, id, path}

凡 POST 一律 confirm:true（全局闸门，契约 §1.2）。所有 handler 只做参数解析、
同步校验（400）+ 委托 visual_service（业务层）；响应脱敏走 _error_payload。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.visual.canvas.model import SPECS
from ..services import visual_service
from ..services.cover_service import platform_choices

from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
    _resolve_file,
)


def _int_or_none(data: dict[str, Any], key: str) -> int | None:
    v = data.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise _HttpError(400, f"{key} 必须是整数")
    return v


def _action_visual_render(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/visual/render {template, params?, spec, article?, theme?, source_ref?}。

    - article 给出时补齐封面/社媒缺省键（title/hook 自动取材）；
    - source_ref 给出（F73）时按 source_ref.kind 兜底抽取并覆写 params.points；
    - 纯计算 POST（零落盘，refine preview 同语义）；错误 → 400 可读。
    """
    template = _require_str(data, "template")
    spec = _require_str(data, "spec")
    if spec not in SPECS:
        raise _HttpError(400, f"spec 非法: {spec!r}（可选: {'/'.join(SPECS)}）")
    params = data.get("params")
    if params is not None and not isinstance(params, dict):
        raise _HttpError(400, "params 必须是对象")
    article = data.get("article")
    article_path: Path | None = None
    if article is not None:
        article_path = _resolve_file(ctx.root, str(article))
    theme = data.get("theme") or "lapis"
    source_ref = data.get("source_ref")
    merged_params = dict(params or {})
    try:
        if source_ref is not None:
            if not isinstance(source_ref, dict):
                raise _HttpError(400, "source_ref 必须是对象")
            info = visual_service.build_infographic(
                article_path or _require_article(ctx, data), source_ref=source_ref, theme=theme
            )
            merged_params.setdefault("title", info["params"]["title"])
            merged_params["points"] = info["params"]["points"]
        svg, merged = visual_service.render(
            template=template, params=merged_params, spec=spec, theme=theme,
            article_path=article_path,
        )
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    except FileNotFoundError as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "svg": svg, "params": merged}


def _require_article(ctx: _StudioContext, data: dict[str, Any]) -> Path:
    """source_ref 抽数必须给 article（信息图数据源=文章旁数据面）。"""
    rel = data.get("article")
    if not isinstance(rel, str) or not rel.strip():
        raise _HttpError(400, "source_ref 需同时给 article")
    return _resolve_file(ctx.root, rel.strip())


def _action_visual_save(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/visual/save：设计稿落盘（版本池先行 + 原子写）。"""
    article_rel = _require_str(data, "article")
    article = _resolve_file(ctx.root, article_rel)
    kind = _require_str(data, "kind")
    template = _require_str(data, "template")
    params = data.get("params")
    overrides = data.get("params_overrides")
    if params is not None and not isinstance(params, dict):
        raise _HttpError(400, "params 必须是对象")
    if overrides is not None and not isinstance(overrides, dict):
        raise _HttpError(400, "params_overrides 必须是对象")
    try:
        result = visual_service.save_design(
            article, kind=kind, template=template, spec=data.get("spec"),
            params=params, params_overrides=overrides,
            preset_id=data.get("preset_id"), svg=data.get("svg"),
            theme=data.get("theme") or "lapis",
        )
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    except FileNotFoundError as exc:
        return _error_payload(exc, ctx.root)
    rel_dir = _rel(ctx.root, Path(result["dir"]))
    return {"ok": True, "dir": rel_dir, "files": result["files"], "updated_at": result["updated_at"]}


def _action_visual_export(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/visual/export：kind=visual_export job（B 主 C 兜底）。

    设计稿缺席**同步返回 200+{ok:false}**「无设计稿，先存稿」（F72 错误补充：
    不必进 job 再做无意义轮询）；参数违例 400。
    """
    from .actions import _runner

    article = _resolve_file(ctx.root, _require_str(data, "article"))
    kind = _require_str(data, "kind")
    if kind not in visual_service.KINDS:
        raise _HttpError(400, f"kind 非法: {kind!r}（可选: {'/'.join(visual_service.KINDS)}）")
    route = data.get("route")
    if route is not None and route not in ("browser", "pillow"):
        raise _HttpError(400, "route 必须是 browser/pillow 之一")
    platform = data.get("platform")
    if platform is not None:
        choices = platform_choices()
        if platform not in choices:
            raise _HttpError(400, f"platform 必须是 {'/'.join(choices)} 之一")
    theme = data.get("theme") or "lapis"
    # 提交期预检（设计稿缺席 / 越界 → 200+{ok:false} 或 400，不进 job）
    try:
        design = visual_service.load_design(article)
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    if design is None:
        return {"ok": False, "error": "无设计稿，先存稿（缺少 design.json）"}

    def _run(_job_id: str) -> dict[str, Any]:
        try:
            out = visual_service.export_design(
                article, kind=kind, route=route, platform=platform, theme=theme
            )
        except (ValueError, FileNotFoundError) as exc:
            return {"ok": False, "error": _sanitize_msg(exc, ctx.root)}
        return {"png": _rel_safe(ctx.root, out["png"]), "size": out["size"], "route": out["route"]}

    job_id = _runner().submit(
        "visual_export", {"article": _rel(ctx.root, article), "kind": kind}, _run
    )
    return {"ok": True, "job_id": job_id}


def _action_visual_preset(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/visual/preset：预设保存（纯 JSON 落盘，同 id 幂等覆盖）。"""
    preset_id = _require_str(data, "id")
    template = _require_str(data, "template")
    params = data.get("params")
    if params is not None and not isinstance(params, dict):
        raise _HttpError(400, "params 必须是对象")
    try:
        result = visual_service.save_preset(
            preset_id=preset_id, template=template, params=params,
            label=data.get("label"), kind=data.get("kind"),
        )
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    # path 指 ~/.musespiral/...（root 外）——脱敏为 ~ 形态（不泄露绝对家目录）
    masked = _mask_home(result["path"])
    return {"ok": True, "id": result["id"], "path": masked}


def _mask_home(path: str) -> str:
    """家目录 → ~ 形态（预设路径在 root 外，错误/响应里统一脱敏）。"""
    from .. import config

    home = str(config.home_dir())
    if path.startswith(home):
        return "~/.musespiral" + path[len(home):]
    return path


def _rel_safe(root: Path, png: str) -> str:
    """root 内相对路径；越界（不应发生）按原样返回（job.result 侧兜底）。"""
    try:
        return _rel(root, Path(png))
    except _HttpError:
        return "<path>"


def _sanitize_msg(exc: BaseException, root: Path) -> str:
    from .security import _sanitize_error

    return _sanitize_error(exc, root)


__all__ = [
    "_action_visual_export",
    "_action_visual_preset",
    "_action_visual_render",
    "_action_visual_save",
]
