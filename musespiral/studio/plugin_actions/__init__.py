"""插件系统 HTTP 接驳层（插件系统设计 §十二 as-built，PL-3 复核修复批
2026-11-20，API 契约 §3.19）——ARCH-2 ③ 拆分后的包门面（2026-11-22）。

原 ``studio/plugin_actions.py`` 单文件（950 行，12+ 端点 handler）按 ARCH-2
（正典=《musespiral-极限架构与功能扩展总规划-2026-11-21》§ARCH-2，沿批1
handlers/ 按域拆分与 ② services/pipeline/ 包门面同模式）拆为五域模块：

  list_detail.py     插件清单/详情/重扫/依赖检查 + 流水线定义读面与读取原语
  pipeline_crud.py   流水线定义写路径（保存 + id 白名单 _PIPE_ID_RE）
  run_read.py        运行读面 + run 产物定位原语 + AI 裁决态读面 + 版本链
  run_adjudicate.py  运行提交 / 三态裁决 adopt|discard / 节点重生成
  segments.py        E-4 转写编辑（段落/ranges 编辑版读写）+ 裁段音频

包内依赖单向无环：pipeline_crud → list_detail ← run_read ← {run_adjudicate,
segments}；``_submit``（studio.actions）保持函数体内惰性导入（环导规避，
拆分前同款）。本 ``__init__`` 是门面+兼容层：**与拆分前完全一致的符号集**
（``__all__`` 锁定 63 名，含下划线私有名与顶层 import 面）——routing/server/
actions/测试的 ``musespiral.studio.plugin_actions`` 调用面零改动；ROUTES
注册表 = 四域片段合并（键集与拆分前逐字相同）。

端点全集（拆分前模块文档原样承接）：

  GET  /api/plugins                    → plugins_service.list_plugins（重扫即事实）
  GET  /api/plugins/<id>/check         → plugins_service.check_plugin
  POST /api/plugins/refresh            → plugins_service.refresh（confirm 闸门）
  GET  /api/pipelines                  → 已存流水线清单（节点 manifest 联表再水化）
  GET  /api/pipelines/<id>             → 单流水线定义（同上再水化）
  POST /api/pipelines                  → 保存：UI 形状 {id, plugin: manifest} 归一为
                                        引擎形状 {id, plugin: <id>} 落盘；保存前
                                        validate_pipeline_contract（后端真拒非法
                                        顺序——复核 high-2 的适配与校验双层）
  POST /api/pipelines/run              → 运行：plan_pipeline 校验先行（零 job 纪律）
                                        → _submit(kind=plugin_pipeline) → {job_id, run_id}
  GET  /api/pipelines/runs/<run_id>    → meta.json 读面（终态含 steps/artifacts）+
                                        各插件输出端口类型表（前端 segments 门控用）
  GET  /api/pipelines/runs/<run_id>/segments → 转写段落+切片 ranges（编辑版
                                        优先——E-4，PL-6 批接驳）
  POST /api/pipelines/runs/segments    → 编辑版落盘 segments.edited.json /
                                        ranges.edited.json（E-4；PL-3 先例：
                                        动态段 POST 改精确路径 + body run_id）
  GET  /api/pipelines/runs/<run_id>/audio?start=&end= → 裁段音频 wav 字节流
                                        （E-4；从源媒体现场裁——音轨 tmpdir
                                        运行后已清理）
  POST /api/pipelines/runs/<run_id>/adopt|discard → 三态裁决（AI-1 批
                                        2026-11-21，契约 §3.19 修订 3；body
                                        {step:'ks'|'aw', indices?}——动态段
                                        POST 本批为 URL 形（run_id 在段内，
                                        server._handle 裁决分流先于 GET 面
                                        判定），把 run 的 draft 产物转
                                        adopted/discarded）
  POST /api/pipelines/runs/<run_id>/regenerate → 节点重生成（AI-3 批
                                        2026-11-21，正典=裁决机制设计 §2.3
                                        三件套·重生成；body {step:'ks'|'aw',
                                        feedback?}）——从该节点重跑（前序不
                                        重跑），旧产物归档 outputs.v{N}.json
                                        、新产物 status 重置 draft；长动作
                                        （ks/aw 是 AI 节点）走 job
                                        kind=plugin_pipeline，前端轮询 jobs 面
  GET  /api/pipelines/runs/<id>/versions?node=&a=&b= → run 域版本链（AI-3
                                        复核 fix-4：归档清单+两版 diff）

存储：~/.musespiral/pipelines/<id>.json（设计 §六）；运行真相=
~/.musespiral/pipeline_runs/<run_id>/meta.json（引擎终态写全——
steps/artifacts/failed_at）。
"""
from __future__ import annotations

# ---- 与拆分前 plugin_actions 顶层 import 面一致（门面契约：dir() 等价）----
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shutil  # noqa: F401
import subprocess  # noqa: F401
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Callable  # noqa: F401

from ... import config  # noqa: F401
from ...core.plugins.manifest import validate_pipeline_contract  # noqa: F401
from ...jobs.store import now_iso  # noqa: F401
from ...services import pipeline_service_plugins as psp  # noqa: F401
from ...services import plugins_service  # noqa: F401
from ..security import _HttpError, _StudioContext  # noqa: F401

# ---- 五域（ARCH-2 ③ 职责边界）----
from .lifecycle import ROUTES as _LIFECYCLE_ROUTES  # W14b: 插件生命周期四端点
from .list_detail import (  # noqa: F401
    ROUTES as _LIST_DETAIL_ROUTES,
    _action_plugins_refresh,
    _load_pipeline,
    _manifests_by_id,
    _pipeline_detail_payload,
    _pipelines_dir,
    _pipelines_payload,
    _plugin_check_payload,
    _plugins_payload,
    _rehydrate,
)
from .pipeline_crud import (  # noqa: F401
    ROUTES as _PIPELINE_CRUD_ROUTES,
    _PIPE_ID_RE,
    _action_pipeline_save,
)
from .run_read import (  # noqa: F401
    _AI_STATUS_VALUES,
    _ARCHIVED_OUTPUTS_RE,
    _REGEN_STEPS,
    _ai_verdicts,
    _effective_edited,
    _find_output_node,
    _group_ranges_status,
    _node_output_docs,
    _pipeline_run_payload,
    _pipeline_run_versions_payload,
    _run_dir_checked,
    _run_versions,
    _version_text,
)
from .run_adjudicate import (  # noqa: F401
    ROUTES as _RUN_ADJUDICATE_ROUTES,
    POST_DYNAMIC_SURFACE,
    _ADJUDICATE_PATH_RE,
    _ADJUDICATE_STATUSES,
    _action_pipeline_run,
    _adjudicate_article,
    _adjudicate_ranges,
    _append_run_log,
    _rewrite_inline_output,
    _validate_indices,
    resolve_adjudicate_post,
    run_adjudicate,
    run_regenerate,
)
from .segments import (  # noqa: F401
    ROUTES as _SEGMENTS_ROUTES,
    _AUDIO_MAX_CLIP_S,
    _AUDIO_TIMEOUT_S,
    _action_pipeline_run_segments_save,
    _find_media_path,
    _pipeline_run_audio_response,
    _pipeline_run_segments_payload,
    _validate_ranges_payload,
    _validate_segments_payload,
    _validate_time_pair,
)

# POST 动作注册表（actions._ACTION_ROUTES 合并面）= 四域片段合并
# （键集与拆分前单文件 ROUTES 逐字相同）
ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    **_LIST_DETAIL_ROUTES,  # /api/plugins/refresh（§十二 重扫）
      **_LIFECYCLE_ROUTES,  # W14b: 插件生命周期四端点
    **_PIPELINE_CRUD_ROUTES,  # /api/pipelines（§十二 保存，body id 形）
    **_RUN_ADJUDICATE_ROUTES,  # /api/pipelines/run（§十二 运行，body id 形）
    **_SEGMENTS_ROUTES,  # /api/pipelines/runs/segments（E-4 编辑版保存）
}

__all__ = [
    "POST_DYNAMIC_SURFACE",
    "ROUTES",
    "_ADJUDICATE_PATH_RE",
    "_ADJUDICATE_STATUSES",
    "_AI_STATUS_VALUES",
    "_ARCHIVED_OUTPUTS_RE",
    "_AUDIO_MAX_CLIP_S",
    "_AUDIO_TIMEOUT_S",
    "_HttpError",
    "_PIPE_ID_RE",
    "_REGEN_STEPS",
    "_StudioContext",
    "_action_pipeline_run",
    "_action_pipeline_run_segments_save",
    "_action_pipeline_save",
    "_action_plugins_refresh",
    "_adjudicate_article",
    "_adjudicate_ranges",
    "_ai_verdicts",
    "_append_run_log",
    "_effective_edited",
    "_find_media_path",
    "_find_output_node",
    "_group_ranges_status",
    "_load_pipeline",
    "_manifests_by_id",
    "_node_output_docs",
    "_pipeline_detail_payload",
    "_pipeline_run_audio_response",
    "_pipeline_run_payload",
    "_pipeline_run_segments_payload",
    "_pipeline_run_versions_payload",
    "_pipelines_dir",
    "_pipelines_payload",
    "_plugin_check_payload",
    "_plugins_payload",
    "_rehydrate",
    "_rewrite_inline_output",
    "_run_dir_checked",
    "_run_versions",
    "_validate_indices",
    "_validate_ranges_payload",
    "_validate_segments_payload",
    "_validate_time_pair",
    "_version_text",
    "Any",
    "Callable",
    "Path",
    "annotations",
    "config",
    "json",
    "now_iso",
    "os",
    "plugins_service",
    "psp",
    "re",
    "resolve_adjudicate_post",
    "run_adjudicate",
    "run_regenerate",
    "shutil",
    "subprocess",
    "tempfile",
    "validate_pipeline_contract",
]
