"""Studio /api/agent 族动作（Phase D F75.4 执行面 + F76.4 采纳面）。

三条端点（POST 二 + GET 一，契约 §3.6）：

  - POST /api/agent/run   {plan, material_ids, params?}：构造期校验 → job
    （kind=`agent_run`）→ `{ok:true, job_id, run_id}`。校验在提交时同步完成
    （**零 job 零 run 目录**，设计 §二-F76.1 步骤 2）。
  - POST /api/agent/adopt {run_id, target, title?}：采纳分发（material /
    compose）——run 的**唯一外部写面**；`run.status≠done`（含 budget_stopped）
    是业务拒绝（`200 {ok:false}`，不是 400）。
  - GET  /api/agent/runs（读面在 `agent_payload.py`；本文件不含 GET）。

**400 vs 200{ok:false} 的分界**（契约 §1.3 错误双层语义）：

  - 400 = 请求**形状**不对（缺 confirm/枚举外/类型错）——客户端改请求即可；
  - 200{ok:false} = 请求形状对但**业务前提不成立**（AI 未配置、run 不存在、
    run 未完成、会话已存在）——用户改状态（配置 AI / 等 run / 换 run）才行。

AI 未配置归 200{ok:false}（与 refine preview 同口径：`_error_payload` 脱敏面，
前端降级提示去配置），不归 400。
"""
from __future__ import annotations

from typing import Any

from ..infra.ai import AIError
from ..services import agent_service

from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _require_str,
)


def _runner():
    """借用 Studio 共享 runner（actions._runner：首取即孤儿恢复）。"""
    from .actions import _runner as studio_runner

    return studio_runner()


def _action_agent_run(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/agent/run：构造期校验 → 建 run → 提交 job（kind=agent_run）。

    校验失败路径全部**同步**：`AgentServiceError`（plan 名/素材/正文面/展平
    步数）与 `AIError`（未配置）都在 `submit_to_runner` 内、在 `runner.submit`
    之前抛出——因此 400/200{ok:false} 时**零 job 零 run 目录**。
    """
    plan = _require_str(data, "plan")
    material_ids = data.get("material_ids")
    if not isinstance(material_ids, list) or not all(
        isinstance(mid, str) and mid.strip() for mid in material_ids
    ):
        raise _HttpError(400, "material_ids 必须是非空字符串数组")
    params = data.get("params")
    if params is not None:
        if not isinstance(params, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in params.items()
        ):
            raise _HttpError(400, "params 必须是 {字符串: 字符串} 对象或 null")

    try:
        out = agent_service.submit_to_runner(
            _runner(), plan, [mid.strip() for mid in material_ids], params=params or {}
        )
    except agent_service.AgentServiceError as exc:
        raise _HttpError(400, str(exc)) from None
    except AIError as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "job_id": out["job_id"], "run_id": out["run_id"]}


def _action_agent_adopt(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/agent/adopt：采纳一次 done 的 synthesis run（唯一写面）。

    `target=compose` 落到 ctx.root（Studio 工作目录即文章库根，与
    `POST /api/compose/new` 同源）；`target=material` 落素材库（家目录，
    与 root 无关）。
    """
    run_id = _require_str(data, "run_id")
    target = _require_str(data, "target")
    if target not in ("material", "compose"):
        raise _HttpError(400, f"target 必须是 material/compose 之一，实际: {target!r}")
    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise _HttpError(400, "title 必须是字符串或 null")

    try:
        out = agent_service.adopt(
            run_id, target=target, title=title,
            root=ctx.root if target == "compose" else None,
        )
    except agent_service.AgentServiceError as exc:
        # 业务拒绝（run 不存在/未完成/无采纳面/会话已存在）→ 200 {ok:false}
        return _error_payload(exc, ctx.root)
    if target == "compose":
        # 契约 §3.6-3 响应形状恰好三键（blocks 计数供 CLI 用，不进 HTTP 面）
        return {"ok": True, "slug": out["slug"], "dir": out["dir"]}
    return out


__all__ = ["_action_agent_adopt", "_action_agent_run"]
