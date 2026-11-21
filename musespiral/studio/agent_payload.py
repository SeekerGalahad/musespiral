"""Studio /api/agent 族 GET 载荷（Phase D F75.4 读面；契约 §3.6-4）。

`/api/agent/runs` 双模式（与 `/api/compose/session?slug=` 的形状同直觉）：

  - 无 `?id=` → 清单：`{runs:[{id, plan, status, started, finished, steps_done,
    chars_out}…]}`（≤50 新在前；`status` 含读侧 `interrupted` 标记）。
  - `?id=<run_id>` → 详情：`{ok:true, run:{…run.json 全量, steps:[{id, task,
    status, out_text, error?}…]}}`。

**为什么详情不是裸 run 对象**（清单也没有 `ok` 键）：详情包在 `ok:true/run`
里是因为它的**失败是可预期的**（prune 后 404、id 空 400）——前端要能按
`ok` 分支；清单恒成功（空列表即空态），裸对象更省一层（沿用 `/api/jobs`
既有形状）。
"""
from __future__ import annotations

from typing import Any

from ..services import agent_service
from .security import _HttpError, _StudioContext


def _agent_runs_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/agent/runs（无 id 清单 / 有 id 详情）。

    `id` 形状门（契约 §3.6-4 / §五 400 表）：白名单外形态（含 `../` 穿越、
    绝对路径、URL 编码分隔符）→ **400** 而非 404——这是「请求形状不对」而非
    「资源不存在」；服务层 `read_run` 另有 None 兜底（双层门，防未来调用方
    漏过此层）。
    """
    values = query.get("id") or []
    if values:
        run_id = values[-1].strip()
        if not run_id:
            raise _HttpError(400, "id 不能为空串（省略 id 取清单）")
        if not agent_service._valid_run_id(run_id):
            raise _HttpError(
                400,
                f"非法 run_id: {run_id!r}（白名单 [A-Za-z0-9][A-Za-z0-9._-]；"
                f"路径穿越/分隔符形态一律拒）",
            )
        run = agent_service.read_run(run_id)
        if run is None:
            raise _HttpError(404, f"run 不存在（可能已被 prune 清理）: {run_id}")
        return {"ok": True, "run": run}
    return {"runs": agent_service.list_runs(limit=agent_service.PRUNE_KEEP)}


__all__ = ["_agent_runs_payload"]
