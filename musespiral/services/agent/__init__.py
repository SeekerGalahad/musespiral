"""多步编排服务（Phase D F75 执行面 + F76 双采纳目标，L3）——``agent/`` 包门面。

原 ``services/agent_service.py``（1046 行单文件，全库最大）按 W2-B4（正典=
《musespiral-运维发布改良设计-2026-11-23》§三，沿 ARCH-2 materials/pipeline
拆分模式）按四簇真实内聚边界拆分：

  store   —— run 目录的路径穿越门、原子读写、run.json 骨架、LRU prune
  build   —— 构造期纯校验与 ctx 组装、F47 覆盖级联、license 快照
  execute —— 跑计划、jobs 闭包、外部宿主挂接、读面（清单/详情）
  adopt   —— 双采纳目标（唯一外部写面，架构门 D5 的扫描对象）

本 ``__init__`` 是包门面：re-export 拆分前 agent_service 全量符号（含模块级
import 名与下划线私有名——测试与 book_fetch/tag_service 的调用面零改动，
``__all__`` 锁定 96 名与拆分前 ``dir()`` 等价）；旧路径
``services/agent_service.py`` 保留为兼容层再转发本包。

**单一持有者**：`~/.musespiral/agent_runs/<run_id>/`（设计 §1.1）+ run 的
jobs 提交（kind=`agent_run`）。引擎在 `core/agent`（L2 纯函数），本包只负责
**组装**（素材正文 → ctx）与**落盘/采纳**（唯一 I/O 与唯一外部写面）。
语义逐字未动，本包只改落点。

三条纪律（设计 §四 Q1-Q4 的服务层落点）：
1. **jobs result 只存计数无正文**（§1.1）——正文在 run 目录的 `steps/<n>.json`。
2. **run 终态 → job 终态映射**（§四 Q2）——`done`/`budget_stopped` → job
   done；`failed` → job failed（error=run 可读错误）。
3. **采纳是唯一外部写面**（§四 Q3）——run 永不直写 materials/正稿。

**构造函数 vs 提交函数**（两条路径的校验分工）：``build_run`` 是纯构造期
（校验失败零 job 零 run 目录）；``create_run`` 构造 → 建 run 目录（status=
running），**不执行**；``submit_to_runner`` 再提交 job。

**run_id 形态**：``<plan>-<YYYYMMDD-HHMMSS>-<rand4>``（设计 §1.1「会话级唯一
即可，无跨机诉求」）。rand4 取 ``uuid4().hex[:4]``——同秒并发两 run 不撞。
"""
from __future__ import annotations

# ---- 与拆分前 agent_service 顶层 import 面一致（门面契约：dir() 等价）----
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from ... import config
from ...core.agent import budgets as agent_budgets
from ...core.agent.interp import STATUS_BUDGET_STOPPED, STATUS_DONE, run_plan  # noqa: F401 - STATUS_* 经 __all__ 再导出
from ...core.agent.plan import validate_plan
from ...core.agent.plans_builtin import (
    BUILTIN_PLANS,
    PLAN_SYNTHESIS,
    PLAN_TAG_BATCH,
    SYNTHESIS_MAX_MATERIALS,  # noqa: F401 - 经 __all__ 再导出（CLI/测试引用同一常量）
    SYNTHESIS_MIN_MATERIALS,  # noqa: F401
    build_synthesis,
    build_tag_batch,
)
from ...core.composition import blocks_from_markdown
from ...core.materials import model
from ...core.materials.confidence import parse_ai_confidence  # N-4 综合稿末行自评
from ...infra.ai import TASKS, AIError, ai_configured
from ...infra.ai.gateway import chat_policed
from ...infra.ai.prompts import load_override
from ...infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ...jobs.store import JobStore, now_iso as _now_iso
from .. import material_body, materials_service, sources_service

# ---- 四簇（W2-B4 职责边界）----
from .store import (  # noqa: F401
    PRUNE_KEEP,
    RUNS_DIR,
    RUN_FILE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    STEPS_DIR,
    STEP_RUNNING,
    AgentServiceError,
    _RUN_ID_RE,
    _iso_from_mtime,
    _make_run_id,
    _now,
    _read_json,
    _run_meta,
    _valid_run_id,
    _write_json,
    prune,
    run_dir,
    runs_root,
)
from .build import (  # noqa: F401
    MATERIAL_EXCERPT_MAX,
    _LICENSE_SEVERITY,
    _apply_source_template,
    _excerpts,
    _license_snapshot,
    _load_materials,
    _resolve_plan,
    apply_prompt_overrides,
    build_run,
    step_templates,
    strictest_license,
)
from .execute import (  # noqa: F401
    STATUS_DONE,  # noqa: F401 - 再导出（拆分前模块级 import 面）
    _ensure_ai_ready,
    _execute,
    _finish_and_persist,
    _gateway_chat_fn,
    _interrupted_run_ids,
    _job_result,
    _summary,
    ChatFn,
    create_run,
    finish_attached_run,
    job_fn,
    list_runs,
    open_attached_run,
    read_run,
    submit_to_runner,
)
from .adopt import (  # noqa: F401
    SYNTHESIS_OP,
    SYNTHESIS_SOURCE_ID,
    SYNTHESIS_URI_SCHEME,
    _adopt_licenses,
    _compose_slug,
    _license_note_of,
    _require_done_synthesis,
    _run_materials,
    _snapshot_notes,
    _synthesis_text,
    adopt,
    adopt_compose,
    adopt_material,
)

__all__ = [
    "AIError",
    "AgentServiceError",
    "Any",
    "BUILTIN_PLANS",
    "Callable",
    "ChatFn",
    "JobStore",
    "MATERIAL_EXCERPT_MAX",
    "PLAN_SYNTHESIS",
    "PLAN_TAG_BATCH",
    "PRUNE_KEEP",
    "Path",
    "RUNS_DIR",
    "RUN_FILE",
    "STATUS_BUDGET_STOPPED",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_RUNNING",
    "STEPS_DIR",
    "STEP_RUNNING",
    "SYNTHESIS_MAX_MATERIALS",
    "SYNTHESIS_MIN_MATERIALS",
    "SYNTHESIS_OP",
    "SYNTHESIS_SOURCE_ID",
    "SYNTHESIS_URI_SCHEME",
    "Sequence",
    "TASKS",
    "_LICENSE_SEVERITY",
    "_RUN_ID_RE",
    "_adopt_licenses",
    "_apply_source_template",
    "_atomic_write",
    "_compose_slug",
    "_ensure_ai_ready",
    "_excerpts",
    "_execute",
    "_finish_and_persist",
    "_gateway_chat_fn",
    "_interrupted_run_ids",
    "_iso_from_mtime",
    "_job_result",
    "_license_note_of",
    "_license_snapshot",
    "_load_materials",
    "_make_run_id",
    "_now",
    "_now_iso",
    "_read_json",
    "_require_done_synthesis",
    "_resolve_plan",
    "_run_materials",
    "_run_meta",
    "_snapshot_notes",
    "_summary",
    "_synthesis_text",
    "_valid_run_id",
    "_write_json",
    "adopt",
    "adopt_compose",
    "adopt_material",
    "agent_budgets",
    "ai_configured",
    "annotations",
    "apply_prompt_overrides",
    "blocks_from_markdown",
    "build_run",
    "build_synthesis",
    "build_tag_batch",
    "chat_policed",
    "config",
    "create_run",
    "datetime",
    "finish_attached_run",
    "hashlib",
    "job_fn",
    "json",
    "list_runs",
    "load_override",
    "material_body",
    "materials_service",
    "model",
    "open_attached_run",
    "parse_ai_confidence",
    "prune",
    "re",
    "read_run",
    "run_dir",
    "run_plan",
    "runs_root",
    "sources_service",
    "step_templates",
    "strictest_license",
    "submit_to_runner",
    "uuid",
    "validate_plan",
]
