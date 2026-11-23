"""assist 域动作：AI 辅助预览三动作（/api/action/* 3 条）。

R-2 行数门拆分（行为零变更）：自 handlers/platform.py 按域迁出——
cover（P3 封面工坊，契约 §3.2-1）/ refine（M4 F57 加工 M0：预览零落盘/
采纳落 derived）/ tags_suggest（Phase D F77 标签建议预览）。三者同型：
同步瞬时、AI 只起草、人工采纳才落库（预览即焚不建 job）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
    _resolve_file,
)


def _action_cover(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/cover：生成封面 PNG（契约 §3.2-1；cover_service 壳层接线）。

    - 契约锁定键=confirm+article；预定义的 preset/size/font/align 四键按
      契约「实施时以 cover_service 签名为准微调」落为服务实有参数
      route/theme/platform/write_back（生成语义零新逻辑，全部透传）；
    - 同步形态（契约两形态二选一）：封面生成本地 PIL/卡片光栅非长动作，
      不进 job（jobs KINDS 白名单零改动）；
    - write_back 缺省沿用服务层 True（mp 封面写回 frontmatter cover + 版本
      池，与 CLI 同口径）；生成失败（未知平台/zhihu 无规格/源图缺失等
      ValueError）→ 200 {ok:false} 可读错误。
    """
    from ...core.visual.cover import ROUTES as _COVER_ROUTES
    from ...services import cover_service

    article_rel = _require_str(data, "path", alias="article")
    article_path = _resolve_file(ctx.root, article_rel)
    kwargs: dict[str, Any] = {}
    route = data.get("route")
    if route is not None:
        if not isinstance(route, str) or route not in _COVER_ROUTES:
            raise _HttpError(400, f"route 必须是 {'/'.join(_COVER_ROUTES)} 之一")
        kwargs["route"] = route
    theme = data.get("theme")
    if theme is not None:
        if not isinstance(theme, str) or not theme.strip():
            raise _HttpError(400, "theme 必须是非空字符串")
        kwargs["theme"] = theme.strip()
    platform = data.get("platform")
    if platform is not None:
        choices = cover_service.platform_choices()
        if not isinstance(platform, str) or platform not in choices:
            raise _HttpError(400, f"platform 必须是 {'/'.join(choices)} 之一")
        kwargs["platform"] = platform
    image = data.get("image")
    if image is not None:
        if not isinstance(image, str) or not image.strip():
            raise _HttpError(400, "image 必须是非空字符串（root 内相对路径）")
        kwargs["image"] = str(_resolve_file(ctx.root, image.strip()))
    write_back = data.get("write_back")
    if write_back is not None:
        if not isinstance(write_back, bool):
            raise _HttpError(400, "write_back 必须是布尔值")
        kwargs["write_back"] = write_back
    try:
        out = cover_service.generate_cover(article_path, **kwargs)
    except Exception as exc:  # 未知平台/zhihu 无规格/源图缺失等 → 可读错误
        return _error_payload(exc, ctx.root)
    return {"ok": True, "cover": _rel(ctx.root, out)}


def _action_refine(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/refine {action, id, …}：F57 加工动作 M0（confirm 全局闸门）。

    两形态（AI 只起草，人工采纳才落库——本期飞轮红线）：
      - {action:"preview", id}：chat(task="refine") 同步起草 → 预览文本
        返回，**零落盘**（不建 job——job result 持久化即预览留痕，违反
        「拒绝即消失不留痕」；ThreadingHTTPServer 同步调用不阻塞）。
        RefineError → 400 可读；AIError → {ok:false} 可读（AI 未配置/
        调用失败，同 annotate_rewrite 降级面）。
      - {action:"adopt", id, text}：人工「存为素材」→ derived 素材落库
        （provenance.chain+license 继承，见 refine_service.adopt）。
      - {action:"regenerate", id, feedback?}（AI-3 批 2026-11-21）：AI 重
        新起草一版并直接采纳——旧版不动、新版追加进同一 REFINE-ITER 版本
        链（refine_service.regenerate=preview(feedback)+adopt 两行复用）；
        feedback 可选字符串拼进 prompt 尾部。同步形态同 preview（零 job）。
    """
    from ...infra.ai import AIError
    from ...services import refine_service

    mid = _require_str(data, "id")
    action = data.get("action")
    if action == "preview":
        try:
            return {"ok": True, **refine_service.preview(mid)}
        except refine_service.RefineError as err:
            raise _HttpError(400, str(err)) from None
        except AIError as err:
            return _error_payload(err, ctx.root)
    if action == "adopt":
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _HttpError(400, "缺少必填字段 text（预览文本原样回传）")
        # N-4：AI 自评置信度回传（preview 的 confidence 原样带回）；省略=None。
        # 形状门在 HTTP 面（非 0-1 数值 → 400），业务校验在服务层。
        confidence = data.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not (0.0 <= float(confidence) <= 1.0)
        ):
            raise _HttpError(400, f"confidence 必须是 0-1 数值或省略: {confidence!r}")
        try:
            return refine_service.adopt(
                mid, text, confidence=float(confidence) if confidence is not None else None,
            )
        except refine_service.RefineError as err:
            raise _HttpError(400, str(err)) from None
    if action == "regenerate":
        feedback = data.get("feedback")
        if feedback is not None and not isinstance(feedback, str):
            raise _HttpError(400, f"feedback 须为字符串（自然语言改进要求，可省略）: {feedback!r}")
        try:
            return refine_service.regenerate(mid, feedback=(feedback or "").strip() or None)
        except refine_service.RefineError as err:
            raise _HttpError(400, str(err)) from None
        except AIError as err:
            return _error_payload(err, ctx.root)
    raise _HttpError(400, "action 必须是 preview/adopt/regenerate 之一")


def _action_tags_suggest(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/tags_suggest {id}：F77 标签建议预览（同步，不建 job）。

    预览即焚判例同 refine preview：job result 持久化=预览留痕，违反
    「拒绝即消失不留痕」——故同步返回不建 job；采纳复用既有
    /api/materials/triage（A 档 F63 tags 键全量替换语义，前端/CLI 算并集
    回传，不另设写端点）。数据校验 400：缺 id（confirm 由 server 全局
    闸门统一收口）。业务校验 200 {ok:false}：素材不存在/已删；无正文面
    （TagError=RefineError 族可读消息）；AI 未配置/AIError → 200
    {ok:false, error}（脱敏面，前端降级）。
    """
    from ...infra.ai import AIError
    from ...services import tag_service

    mid = _require_str(data, "id")
    try:
        return {"ok": True, **tag_service.preview(mid)}
    except tag_service.TagError as err:
        return _error_payload(err, ctx.root)
    except AIError as err:
        return _error_payload(err, ctx.root)


# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/action/cover": _action_cover,  # P3 封面工坊（契约 §3.2-1；cover_service 壳层接线）
    "/api/action/refine": _action_refine,  # M4：F57 加工 M0（预览零落盘/采纳落 derived）
    "/api/action/tags_suggest": _action_tags_suggest,  # Phase D F77：标签建议预览（同步无 job；采纳走 materials/triage）
}
