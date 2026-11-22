"""插件流水线执行引擎（插件系统设计 §五，PL-2）——L3 编排（ARCH-2 拆分后
的包门面）。

原 ``services/pipeline_service_plugins.py``（1291 行单文件）按 ARCH-2
（正典=《musespiral-极限架构与功能扩展总规划-2026-11-21》§ARCH-2，沿
2026-11-19 R-1 compose 拆分与 2026-11-22 materials 拆分同模式）按职责
边界拆为七簇：

- workspace——产物暂存区 + 引用/值分流（INLINE_MAX_BYTES/ArtifactRef/
  Workspace/_PipelineContext）；
- plan——阶段一解析校验（PipelinePlanError/PlannedNode/plan_pipeline，
  零 run 目录零 job）；
- engine——阶段二拓扑执行 + 阶段三收集 + 阶段四清理 + 失败语义
  （run_pipeline 与节点执行件、run 目录族）；
- adjudicate——裁决态原语（_reset_draft_status；adopt/discard 端点在
  studio/plugin_actions/（run_adjudicate 域），不在服务层）；
- regenerate——节点级重生成 + 级联重放（AI-3）；
- prune——run 目录 LRU 回收（PRUNE_KEEP/prune_runs）；
- jobs——runner 接驳（job_fn/submit_to_runner，kind=plugin_pipeline）。

本 ``__init__`` 是包门面：re-export 拆分前 pipeline_service_plugins 全量
符号（含下划线私有名——测试与 studio 调用面零改动，``__all__`` 锁定 68
名，ARCH-4 控制流批增 5 名：MAX_LOOP_ITERS/check_condition/evaluate_condition/
_condition_context/_node_spec）；旧路径
``services/pipeline_service_plugins.py`` 保留为兼容层再转发本包。

与 services/pipeline_service **并存不替代**（正典 §一：那是「发布前的
内置金路径」五步硬编码，本模块是「数据处理管道平台」用户可编排）；
两者共享的只有步级记账纪律 ``{step, status, detail, artifacts}`` 与
jobs 接驳形态（job_fn/submit_to_runner 先例）。

四阶段模型（正典 §5.1）：
  阶段一 解析校验（plan.plan_pipeline）——manifest 可读 + 相邻契约接
         得上（core.plugins.manifest.validate_pipeline_contract）+ 权限
         并集检查；**此阶段失败不产生 run 目录**（PipelinePlanError
         构造期抛，零 run 零 job——agent_service/pipeline_service 同款
         纪律）。
  阶段二 拓扑执行（engine.run_pipeline 线性逐节点）——gather inputs →
         check_input → execute → workspace.put。
  阶段三 产物收集——末节点（sink）产物收集到 ``<run_dir>/output/``。
  阶段四 清理（finally）——临时目录（ctx.tmpdir 受管区）收尾。**失败
         不清理 workspace**（用户要看现场）；成功态 workspace 也不删
         ——保留策略=LRU 最近 PRUNE_KEEP（默认 5）次，由
         ``musespiral plrun prune`` 接管，引擎只写 meta.json 终态。

失败语义（沿用 pipeline_service 纪律）：失败即停；已完成步与失败步
（含 reason）全部保留在 result.steps；节点返回值缺 manifest.outputs
声明的键 → OutputContractError 判失败（reason 可读），额外键忽略不报错。

序列化阈值（评审 B-5）：``INLINE_MAX_BYTES``（1 MiB）唯一权威——超过
或不可 JSON 序列化的产物落盘存引用，小于则内联（Workspace.put）。

job 集成（正典 §5.3 裁决）：整条流水线只占 1 个 kind ``plugin_pipeline``
（KINDS 18→19），内部节点不是独立 job——节点生命周期归流水线管理。
取消接 runner 协作式取消（R3.5 §2.2）：ctx.cancelled 读
``runner.is_cancelled(job_id)``；插件抛 PluginCancelled → run 级
status="cancelled"（非 failed；job 侧按 batch_adapt 部分成功先例以
done 收束、result 携带步级记录）。

诚实边界（v1，记入正典 §十三）：runtime.timeout_s 未强制（进程内
python 无法安全打断线程，需线程级看门狗）；config_schema 只填 default
不做深校验（JSON-Schema 子集评估留给后续期）；「不可 JSON 序列化」的
非 bytes/PathLike 值以进程内对象引用（mem ref）保真——跨进程续读
不支持（单次 run 生命周期内取数正确）。
"""
from __future__ import annotations

# ---- 与拆分前 pipeline_service_plugins 顶层 import 面一致（门面契约：dir() 等价）----
import importlib.util
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .. import plugins_service
from ... import config
from ...core.plugins.context import PluginCancelled, PluginContext, PluginError
from ...core.plugins.manifest import (
    PERMISSIONS,
    Plugin,
    Port,
    expected_dirname,
    parse_manifest,
    validate_pipeline_contract,
)
from ...core.plugins.types import types_compatible
from ...jobs.runner import JobFailure
from ...jobs.store import now_iso as _now_iso

# ---- 七簇（ARCH-2 职责边界）----
from .workspace import (  # noqa: F401
    INLINE_MAX_BYTES,
    ArtifactRef,
    Workspace,
    _PipelineContext,
    _UNSAFE_FILENAME_RE,
)
from .plan import (  # noqa: F401
    MAX_LOOP_ITERS,
    PipelinePlanError,
    PlannedNode,
    _merge_config,
    _normalize_nodes,
    check_condition,
    evaluate_condition,
    plan_pipeline,
)
from .engine import (  # noqa: F401
    RUNS_DIRNAME,
    OutputContractError,
    _RUN_ID_RE,
    _SLUG_RE,
    _NodeFailure,
    _condition_context,
    _gather_inputs,
    _load_python_entry,
    _new_run_id,
    _node_spec,
    _wire_input,
    pipeline_runs_root,
    run_pipeline,
)
from .adjudicate import _reset_draft_status  # noqa: F401
from .regenerate import (  # noqa: F401
    RegenerateError,
    _ARCHIVED_MD_RE,
    _ARCHIVED_OUTPUTS_RE,
    _RegenLog,
    _cascade_replay_downstream,
    _regenerate_node_inner,
    precheck_regenerate,
    regenerate_node,
)
from .prune import PRUNE_KEEP, prune_runs  # noqa: F401
from .jobs import job_fn, submit_to_runner  # noqa: F401

__all__ = [
    "Any",
    "ArtifactRef",
    "Callable",
    "INLINE_MAX_BYTES",
    "Iterable",
    "JobFailure",
    "MAX_LOOP_ITERS",
    "OutputContractError",
    "PERMISSIONS",
    "PRUNE_KEEP",
    "Path",
    "PipelinePlanError",
    "PlannedNode",
    "Plugin",
    "PluginCancelled",
    "PluginContext",
    "PluginError",
    "Port",
    "RUNS_DIRNAME",
    "RegenerateError",
    "Sequence",
    "Workspace",
    "_ARCHIVED_MD_RE",
    "_ARCHIVED_OUTPUTS_RE",
    "_NodeFailure",
    "_PipelineContext",
    "_RUN_ID_RE",
    "_RegenLog",
    "_SLUG_RE",
    "_UNSAFE_FILENAME_RE",
    "_cascade_replay_downstream",
    "_condition_context",
    "_gather_inputs",
    "_load_python_entry",
    "_merge_config",
    "_new_run_id",
    "_node_spec",
    "_normalize_nodes",
    "_now_iso",
    "_regenerate_node_inner",
    "_reset_draft_status",
    "_wire_input",
    "annotations",
    "check_condition",
    "config",
    "dataclass",
    "evaluate_condition",
    "expected_dirname",
    "field",
    "importlib",
    "job_fn",
    "json",
    "os",
    "parse_manifest",
    "pipeline_runs_root",
    "plan_pipeline",
    "plugins_service",
    "precheck_regenerate",
    "prune_runs",
    "re",
    "regenerate_node",
    "run_pipeline",
    "shutil",
    "submit_to_runner",
    "time",
    "types_compatible",
    "uuid",
    "validate_pipeline_contract",
]
