"""pipeline 包：jobs（runner 接驳——kind=plugin_pipeline）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的 jobs 接驳簇
（正典 §5.3 裁决）：整条流水线只占 1 个 kind ``plugin_pipeline``
（KINDS 18→19），内部节点不是独立 job——节点生命周期归流水线管理。

- ``job_fn``——把一条流水线包成 jobs 闭包（runner 调 ``fn(job_id)``）；
  失败 → JobFailure（result 携带步级记录）；取消 → 按 batch_adapt 部分
  成功先例以 done 收束。
- ``submit_to_runner``——阶段一校验在 ``runner.submit`` **之前**（构造期，
  零 job——pipeline_service.PipelineServiceError 先例同款纪律）。

包门面与全量符号面见 ``musespiral.services.pipeline`` 的 ``__init__``。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

from ...jobs.runner import JobFailure
from .engine import run_pipeline
from .plan import _normalize_nodes, plan_pipeline


def job_fn(
    nodes: Iterable[dict[str, Any] | str],
    *,
    plugins_root: Path | None = None,
    run_root: Path | None = None,
    ai_client: Any = None,
    runner: Any = None,
) -> Callable[[str], dict[str, Any]]:
    """把一条插件流水线包成 jobs 闭包（runner 调 ``fn(job_id)``）。

    失败 → JobFailure（消息「插件流水线在 <node> 节点失败：<原因>」，
    result 携带步级记录）；取消 → 按 batch_adapt 部分成功先例以 done
    收束（result.status="cancelled" 供 UI 区分，步级记录完整保留）。
    """
    node_list = _normalize_nodes(nodes)  # 生成器安全：闭包内只消费这一份

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(text: str) -> None:
            if runner is not None:
                runner.set_phase(job_id, text)

        result = run_pipeline(
            node_list,
            plugins_root=plugins_root,
            run_root=run_root,
            ai_client=ai_client,
            cancelled=(lambda: runner.is_cancelled(job_id)) if runner is not None else None,
            on_phase=_phase,
        )
        if result["status"] == "failed":
            failed = str(result.get("failed_at") or "")
            detail = next(
                (s["detail"] for s in result["steps"] if s["step"] == failed), ""
            )
            raise JobFailure(
                f"插件流水线在 {failed} 节点失败：{detail}", result=result
            )
        return result

    return _run


def submit_to_runner(
    runner: Any,
    nodes: Iterable[dict[str, Any] | str],
    *,
    plugins_root: Path | None = None,
    run_root: Path | None = None,
    ai_client: Any = None,
) -> str:
    """提交一条插件流水线 job（kind=plugin_pipeline），返回 job_id。

    阶段一校验在 ``runner.submit`` **之前**（构造期，零 job——
    pipeline_service.PipelineServiceError 先例同款纪律）。
    """
    node_list = _normalize_nodes(nodes)
    plan_pipeline(node_list, plugins_root=plugins_root)  # 校验先行；可抛 PipelinePlanError
    fn = job_fn(
        node_list,
        plugins_root=plugins_root,
        run_root=run_root,
        ai_client=ai_client,
        runner=runner,
    )
    return runner.submit("plugin_pipeline", {"nodes": node_list}, fn)
