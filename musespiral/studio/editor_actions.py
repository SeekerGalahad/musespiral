"""Studio E-3 编辑器 AI 微操作 POST 动作（正典=《musespiral-专业创作者视角深度评审与
改进-2026-11-18》§3.3）＋ G-T1 Ghost Text 续写（正典=《musespiral-调研吸收功能设计-
2026-11-18》§二）。

两端点都由本文件承载（拆分纪律同 edit_actions：actions 顶部导入本模块
装配路由表，反向顶部互导会成环）：

  POST /api/editor/ai-action {confirm:true, action, text, context?}
  POST /api/editor/ghost-write {confirm:true, context, title?, tags?}

  - ai-action：action ∈ expand|summarize|rewrite|continue|qa（枚举外 → 400
    可读）；**同步形态、零落盘**：AI 只起草，人工在前端 diff 弹窗里「接受」
    才写回编辑器——服务端既不建 job（job result 持久化=预览留痕，违反
    「拒绝即消失不留痕」refine/tags_suggest 判例）也不碰正文文件（写回在
    前端 CM5 replaceSelection，后端无写面）；
  - ghost-write：无选中的行内预判续写（前端 800ms 防抖触发），同样同步
    零落盘零 job；AI 未配置 / 调用失败 → 200 {ok:false} 可读（脱敏面，
    同 annotate_rewrite）。
"""
from __future__ import annotations

from typing import Any

from ..infra.ai import AIError
from ..services import editor_ai_service
from .security import _HttpError, _StudioContext, _error_payload, _require_str


def _action_editor_ai(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/editor/ai-action → `{ok:true, action, task, gen, original}`。

    响应同时回 `original`（=入参 text 原样）——diff 弹窗的「原文」面不必靠
    前端缓存：选区在 AI 往返期间可能被用户改动，以服务端回显的事实为对照
    基准（前端仍以自身选区做替换，回显仅供展示与校验）。

    400（表单校验）：action 枚举外 / text 缺失或非字符串（`_require_str` 统一
    文案）/ context 非字符串。200 {ok:false}（业务）：文字为空/过长、
    AI 未配置、AI 调用失败、空响应。
    """
    action = data.get("action")
    if not isinstance(action, str) or action not in editor_ai_service.EDITOR_ACTIONS:
        raise _HttpError(
            400,
            "action 必须是 "
            + "/".join(editor_ai_service.EDITOR_ACTIONS)
            + " 之一",
        )
    text = _require_str(data, "text")
    context = data.get("context")
    if context is not None and not isinstance(context, str):
        raise _HttpError(400, "context 必须是字符串（选填，选中文字周边的上下文）")
    try:
        result = editor_ai_service.run_action(action, text, context)
    except editor_ai_service.EditorAIError as err:
        return _error_payload(err, ctx.root)
    except AIError as err:
        return _error_payload(err, ctx.root)
    return {"ok": True, "original": text, **result}


def _action_editor_ghost(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/editor/ghost-write → `{ok:true, task, ghost}`（G-T1 §二）。

    前端（editor.js ghost 控制器）在「用户停笔 800ms 且光标在行尾/空行」时
    调用：`context`=当前行 ±1 行拼接，`title`/`tags` 选填（文章 frontmatter
    提取，供模型对题）。服务端无状态——采纳（Tab）/忽略（Esc）/防抖/cooldown
    全在前端；后端每次调用都是独立的一次 ghost_write 任务（F79 usage 按
    task 行记账，前端请求数即台账数）。

    400（表单校验）：context 缺失或非字符串（`_require_str` 统一文案）/
    title 非字符串 / tags 既非字符串数组也非字符串。200 {ok:false}（业务）：
    context 纯空白、AI 未配置、AI 调用失败、空响应。
    """
    context = _require_str(data, "context")
    title = data.get("title")
    if title is not None and not isinstance(title, str):
        raise _HttpError(400, "title 必须是字符串（选填，文章标题）")
    tags = data.get("tags")
    tags_ok = tags is None or isinstance(tags, str) or (
        isinstance(tags, list) and all(isinstance(t, str) for t in tags)
    )
    if not tags_ok:
        raise _HttpError(400, "tags 必须是字符串数组（选填，文章标签）")
    try:
        result = editor_ai_service.run_ghost(context, title, tags)
    except editor_ai_service.EditorAIError as err:
        return _error_payload(err, ctx.root)
    except AIError as err:
        return _error_payload(err, ctx.root)
    return {"ok": True, **result}
