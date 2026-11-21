"""compose 域动作：/api/compose/*（10 条）。

R-2 按域登记（行为零变更）：10 个执行体在第十八期 F48 起已下沉的
compose_actions.py（创作台动作，services 层经 compose_service 门面），本
模块按域 re-export 并登记 ROUTES 片段（执行体模块原位不动——其函数体内
`from .actions import _runner, _submit` 的既有惰性导入面与测试锚定不变）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..compose_actions import (  # 第十八期 F48-F52：创作台动作（M2 接线；UI 归 M3）
    _action_compose_add,
    _action_compose_adopt,
    _action_compose_decide,
    _action_compose_dual,  # G-T3 2026-11-19：双模型对比（§四；generate/pick/keep 三形态）
    _action_compose_generate,
    _action_compose_new,
    _action_compose_outline,  # G-T2 2026-11-18：大纲模式（§三）
    _action_compose_remove_source,  # F-C 2026-11-18：素材撤池
    _action_compose_novel_summary,  # E-5 2026-11-19：小说上下文（前情提要+人物卡，§3.6）
    _action_compose_rolling_summary,  # G-T4 2026-11-19：滚动摘要刷新/手改（§五）
)
from ..security import _StudioContext

# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/compose/new": _action_compose_new,  # 第十八期 F48：建会话（同步瞬时）
    "/api/compose/add": _action_compose_add,  # F48：素材入池（章节勾选复用 scanner）
    "/api/compose/remove_source": _action_compose_remove_source,  # F-C 2026-11-18：素材撤池（同步瞬时）
    "/api/compose/generate": _action_compose_generate,  # F48：再生成（job compose_generate）
    "/api/compose/decide": _action_compose_decide,  # F49：逐块三态裁决
    "/api/compose/adopt": _action_compose_adopt,  # F52：采纳入稿（合并+habits+正稿入队列）
    "/api/compose/outline": _action_compose_outline,  # G-T2：大纲模式 generate|update|approve|expand（§三）
    "/api/compose/rolling-summary": _action_compose_rolling_summary,  # G-T4：滚动摘要刷新/手改（§五）
    "/api/compose/novel-summary": _action_compose_novel_summary,  # E-5：小说上下文（前情提要+人物卡，§3.6）
    "/api/compose/dual": _action_compose_dual,  # G-T3：双模型对比 generate|pick|keep（§四）
}
