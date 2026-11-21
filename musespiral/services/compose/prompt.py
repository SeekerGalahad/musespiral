"""compose 包：prompt（prompt 组装——插槽序、_fill_slots、各 block 渲染）。

R-1 六簇拆分的 prompt 簇：任务底座模板（prompts.d 覆盖级联）、固定插槽序
（style → sources → novel → characters → rolling → draft → decisions）、
单遍插槽填充、素材节选预算与缺槽回补。
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core.composition import (
    PER_SOURCE_BUDGET,
    ROUND_CONTEXT_K,
    SESSION_BUDGET,
    CompositionSession,
    Round,
    excerpt_sources as _excerpt_sources,
    markdown_from_blocks,
    round_diff_summary as _round_diff_summary,
    select_sections as _select_sections,
)
from ...core.discovery import scanner
from ...infra.ai.habits_inject import load_style_samples, style_block
from .characters import _CHARACTERS_HEADER, _session_characters, characters_block
from .summary import _NOVEL_HEADER, _ROLLING_HEADER, novel_block, rolling_block

# prompts.d 覆盖级联（F47 契约）：读侧统一走 infra/ai/prompts.load_override
# （键域校验+方言守门的单一持有者）；~/.musespiral/prompts.d/compose.txt 存在
# 即整体覆盖内置缺省——编辑面归设置面 M1，本层只消费。

# 任务底座模板（含输出协议指令；插槽按 §2.3 固定顺序出现，{{rolling}} 为
# G-T4 滚动摘要插槽——有摘要才注入，空则整节缺席）
DEFAULT_TEMPLATE = """你是资深公众号作者，正在基于素材创作/迭代一篇公众号文章。当前处于分块协作模式：你会拿到素材节选、当前真稿与上一轮的人工裁决反馈，请输出整篇最新稿。

输出协议（严格遵守）：
- 每个内容块以一行定界符开始：<<<block kind:para>>>（kind ∈ heading|para|code|quote|image）
- 改写上一轮已有的块时，在定界符中携带其原 id 作锚：<<<block id:b3 kind:para>>>（b3 为该块在当前真稿中的锚 id）
- 全新内容块省略 id：<<<block kind:para>>>
- 定界符之后是该块的 Markdown 文本，直到下一个定界符或全文结束
- 只输出块序列，不要解释协议，不要输出 frontmatter

{{style}}

【素材节选】
{{sources}}

{{novel}}

{{characters}}

{{rolling}}

【当前真稿】（<!--b:id--> 为块锚，人工可能已直接编辑内容）
{{draft}}

【上一轮人工裁决】（下一轮请遵循：丢弃块按方向改写或弃，人工改稿以人工定稿为准）
{{decisions}}"""

# 空态占位（插槽空值时的可见说明——模型不该猜）
_EMPTY_SOURCES = "（未添加素材）"
_EMPTY_DRAFT = "（空——首轮创作）"
_EMPTY_DECISIONS = "（无——首轮或上轮未裁决）"


# ---- prompt 组装（§2.3 管线；纯文本拼装，插槽化）----


def load_task_template(task: str = "compose") -> str:
    """任务底座模板：prompts.d 覆盖级联（F47 读侧经 infra/ai/prompts）。"""
    from ...infra.ai.prompts import load_override

    return load_override(task).strip() or DEFAULT_TEMPLATE


def _format_decisions(round_: Round) -> str:
    if not round_.decisions:
        return _EMPTY_DECISIONS
    by_block = {b.id: b for b in round_.blocks}
    lines: list[str] = []
    for d in round_.decisions:
        bid = str(d.get("block") or "")
        block = by_block.get(bid)
        preview = (block.text.strip().replace("\n", " ")[:30] + "…") if block else ""
        action = d.get("action")
        if action == "keep":
            lines.append(f"- 块 {bid}（{preview}）：保留——人工认可原样")
        elif action == "adopt_edited":
            text = str(d.get("text") or "")
            shown = text if len(text) <= 200 else text[:200] + "…"
            lines.append(f"- 块 {bid}：人工已改稿采纳，以人工定稿为准：{shown}")
        elif action == "discard":
            lines.append(f"- 块 {bid}（{preview}）：已丢弃——原因/方向：{d.get('direction', '')}")
    return "\n".join(lines) if lines else _EMPTY_DECISIONS


_SLOT_ORDER = ("style", "sources", "novel", "characters", "rolling", "draft", "decisions")
_SLOT_HEADERS = {
    "style": "【写作风格】",
    "sources": "【素材节选】",
    "novel": _NOVEL_HEADER,
    "characters": _CHARACTERS_HEADER,
    "rolling": _ROLLING_HEADER,
    "draft": "【当前真稿】",
    "decisions": "【上一轮人工裁决】",
}

# 插槽 token（单遍填充用；键域=模板内实际出现的 {{word}}）
_SLOT_TOKEN_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def _fill_slots(template: str, values: dict[str, str | None]) -> str:
    """单遍插槽填充：扫描一遍模板，命中的 {{key}} 一次性替换。

    修复语义（2026-11-19 集成修复批）：逐键 str.replace 会让**先替换进去
    的值里残留的 {{token}} 被后续键再吃一次**——摘要是用户可手改、AI 生成
    也可能带花括号的自由文本，direction 同理；单遍填充下值内容绝不参与
    再扫描。values 外的未知 token 原样保留；值为 None/空串 → 替换为空。
    """

    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key in values:
            return values[key] or ""
        return m.group(0)

    return _SLOT_TOKEN_RE.sub(_sub, template)


def build_prompt(session: CompositionSession, *, template: str | None = None) -> str:
    """按固定序组装 prompt（插槽填充 + 缺槽回补 + token 治理）。

    素材侧：勾选章节 → 双层预算（2000/素材、8000/会话）截断+标注；
    轮次侧：{{decisions}} = 上轮逐块裁决（含 discard 方向）+ 最近 K=2 轮
    块级 diff 摘要（不整稿重放）；
    滚动摘要侧（G-T4 §五）：{{rolling}} = 会话 rolling_summary 整节（含
    节标题）——**空摘要整节缺席**（不注入空占位：模型不该被「无摘要」
    的说明干扰），有摘要则进入固定插槽位（素材后、真稿前）；
    小说模式侧（E-5 §3.6）：{{novel}} = 前情提要整节、{{characters}} = 挂卡
    人物卡渲染行——同款「空则整节缺席」；两者与 rolling 并存互不覆盖
    （正典要求两摘要可并存：rolling 记近期脉络、novel 记此前各章）。
    """
    tpl = template if template is not None else load_task_template()
    sources_payload: list[dict] = []
    for s in session.sources:
        if s.material_id:
            # 素材库直引条（N-2 引用自己）：path 是 material://<id> 虚拟
            # 定位符，内容经素材库现取 payload.text——素材已删/不在册时
            # 跳过不塌（清单仍可见，同下方文件被移走分支语义）。
            from .. import materials_service  # 函数体内导入（导入期解耦）

            material = materials_service.get_material(s.material_id)
            if material is None:
                continue
            content = str((material.get("payload") or {}).get("text") or "")
            title = str((material.get("origin") or {}).get("title") or s.material_id)
            sources_payload.append({"path": s.path, "title": title, "content": content})
            continue
        try:
            p = Path(s.path).expanduser()
            _, body = scanner.split_frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue  # 素材被移走：跳过不塌（清单仍可见）
        content = _select_sections(body, s.sections)
        title = scanner.scan_file(p)["title"] if p.is_file() else p.name
        sources_payload.append({"path": s.path, "title": title, "content": content})
    sources_text = (
        _excerpt_sources(sources_payload, PER_SOURCE_BUDGET, SESSION_BUDGET)[0]
        if sources_payload
        else _EMPTY_SOURCES
    )
    draft_text = markdown_from_blocks(session.draft) if session.draft else _EMPTY_DRAFT
    decisions_text = _format_decisions(session.rounds[-1]) if session.rounds else _EMPTY_DECISIONS
    if session.rounds:
        summary = _round_diff_summary(session.rounds, session.draft, ROUND_CONTEXT_K)
        if summary:
            decisions_text += f"\n\n近几轮块级变化：\n{summary}"
    style_text = style_block(_load_habits_for_style(), load_style_samples())
    values = {
        "style": style_text,
        "sources": sources_text,
        # E-5（§3.6）：前情提要与人物卡同款「空则整节缺席」
        "novel": novel_block(session.novel_summary) or None,
        "characters": characters_block(_session_characters(session)) or None,
        # 空摘要 → None：令牌位清空且不回补（不注入「无摘要」占位）
        "rolling": rolling_block(session.rolling_summary) or None,
        "draft": draft_text,
        "decisions": decisions_text,
    }
    # 单遍填充（_fill_slots）：值内残留的 {{token}} 不参与再扫描——摘要/
    # 方向等自由文本面的花括号内容保持字面（防二次吃 token）。
    present = {k: ("{{" + k + "}}") in tpl for k in _SLOT_ORDER}
    prompt = _fill_slots(tpl, {k: values[k] for k in _SLOT_ORDER if present[k]})
    for k in _SLOT_ORDER:
        if present[k]:
            values[k] = None  # 已消费，不再回补
    # 模板缺槽回补：按固定顺序附加（自定义模板漏插槽也不丢上下文）。
    # style/rolling 的值自带节标题 → 回补时不再重复加标题（否则双标题）。
    tail = "\n\n".join(
        (values[k] if values[k].startswith(_SLOT_HEADERS[k]) else f"{_SLOT_HEADERS[k]}\n{values[k]}")
        for k in _SLOT_ORDER
        if values[k] is not None
    )
    if tail:
        prompt = f"{prompt}\n\n{tail}"
    return prompt.strip()


def _load_habits_for_style():
    from ...core.habits import load_habits

    return load_habits()


def build_messages(session: CompositionSession, *, template: str | None = None) -> list[dict[str, str]]:
    """chat 消息面（单 user 消息——style 已按管线序内嵌在 prompt 内）。"""
    return [{"role": "user", "content": build_prompt(session, template=template)}]
