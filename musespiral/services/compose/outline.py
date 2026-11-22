"""compose 包：outline（G-T2 大纲模式——CRUD / 裁决 / 逐节展开）。

R-1 六簇拆分的大纲簇：outline.json 的宽容读/原子写、大纲读写与 AI 生成、
逐节裁决（approve gating）与逐节展开（每节独立请求进块协议，产出走与
generate 完全相同的落盘/匹配链）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...core.composition import (
    STATUS_APPROVED,
    STATUS_COMPOSING,
    STATUS_EXPANDED,
    STATUS_PENDING,
    OutlineSection,
    Round,
    match_round,
    outline_from_json,
    outline_to_json,
    parse_ai_output,
    validate_sections,
)
from ...infra.ai import chat as _chat
from ...infra.ai.habits_inject import load_style_samples, style_block
from .characters import _session_characters, characters_block, scan_conflicts
from .foreshadowing import foreshadowing_block, open_foreshadowing
from .prompt import _fill_slots, _load_habits_for_style
from .store import (
    ComposeError,
    DECISIONS_SUFFIX,
    _load_session,
    _rounds_dir,
    _save_meta,
    _serialized,
    _write_json_atomic,
    session_dir,
)
from .summary import novel_block, rolling_block

OUTLINE_FILE = "outline.json"  # G-T2 大纲模式（§三）

# G-T2 大纲生成/展开模板（§三）：task 复用 "compose" 键（不新增 TASKS——
# 与 G-T4 rolling_summary 的 TASKS 扩键互不冲突）。prompts.d 覆盖级联对
# compose_outline/compose_expand 两个键可选生效（键合法 [a-z0-9_]*；
# outline_generate / expand_section 经 load_override 消费——2026-11-19
# 集成修复批接线，此前注释承诺而代码未读覆盖文件）。
OUTLINE_GEN_TEMPLATE = """你是资深公众号作者，正在为长文规划大纲。请按【主题】输出 4-8 节大纲。
每节一行，格式：标题 | 目标字数 | 方向备注（无方向留空）。只输出大纲行，不要解释。

【主题】
{{title}}

【素材标题】
{{sources}}"""

OUTLINE_EXPAND_TEMPLATE = """你是资深公众号作者，正在逐节展开一篇长文。请只写【本节】的内容，输出块序列。
输出协议：每个内容块以一行 <<<block kind:para>>> 起始（kind ∈ heading|para|code|quote|image），其后为该块 Markdown 文本。只输出块序列，不要解释。
严格遵守本节方向备注，与已展开节的脉络保持一致，不要重复已写内容。

【本节】{{title}}（目标 {{target_chars}} 字）
【本节方向】{{direction}}

{{novel}}

{{characters}}

{{foreshadowing}}

{{rolling}}

【已展开节的脉络摘要】
{{prior}}

【写作风格】
{{style}}"""


def _outline_path(d: Path) -> Path:
    return d / OUTLINE_FILE


def _load_outline(d: Path) -> tuple[list[OutlineSection], str | None]:
    """宽容读大纲文件：缺失/损坏 → 空大纲（不塌宿主）。"""
    path = _outline_path(d)
    if not path.is_file():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], None
    return outline_from_json(data)


def _save_outline(d: Path, sections: list[OutlineSection], current: str | None = None) -> None:
    _write_json_atomic(_outline_path(d), outline_to_json(sections, current_expanding=current))


def outline_get(slug: str, root: str | Path | None = None) -> dict:
    """读大纲（UI 右栏数据面）。无大纲 → {sections:[], current_expanding:null}。"""
    d = session_dir(slug, root)
    if not d.is_dir():
        raise ComposeError(f"会话不存在: {slug}")
    sections, current = _load_outline(d)
    return outline_to_json(sections, current_expanding=current)


@_serialized
def outline_save(
    slug: str, sections_raw: object, root: str | Path | None = None
) -> dict:
    """整体写入大纲（手写或 AI 生成后回写），校验后原子落盘。"""
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），大纲不可改")
    sections = validate_sections(sections_raw)
    _save_outline(d, sections)
    return outline_to_json(sections)


@_serialized
def outline_generate(
    slug: str, root: str | Path | None = None, *, chat_fn=None
) -> dict:
    """AI 生成大纲：compose 任务产出「标题 | 字数 | 方向」行 → 解析成 sections。

    只落 outline.json（不建 job、不进 draft）；用户仍须逐节裁决（approved）
    才能展开。chat_fn 仅供测试注入。底座模板走 prompts.d/compose_outline.txt
    覆盖级联（缺文件回退内置 OUTLINE_GEN_TEMPLATE——本注释承诺自落地起生效，
    2026-11-19 集成修复批接线）。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），大纲不可生成")
    titles = [s.path for s in session.sources] or [session.title or slug]
    from ...infra.ai.prompts import load_override

    prompt = _fill_slots(
        load_override("compose_outline").strip() or OUTLINE_GEN_TEMPLATE,
        {"title": session.title or slug, "sources": "\n".join(titles)},
    )
    messages = [{"role": "user", "content": prompt}]
    call = chat_fn if chat_fn is not None else _chat
    content = call(messages, task="compose", temperature=0.5)
    if not isinstance(content, str) or not content.strip():
        raise ComposeError("AI 生成大纲响应为空")
    sections = _parse_outline_lines(content)
    if not sections:
        raise ComposeError("AI 未产出可解析的大纲（期望每行：标题 | 字数 | 方向）")
    _save_outline(d, sections)
    return outline_to_json(sections)


# 大纲行前缀标记（有序/无序列表记号）：仅剥「符号或 数字+标点」形态——
# 数字后必须跟 [.、)] 且**不紧跟另一数字**才认定是标记，因此 "3 条铁律"、
# "2026 年展望"、"1.5 倍增长的陷阱" 这类数字开头标题不受影响；旧实现
# lstrip("-*0123456789.、 ") 是整字符集剥离，会把标题前导数字吃掉
# （2026-11-19 集成修复判例）。{1,2} 容忍一层嵌套（"- 1. 标题"）。
_OUTLINE_MARKER_RE = re.compile(r"^(?:[-*•]\s+|\d{1,4}\s*[.、)](?!\d)\s*){1,2}")


def _parse_outline_lines(text: str) -> list[OutlineSection]:
    """解析「标题 | 目标字数 | 方向备注」行（容错：无字数/方向按默认）。"""
    out: list[OutlineSection] = []
    for line in text.splitlines():
        line = line.strip()
        line = _OUTLINE_MARKER_RE.sub("", line, count=1).strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        title = parts[0] if parts else ""
        if not title:
            continue
        target = 0
        if len(parts) > 1:
            m = re.search(r"\d+", parts[1])
            if m:
                target = int(m.group())
        direction = parts[2] if len(parts) > 2 else ""
        out.append(OutlineSection(
            id=f"s{len(out) + 1}", title=title, target_chars=target,
            direction=direction, status=STATUS_PENDING,
        ))
    return out


@_serialized
def outline_update_section(
    slug: str, section_id: str, root: str | Path | None = None, **changes
) -> dict:
    """更新单节（标题/方向/字数）；status 变更走 outline_approve，不走此处。"""
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），大纲不可改")
    sections, current = _load_outline(d)
    target = next((s for s in sections if s.id == section_id), None)
    if target is None:
        raise ComposeError(f"大纲节不存在: {section_id}")
    for key in ("title", "direction", "target_chars"):
        if key in changes and changes[key] is not None:
            setattr(target, key, changes[key])
    # 复用数据面校验（改后整表校验，防止 title 空/字数负）
    sections = validate_sections([s.to_dict() for s in sections])
    _save_outline(d, sections, current)
    return outline_to_json(sections, current_expanding=current)


@_serialized
def outline_approve(
    slug: str, section_id: str, approved: bool = True, root: str | Path | None = None
) -> dict:
    """逐节裁决：approved=True → status=approved（可展开）；False → 退回 pending。

    已展开（expanded）节不可回退——进块协议后大纲只是快照。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），大纲不可裁决")
    sections, current = _load_outline(d)
    target = next((s for s in sections if s.id == section_id), None)
    if target is None:
        raise ComposeError(f"大纲节不存在: {section_id}")
    if target.status == STATUS_EXPANDED:
        raise ComposeError(f"节 {section_id} 已展开，不可改裁决")
    target.status = STATUS_APPROVED if approved else STATUS_PENDING
    _save_outline(d, sections, current)
    return outline_to_json(sections, current_expanding=current)


def _section_summary(text: str, limit: int = 80) -> str:
    """展开产出的脉络摘要（去定界符/空白，取前 limit 字）——后续节 prompt 携带。"""
    import re as _re

    clean = _re.sub(r"<<<block[^>]*>>>", "", text or "")
    clean = " ".join(clean.split())
    return clean[:limit] + ("…" if len(clean) > limit else "")


@_serialized
def expand_section(
    slug: str, section_id: str, root: str | Path | None = None, *, chat_fn=None
) -> dict:
    """展开单节（G-T2 核心）：裁决 gating（仅 approved 可展开）→ 调 AI 产块
    → 进正常块协议（rounds/N.md + 块匹配 + decisions 初始化）→ 节标记 expanded
    并记 summary。

    prompt 含本节 direction + 已展开节摘要（防长文跑偏的核心——每节独立请求，
    带上前序脉络）。产出走与 generate 完全相同的落盘/匹配链（复用同款纪律）。
    底座模板走 prompts.d/compose_expand.txt 覆盖级联（缺文件回退内置
    OUTLINE_EXPAND_TEMPLATE——2026-11-19 集成修复批接线，与 compose_outline
    同款注释承诺落地）。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再展开大纲")
    sections, current = _load_outline(d)
    target = next((s for s in sections if s.id == section_id), None)
    if target is None:
        raise ComposeError(f"大纲节不存在: {section_id}")
    if target.status != STATUS_APPROVED:
        raise ComposeError(f"节 {section_id} 尚未裁决（status={target.status}），不可展开（先 approve）")

    prior = "\n".join(
        f"- {s.title}：{s.summary}" for s in sections
        if s.status == STATUS_EXPANDED and s.summary
    ) or "（无——本节为首节）"
    from ...infra.ai.prompts import load_override

    # 单遍填充（_fill_slots）：direction 是用户自由文本，可含 {{…}} 花括号
    # 内容——逐键 replace 会让它被后续插槽再消费（探针判例：direction 写
    # "用 {{prior}} 占位" 被替换成「（无——本节为首节）」）。
    prompt = _fill_slots(
        load_override("compose_expand").strip() or OUTLINE_EXPAND_TEMPLATE,
        {
            "title": target.title,
            "target_chars": str(target.target_chars),
            "direction": target.direction or "（未指定，按标题自由发挥）",
            # G-T4：滚动摘要同款注入（设计 §五「后续所有 compose/generate
            # 请求的 prompt 自动携带」——本节展开也是一次 compose 生成）
            "rolling": rolling_block(session.rolling_summary),
            # E-5（§3.6）：前情提要与人物卡同款注入——大纲逐节展开同样需要
            # 「此前各章 + 角色一致性」，旁路掉会让长文越写越散
            "novel": novel_block(session.novel_summary),
            "characters": characters_block(_session_characters(session)),
            # FEAT-4：未回收伏笔同款注入（展开也是一次 compose 生成——伏笔
            # 恰恰常埋在逐节展开的长文里）
            "foreshadowing": foreshadowing_block(session.foreshadowing),
            "prior": prior,
            "style": style_block(_load_habits_for_style(), load_style_samples()),
        },
    )
    messages = [{"role": "user", "content": prompt}]
    call = chat_fn if chat_fn is not None else _chat
    content = call(messages, task="compose", temperature=0.6)
    if not isinstance(content, str) or not content.strip():
        raise ComposeError("AI 展开大纲响应为空")

    n = (session.rounds[-1].n if session.rounds else 0) + 1
    rounds_dir = _rounds_dir(d)
    rounds_dir.mkdir(parents=True, exist_ok=True)
    (rounds_dir / f"{n}.md").write_text(content, encoding="utf-8")
    previous = session.rounds[-1].blocks if session.rounds else session.draft
    blocks = match_round(parse_ai_output(content), previous, session.taken_ids())
    session.rounds.append(Round(n=n, blocks=blocks))
    _save_meta(session, d)
    _write_json_atomic(rounds_dir / f"{n}{DECISIONS_SUFFIX}", [])

    target.status = STATUS_EXPANDED
    target.summary = _section_summary(content)
    _save_outline(d, sections, None)
    return {
        "section": section_id,
        "round": n,
        "blocks": [b.to_dict() for b in blocks],
        "summary": target.summary,
        "outline": outline_to_json(sections),
        # E-5（§3.6）：展开产出同样过冲突检测（只提示不拦截）
        "conflicts": scan_conflicts(session, blocks),
        # FEAT-4：展开产出同样带未回收伏笔提示（与 generate 同口径）
        "foreshadowing": open_foreshadowing(session.foreshadowing),
    }
