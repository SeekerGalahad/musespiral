"""编辑器 AI 微操作（E-3，正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.3）。

五个动作 → 五个 chat 任务键（TASKS 白名单同批扩键，`editor_expand` /
`editor_summarize` / `editor_rewrite` / `editor_continue` / `editor_qa`）：

  - expand     选中几句 → 扩写（补细节，不编造事实）
  - summarize  选中一段 → 摘要（1-3 句）
  - rewrite    换个说法（原意不变、语言更自然）
  - continue   续写两句（顺文风，不重复已有内容）
  - qa         问 AI 关于这段（产可直接采用的说明性段落）

形态与 refine/tag_suggest 一致：**AI 只起草，人工在 diff 弹窗里接受才写回
编辑器**——本服务只回 `{gen}`，零落盘、零 job（拒绝即消失不留痕；job result
持久化即预览留痕，违反同一判例）。写回动作在前端（CM5 replaceSelection +
`<!--ai:block-->` 包裹），后端不碰正文文件。

治理：走 `chat_policed`（缓存/预算/台账五段序），`cache=False`——改写类生成
任务恒不缓存（F79 §八-3），同一选区改一次与改两次的产出本就允许不同。

模板覆盖：`~/.musespiral/prompts.d/<task>.txt` 整体覆盖内置缺省（F47 级联同纪律），
`{{text}}` / `{{context}}` 槽位控制注入位置；生效模板文本透传 gateway 的
`template` 参（F79.1：内置模板一改，旧缓存自然失效）。模板零模型/供应商字样
（方言守门扫描面已登记本文件）。

G-T1 Ghost Text 续写（2026-11-19，正典=《musespiral-调研吸收功能设计-2026-11-18》
§二）：本文件第二批能力——编辑器内 AI 灰色预判续写（与五动作的「选中→弹窗」
不同，ghost 是无选中的行内预判）。同一治理面（chat_policed，usage 自动计入
F79），独立任务键 `ghost_write`（temperature 0.8 创意偏高、产出硬帽 200 字）：
`run_ghost(context, title, tags)` 只回 `{task, ghost}`——零落盘零 job（同批
「拒绝即消失不留痕」判例），采纳/忽略与防抖/cooldown 全在前端（editor.js）。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..infra.ai import AIError, ai_configured
from ..infra.ai.gateway import chat_policed
from ..infra.ai.prompts import load_override
from .rewrite_service import _strip_code_fence

# 动作 → 任务键（唯一映射表；studio 的 action 枚举与 TASKS 扩键同源）
EDITOR_ACTIONS: dict[str, str] = {
    "expand": "editor_expand",
    "summarize": "editor_summarize",
    "rewrite": "editor_rewrite",
    "continue": "editor_continue",
    "qa": "editor_qa",
}

# 动作标签（错误文案与前端标题共用同一中文名；前端只做展示）
ACTION_LABELS: dict[str, str] = {
    "expand": "扩写",
    "summarize": "摘要",
    "rewrite": "改写",
    "continue": "续写",
    "qa": "问答",
}

# 温度：创意类偏高、压缩/答疑类偏低（§3.3 模板配置节同向）
ACTION_TEMPERATURE: dict[str, float] = {
    "expand": 0.7,
    "summarize": 0.3,
    "rewrite": 0.5,
    "continue": 0.7,
    "qa": 0.3,
}

# 输入预算（字符）：单次选区/上下文上限（防整篇塞入；超限由 studio 层 400）
TEXT_MAX_CHARS = 20_000
CONTEXT_MAX_CHARS = 8_000

# ── G-T1 Ghost Text（§二）────────────────────────────────────────────
# 温度 0.8（§2.4：ghost 属创意预判，比 editor_continue 的 0.7 更放得开）；
# 产出硬帽 200 字（设计 max_out=200 的服务端字符口径——gateway 无 token 上
# 限参数，与 G-T4 rolling_summary 的「服务端字符截断兜底」同一机制）。
GHOST_TASK = "ghost_write"
GHOST_TEMPERATURE = 0.8
GHOST_MAX_CHARS = 200

_TEXT_SLOT = "{{text}}"
_CONTEXT_SLOT = "{{context}}"
_GHOST_TITLE_SLOT = "{{title}}"
_GHOST_TAGS_SLOT = "{{tags}}"

# 内置任务底座模板（prompts.d/<task>.txt 存在即整体覆盖）
DEFAULT_PROMPTS: dict[str, str] = {
    "editor_expand": """你是资深中文编辑。请把下面这段文字扩写得更丰满：补足细节、衔接与例证，让读者读得下去。

要求：
- 保留原文全部事实与观点，不编造数据、引文或案例
- 与原文语气一致，不堆砌形容词与排比
- 只输出扩写后的 Markdown 正文，不要输出解释、不要加标题前缀

【选中文字】
{{text}}""",
    "editor_summarize": """你是资深中文编辑。请把下面这段文字压缩成 1-3 句摘要。

要求：
- 保留原文的核心结论与关键事实（数字、名称、版本）
- 不添加原文没有的推断
- 只输出摘要正文，不要输出解释

【选中文字】
{{text}}""",
    "editor_rewrite": """你是资深中文编辑。请换一种说法重写下面这段文字。

要求：
- 原意与事实完全不变，只调整措辞与语序
- 语言更自然、更短促有力，避免营销腔
- 只输出重写后的 Markdown 正文，不要输出解释

【选中文字】
{{text}}""",
    "editor_continue": """你是资深中文编辑。请顺着下面这段文字续写 2-3 句。

要求：
- 承接原文的论证方向与语气，不重复已说过的内容
- 不编造数据、引文或案例
- 只输出续写的正文（不含原文），不要输出解释

【选中文字】
{{text}}""",
    "editor_qa": """你是资深中文编辑。请针对下面这段文字，回答作者的疑问并给出可直接采用的说明性段落。

要求：
- 只依据这段文字提供的信息作答，信息不足时明确说出缺什么，不编造
- 答案自成一段，可直接放进正文
- 只输出答案正文，不要输出解释

【选中文字】
{{text}}""",
    # G-T1 Ghost Text（§二）：行内预判续写——非全文、不解释、不重复已有内容；
    # 前端以灰色半透明渲染、Tab 才真正落稿，输出短（硬帽 200 字见 GHOST_MAX_CHARS）。
    # 标题/标签两行槽位在值缺省时由 build_ghost_prompt 整行剪除（不留空标签行）。
    "ghost_write": """你是资深中文写作者的「隐身陪写伙伴」。请顺着作者当前写到的位置，自然续写一小段（约 80 字以内）。

文章标题：{{title}}
文章标签：{{tags}}

要求：
- 承接上文语气与论证方向，像作者自己会写出的下一句，不重复已有内容
- 不编造数据、引文或案例；不替作者下最终结论
- 只输出续写正文，不要解释、不要标题、不要任何前缀或引号

【上下文】
{{context}}""",
}


class EditorAIError(ValueError):
    """编辑器微操作业务错误（可读消息；studio 转 400/ok:false）。"""


ChatFn = Callable[..., str]


def _blocks(text: str, context: str | None) -> str:
    """组装注入块：选中文字必在；上下文（选填）在前，供模型判断语气与位置。"""
    parts: list[str] = []
    if context:
        parts.append(f"【上下文】（仅供理解，不要重复输出）\n{context}")
    parts.append(f"【选中文字】\n{text}")
    return "\n\n".join(parts)


def build_prompt(action: str, text: str, context: str | None = None) -> tuple[str, str]:
    """组装一次微操作的 prompt → `(prompt, 生效模板)`。

    覆盖文件（prompts.d/<task>.txt）> 内置缺省。注入规则：

      - 模板含 `{{text}}`/`{{context}}` 槽位 → 原位替换（模板自己决定注入位置）；
      - 模板只含 `{{text}}`（内置五模板即此形）→ 上下文块**追加在末尾**——
        否则上下文会被静默丢弃（本批实测踩到：只 replace 不追加，上下文永不进
        prompt，前端传了也白传）；
      - 模板不含任何槽位 → 注入块（上下文 + 选中文字）追加在末尾。

    生效模板随 prompt 返回，供 gateway `template` 参（F79.1 口径）。
    """
    task = EDITOR_ACTIONS.get(action)
    if task is None:
        raise EditorAIError(
            f"未知动作: {action!r}（可用: {'/'.join(EDITOR_ACTIONS)}）"
        )
    base = load_override(task) or DEFAULT_PROMPTS[task]
    if _CONTEXT_SLOT in base:
        return base.replace(_TEXT_SLOT, text).replace(_CONTEXT_SLOT, context or ""), base
    if _TEXT_SLOT in base:
        rendered = base.replace(_TEXT_SLOT, text)
        if context:
            rendered = f"{rendered}\n\n【上下文】（仅供理解，不要重复输出）\n{context}"
        return rendered, base
    return f"{base}\n\n{_blocks(text, context)}", base


def run_action(
    action: str,
    text: str,
    context: str | None = None,
    *,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """执行一次 AI 微操作 → `{action, task, gen}`（gen 已剥围栏、去首尾空白）。

    参数校验（动作枚举/选中文字非空/长度）→ EditorAIError 可读拒绝；AI 未配置
    或调用失败 → AIError（studio 转 200 {ok:false} 降级面，同 annotate_rewrite）。
    空响应视作 AI 失败（AIError），不把空串交给前端去替换掉用户文字。
    """
    task = EDITOR_ACTIONS.get(action)
    if task is None:
        raise EditorAIError(
            f"未知动作: {action!r}（可用: {'/'.join(EDITOR_ACTIONS)}）"
        )
    if not isinstance(text, str) or not text.strip():
        raise EditorAIError("选中文字不能为空")
    if len(text) > TEXT_MAX_CHARS:
        raise EditorAIError(f"选中文字过长（{len(text)} 字符，上限 {TEXT_MAX_CHARS}）")
    if context is not None and not isinstance(context, str):
        raise EditorAIError("context 必须是字符串")
    if context and len(context) > CONTEXT_MAX_CHARS:
        context = context[:CONTEXT_MAX_CHARS]
    if not ai_configured(task):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    prompt, effective_template = build_prompt(action, text, context)
    res = chat_policed(
        [{"role": "user", "content": prompt}],
        task=task,
        temperature=ACTION_TEMPERATURE[action],
        cache=False,  # 生成类恒不缓存（F79 §八-3）
        template=effective_template,
        chat_fn=chat_fn,
    )
    gen = _strip_code_fence(str(res.get("text") or "")).strip()
    if not gen:
        raise AIError("AI 未返回可用内容（空响应）")
    return {"action": action, "task": task, "gen": gen}


# ── G-T1 Ghost Text 续写（§二）───────────────────────────────────────


def build_ghost_prompt(
    context: str,
    title: str | None = None,
    tags: list[str] | str | None = None,
) -> tuple[str, str]:
    """组装一次 ghost 续写的 prompt → `(prompt, 生效模板)`。

    槽位：`{{context}}` 必在（续写的锚）；`{{title}}` / `{{tags}}` 有值才
    原位替换，缺省时连同其「文章标题：/文章标签：」标签行整行剪除（内置
    模板不留空标签行）。覆盖级联同五动作：`prompts.d/ghost_write.txt` 整体
    覆盖内置缺省；模板不含任何槽位 → 上下文块追加在末尾（不静默丢输入，
    build_prompt 同判例）。生效模板随 prompt 返回，供 gateway `template` 参
    （F79.1 缓存失效口径）。
    """
    base = load_override(GHOST_TASK) or DEFAULT_PROMPTS[GHOST_TASK]
    tag_text = ""
    if isinstance(tags, str):
        tag_text = tags.strip()
    elif isinstance(tags, (list, tuple)):
        tag_text = "、".join(str(t).strip() for t in tags if str(t).strip())
    title_text = str(title or "").strip()
    rendered = (
        base.replace(_GHOST_TITLE_SLOT, title_text)
        .replace(_GHOST_TAGS_SLOT, tag_text)
    )
    if not title_text:
        rendered = re.sub(r"^文章标题：[ \t]*$\n?", "", rendered, flags=re.M)
    if not tag_text:
        rendered = re.sub(r"^文章标签：[ \t]*$\n?", "", rendered, flags=re.M)
    if _CONTEXT_SLOT in rendered:
        rendered = rendered.replace(_CONTEXT_SLOT, context)
    else:
        rendered = f"{rendered}\n\n【上下文】\n{context}"
    return rendered.strip(), base


def run_ghost(
    context: str,
    title: str | None = None,
    tags: list[str] | str | None = None,
    *,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """执行一次 ghost 续写 → `{task, ghost}`（ghost 已剥围栏、去首尾、帽 200 字）。

    与 run_action 同形态：参数校验 → EditorAIError 可读拒绝；AI 未配置或
    调用失败 → AIError（studio 转 200 {ok:false} 降级面）；空响应视作失败。
    `cache=False`（生成类恒不缓存）；usage 由 chat_policed 记账（task=
    ghost_write 行，F79 台账与 CLI ai usage 同数）。
    """
    if not isinstance(context, str) or not context.strip():
        raise EditorAIError("context 不能为空（ghost 需要可承接的上下文）")
    if title is not None and not isinstance(title, str):
        raise EditorAIError("title 必须是字符串")
    if context and len(context) > CONTEXT_MAX_CHARS:
        context = context[:CONTEXT_MAX_CHARS]
    if not ai_configured(GHOST_TASK):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    prompt, effective_template = build_ghost_prompt(context, title, tags)
    res = chat_policed(
        [{"role": "user", "content": prompt}],
        task=GHOST_TASK,
        temperature=GHOST_TEMPERATURE,
        cache=False,  # 生成类恒不缓存（F79 §八-3）
        template=effective_template,
        chat_fn=chat_fn,
    )
    gen = _strip_code_fence(str(res.get("text") or "")).strip()
    if not gen:
        raise AIError("AI 未返回可用内容（空响应）")
    return {"task": GHOST_TASK, "ghost": gen[:GHOST_MAX_CHARS]}
