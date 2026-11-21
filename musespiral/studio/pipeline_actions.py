"""Studio E1 金路径触发动作（组合批 2026-11-17，契约 §3.7）。

  - POST /api/action/pipeline `{confirm:true, article, skip?}`：一键金路径
    （check→title→cover→adapt→pack 混合管线）→ job（kind=`pipeline`）→
    `{ok:true, job_id}`。对齐 agent/topic_radar 先例：构造期校验在
    `pipeline_service.submit_to_runner` 内、在 `runner.submit` 之前完成
    （校验不通过 → 零 job）。

**接缝说明**：pipeline_service（金路径工程师并行产出——steps 执行体与
`musespiral pipeline run` CLI）落地前，本端点以 200 `{ok:false}` 可读降级
（服务缺席不是客户端错误，不 400）；落地后经延迟 import 直连，本文件
零改动。调用面锚定：`pipeline_service.submit_to_runner(runner, article,
root=ctx.root, skip=skip) → job_id`（topic_radar 先例同形）。

**400 vs 200{ok:false} 的分界**（契约 §1.3）：
  - 400 = 请求形状不对（缺 confirm——全局闸门 / article 缺失越界 /
    skip 非字符串数组）；
  - 200{ok:false} = 业务前提不成立（管线服务未集成 / 服务层校验拒绝
    如步骤名枚举外——PipelineServiceError 可读文案）。
"""
from __future__ import annotations

from typing import Any

from .security import _HttpError, _StudioContext, _error_payload, _resolve_file


def _runner():
    """借用 Studio 共享 runner（actions._runner：首取即孤儿恢复）。"""
    from .actions import _runner as studio_runner

    return studio_runner()


def _action_pipeline(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/pipeline：一键金路径 → job（kind=pipeline）。"""
    article = data.get("article")
    if not isinstance(article, str) or not article.strip():
        raise _HttpError(400, "缺少必填字段 article（root 内相对路径）")
    _resolve_file(ctx.root, article.strip())  # 缺文件 404 / 越界 400（形状面）
    skip = data.get("skip")
    if skip is not None:
        if not isinstance(skip, list) or not all(
            isinstance(s, str) and s.strip() for s in skip
        ):
            raise _HttpError(400, "skip 必须是非空字符串数组（要跳过的步骤名）或省略")
        skip = [s.strip() for s in skip]

    try:
        from ..services import pipeline_service  # 延迟 import：缺席降级不炸路由
    except ImportError:
        return {
            "ok": False,
            "error": "金路径管线尚未集成（pipeline_service 待落地）——集成轮接驳",
        }
    try:
        job_id = pipeline_service.submit_to_runner(
            _runner(), article.strip(), root=ctx.root, skip=skip
        )
    except Exception as exc:  # noqa: BLE001 - PipelineServiceError 未定形，统一降级面
        return _error_payload(exc, ctx.root)
    return {"ok": True, "job_id": job_id}


__all__ = ["_action_pipeline"]
