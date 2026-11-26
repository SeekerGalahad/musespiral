"""article 域动作：/api/article/* + /api/editor/* + /api/annotations/* + /api/visual/* + /api/action/{quality_loop,title_ab}（14 条）。

R-2 按域登记（行为零变更）：执行体在前批已下沉的既有模块——edit_actions
（R7 保存/批注增删/按批注 AI 改写）、editor_actions（E-3 微操作 + G-T1
Ghost Text）、visual_actions（Phase C F70-F74 四动作）、version_actions
（E-6 版本恢复）、quality_actions（P1 双件 2026-11-23），本模块按域 re-export
并登记 ROUTES 片段（执行体模块原位不动：测试按文件路径/模块属性锚定）。
"""
from __future__ import annotations

from typing import Any, Callable

from ..edit_actions import (  # R7：编辑保存/批注增删/按批注 AI 改写
    _action_annotate_rewrite,
    _action_annotation_add,
    _action_annotation_delete,
    _action_save,
)
from ..editor_actions import (  # E-3 编辑器 AI 微操作（§3.11）+ G-T1 Ghost Text 续写 + B3 插入媒体
    _action_editor_ai,
    _action_editor_ghost,
    _action_insert_media,
)
from ..quality_actions import (  # P1 双件（契约 §3.26）：质量循环 job + 标题 A/B
    _action_quality_loop,
    _action_title_ab,
)
from ..version_actions import _action_article_restore  # E-6 版本恢复（2026-11-19，契约 §3.17）
from ..visual_actions import (  # Phase C F70-F74：/api/visual 族四动作
    _action_visual_export,
    _action_visual_preset,
    _action_visual_render,
    _action_visual_save,
)
from ..security import _StudioContext

# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/article/save": _action_save,  # R7：编辑保存（frontmatter 不动；edit_actions）
    "/api/article/restore": _action_article_restore,  # E-6 2026-11-19：版本池恢复（§3.17；当前版先入池）
    "/api/annotations/add": _action_annotation_add,  # R7：批注追加（GET 读端点在 routing）
    "/api/annotations/delete": _action_annotation_delete,  # R7：批注删除（裁决：POST 承载）
    "/api/action/annotate_rewrite": _action_annotate_rewrite,  # R7：按批注 AI 改写预览
    "/api/editor/ai-action": _action_editor_ai,  # E-3 2026-11-19：编辑器微操作五动作（§3.11-1）
    "/api/editor/ghost-write": _action_editor_ghost,  # G-T1 2026-11-19：Ghost Text 行内续写（调研吸收 §二）
    "/api/visual/render": _action_visual_render,  # Phase C F71/F73：真 SVG 预览（纯计算零落盘）
    "/api/visual/save": _action_visual_save,  # Phase C F71/F72：设计稿落盘（版本池先行）
    "/api/visual/export": _action_visual_export,  # Phase C F71-F73：导出 job visual_export
    "/api/visual/preset": _action_visual_preset,  # Phase C F74：预设保存（纯 JSON）
    # B3 2026-11-26：编辑器插入媒体（素材文件 → 文章 media/，durable 副本）
    "/api/action/insert_media": _action_insert_media,
    "/api/action/quality_loop": _action_quality_loop,  # P1-A 2026-11-23：质量循环（check→AI 修复→再 check，job）
    "/api/action/title_ab": _action_title_ab,  # P1-B 2026-11-23：标题 A/B 三风格（generate 同步 / apply 写回）
}
