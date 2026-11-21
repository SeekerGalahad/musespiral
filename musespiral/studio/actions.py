"""Studio POST 动作路由注册表（R-2 按域拆分：执行体落 handlers/ 四模块）。

  - 本文件只保留唯一注册表 _ACTION_ROUTES（四域 ROUTES 片段合并）+ 兼容
    re-export 面（行为零变更）。保留原因：studio/__init__（§七 迁移兼容，
    「不删」）与既有测试/兄弟模块的补丁锚点、惰性导入都瞄准
    musespiral.studio.actions 命名空间，逐一保持可用：
      * publish_flow 补丁锚点（test_studio patch studio_actions.publish_flow；
        执行体 handlers/platform._action_publish 经 actions 命名空间晚绑定
        读取，补丁语义不变）
      * adapt / url_fetch 模块锚点（patch 落共享模块属性，锚名必须留此）
      * _runner / _reset_runner / _submit / _assert_fetch_url（兄弟动作模块
        函数体内 `from .actions import …` 的既有惰性导入面）
      * _discover_star_payload（routing.py 顶部导入）
      * _action_adapt/_action_pack/_action_publish/_assert_asset_bounds
        （studio/__init__ 兼容 re-export，§七）
  - 六域拆分（方案 §一 R-2 + 行数门二拆）：handlers/compose（/api/compose/*
    10）/ materials（materials+sources+ingest 15，N-2 批 +3）/ article（article+editor+
    annotations+visual 11）/ platform（action+jobs+settings+其余 22）/
    discover（发现/回采 3）/ assist（AI 辅助预览 3）
  - 长动作契约不变：建 job 返 {ok, job_id}（R4），产物在 job.result；
    二进制旁路 /api/materials/upload 仍在 server._BINARY_POST_ROUTES
    （不占本表，契约锁取并集对拍）
  - 契约锁：CONTRACT_POST_EXACT 62 = 本表 61 ∪ 二进制旁路 1，增删路由
    先改契约文档再改表（test_architecture 红灯兜底）；GET 面注册表仍在
    routing.py（_GET_ROUTES，42 条，本文件不承载 GET）
"""
from __future__ import annotations

from typing import Any, Callable

# ---- 补丁锚点保留（正典 studio/__init__ §七：patch 请打在 .actions 上）----
from ..core import adaptation as adapt
from ..infra.sources import url_fetch
from ..platforms.wechat import publish_flow
from .handlers.article import ROUTES as _ARTICLE_ROUTES
from .handlers.assist import ROUTES as _ASSIST_ROUTES
from .handlers.compose import ROUTES as _COMPOSE_ROUTES
from .handlers.discover import (
    ROUTES as _DISCOVER_ROUTES,
    _discover_star_payload,  # routing.py 顶部 from .actions import 的既有导入面（re-export）
)
from .handlers.materials import ROUTES as _MATERIALS_ROUTES
from .handlers.platform import (
    ROUTES as _PLATFORM_ROUTES,
    _action_adapt,
    _action_pack,
    _action_publish,
    _assert_asset_bounds,
    _assert_fetch_url,
    _reset_runner,
    _runner,
    _submit,
)
from .plugin_actions import ROUTES as _PLUGIN_ROUTES  # PL-3 接驳批（复核修复 2026-11-20）
from .security import _StudioContext

_ACTION_ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    **_COMPOSE_ROUTES,  # /api/compose/*（10）
    **_MATERIALS_ROUTES,  # /api/materials/* + /api/sources/* + /api/ingest/*（15，N-2 +3）
    **_ARTICLE_ROUTES,  # /api/article|editor|annotations|visual/*（11）
    **_PLATFORM_ROUTES,  # /api/action|jobs|settings/* + 其余（22）
    **_DISCOVER_ROUTES,  # /api/action/discover_draft|sync_stats + /api/discover/star（3）
    **_ASSIST_ROUTES,  # /api/action/cover|refine|tags_suggest（3）
    **_PLUGIN_ROUTES,  # /api/plugins/refresh + /api/pipelines(±run)（3，契约 §3.18）
}
