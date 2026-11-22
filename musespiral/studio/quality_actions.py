"""Studio 质量循环 + 标题 A/B 动作（P1 双件，契约 §3.26；2026-11-23）。

  - POST /api/action/quality_loop `{confirm:true, article, max_rounds?}`：
    check → AI 修复 → 再 check 多轮长动作 → **job（kind=`quality_loop`）**
    → `{ok:true, job_id}`。AI 多次调用是长动作进 job（annotate_rewrite
    判例）；修复稿写 `<stem>.quality_fix.md` 预览（服务层红线：原文零触碰）。
  - POST /api/action/title_ab `{confirm:true, article, mode?, title?}`：
    `mode=generate`（缺省）= AI/启发式三风格候选，**同步瞬时零落盘零 job**
    （assist.py 判例：AI 只起草、预览即焚——job result 持久化即预览留痕）；
    `mode=apply` = 三选一写回 frontmatter.title（版本池先行，title --apply
    同款）。**不接微信 A/B API**——本地预览三选一。

**400 vs 200{ok:false} 分界**（契约 §1.3）：形状/枚举/路径错误 400 提交期
拒；业务失败（AI 不可用/循环内异常）落 job.error（quality_loop）或
_error_payload（title_ab，200 ok:false 可读错误）。
"""
from __future__ import annotations

from typing import Any

from ..services import quality_loop, title_ab
from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
    _resolve_file,
)


def _action_quality_loop(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/quality_loop：质量循环 → job（kind=quality_loop）。"""
    from .actions import _submit

    article_rel = _require_str(data, "article")
    article_path = _resolve_file(ctx.root, article_rel)
    rounds = data.get("max_rounds", 3)
    # bool 是 int 子类（True==1）一并排除；范围 1-5 与前端下拉一致
    if not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 5:
        raise _HttpError(400, "max_rounds 必须是 1-5 的整数")

    def _body(_job_id: str) -> dict[str, Any]:
        out = quality_loop.loop(article_path, max_rounds=rounds, auto_fix=True)
        result: dict[str, Any] = {
            "rounds": out["rounds"],
            "fixes_applied": out["fixes_applied"],
            "final_result": [
                {"level": level, "message": message} for level, message in out["final_result"]
            ],
        }
        preview = quality_loop.fix_preview_path(article_path)
        if out["fixes_applied"] and preview.exists():
            result["output"] = _rel(ctx.root, preview)
        return result

    return _submit(ctx, "quality_loop", {"article": article_rel, "max_rounds": rounds}, _body)


def _action_title_ab(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/title_ab：三风格候选生成（同步）与三选一写回（同步）。"""
    article_rel = _require_str(data, "article")
    article_path = _resolve_file(ctx.root, article_rel)
    mode = data.get("mode", "generate")
    if mode not in ("generate", "apply"):
        raise _HttpError(400, "mode 必须是 generate|apply")
    if mode == "apply":
        picked = data.get("title")
        if not isinstance(picked, str) or not picked.strip():
            raise _HttpError(400, "mode=apply 时 title 必填（非空字符串）")
        try:
            applied = title_ab.apply_variant(article_path, picked)
        except (OSError, ValueError) as exc:
            return _error_payload(exc, ctx.root)
        return {"ok": True, "applied": applied}
    try:
        out = title_ab.generate_variants(article_path)
    except (OSError, ValueError) as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "source": out["source"], "variants": out["variants"]}


__all__ = ["_action_quality_loop", "_action_title_ab"]
