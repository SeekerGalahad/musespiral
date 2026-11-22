"""插件流水线执行引擎——兼容门面（ARCH-2 拆分后薄转发层）。

实现已于 2026-11-22（ARCH-2，正典=《musespiral-极限架构与功能扩展总规划
-2026-11-21》§ARCH-2）按七簇真实职责边界拆入 ``services/pipeline/`` 包：
workspace（产物暂存区+引用/值分流）/ plan（阶段一解析校验）/ engine
（阶段二~四拓扑执行+失败语义）/ adjudicate（裁决态原语——adopt/discard
端点在 studio/plugin_actions/ run_adjudicate 域）/ regenerate（节点级重生成）/ prune
（LRU 回收）/ jobs（runner 接驳）。全量符号面见
``musespiral.services.pipeline`` 的 ``__init__``。

本模块保留原 import 路径与**拆分前完全一致的符号集**
（``from .pipeline import *`` + ``__all__`` 全集 63 名，含下划线私有名）
——Studio（plugin_actions）/ CLI（plrun_cmds）/ 全部测试的调用面零改动。

拆分前模块无运行期注入缝（调用面只读消费符号，无 setattr 替换用法），
故无需 materials_service 式的 ``__setattr__`` 转发机制。
"""
from __future__ import annotations

from .pipeline import *  # noqa: F401,F403 —— 门面契约：符号集=拆分前全集（__all__ 锁定）

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
