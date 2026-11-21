"""compose 包：characters（人物卡 + 冲突检测）。

R-1 六簇拆分的人物卡簇（E-5 §3.6）：会话挂载面（characters=素材 id 列表，
生成时现取素材库真值）、挂/摘卡 CRUD、会话种类切换（article↔novel 的
元数据面）、前情章节统计与生成后的说话风格冲突检测（只提示不拦截）。
"""
from __future__ import annotations

import re
from pathlib import Path

from ...core.composition import (
    SESSION_KINDS,
    STATUS_COMPOSING,
    Block,
    CompositionSession,
    prior_chapters_text as _prior_chapters_text,
    render_character as _render_character,
)
from .store import (
    ComposeError,
    _load_session,
    _save_meta,
    _working_blocks,
)
from .summary import NOVEL_SUMMARY_INPUT_MAX

_CHARACTERS_HEADER = "【人物卡】（写作时保持一致：外貌、说话风格、既有事件不得与之冲突）"


def _session_characters(session: CompositionSession) -> list[dict]:
    """会话挂卡 → 人物卡 dict 列表（按挂载序；素材已删/非人物卡的 id 静默跳过）。"""
    from .. import materials_service  # 函数体内导入（服务层内解耦）

    out: list[dict] = []
    for mid in session.characters:
        card = materials_service.get_character(mid)
        if card is not None:
            out.append(card)
    return out


def characters_block(cards: list[dict]) -> str:
    """人物卡列表 → prompt 注入节（每卡一行渲染）；空列表返回空串。

    渲染归 ``core.composition.novel.render_character``（L2 纯）——本函数只管
    节标题与换行（**注入点唯一**：正文 compose 与大纲展开经同一 build_prompt/
    同一 helper，不存在第二份拼装）。
    """
    lines = [line for line in (_render_character(c) for c in cards) if line]
    if not lines:
        return ""
    return f"{_CHARACTERS_HEADER}\n" + "\n".join(lines)


def session_characters(slug: str, root: str | Path | None = None) -> list[dict]:
    """读面：会话挂卡的人物卡列表（Studio 右栏「👤 人物卡」数据面）。"""
    session, _ = _load_session(slug, root)
    return _session_characters(session)


def set_session_kind(
    slug: str, kind: str, root: str | Path | None = None
) -> dict:
    """切换会话种类（article ↔ novel，E-5 §3.6「kind=novel 时显示前情提要」）。

    只改元数据，不动 draft/轮次；封版（adopted）会话拒绝（与其余写面同口径）。
    """
    if kind not in SESSION_KINDS:
        raise ComposeError(f"kind 必须是 {'/'.join(SESSION_KINDS)} 之一: {kind!r}")
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），kind 不可改")
    session.kind = kind
    _save_meta(session, d)
    return {"slug": slug, "kind": kind}


def add_character(
    slug: str, material_id: str, root: str | Path | None = None
) -> dict:
    """把一张人物卡挂到会话（§3.6「创作工坊会话可挂多张人物卡」）。

    校验：素材存在且是人物卡（meta.sub_type=character）；重复挂同一张 → 幂等
    （不重复入列）；封版会话拒绝。返回挂载后的卡列表。
    """
    from .. import materials_service

    card = materials_service.get_character(material_id)
    if card is None:
        raise ComposeError(
            f"素材不是人物卡或不存在: {material_id!r}（人物卡=素材 meta.sub_type=character）"
        )
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再挂人物卡")
    if material_id not in session.characters:
        session.characters.append(material_id)
        _save_meta(session, d)
    return {"slug": slug, "character": card, "characters": _session_characters(session)}


def remove_character(
    slug: str, material_id: str, root: str | Path | None = None
) -> dict:
    """从会话摘下一张人物卡（不删素材本身——素材库才是卡的家）。"""
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），人物卡不可摘")
    if material_id not in session.characters:
        raise ComposeError(f"人物卡未挂在该会话: {material_id!r}")
    session.characters = [c for c in session.characters if c != material_id]
    _save_meta(session, d)
    return {"slug": slug, "characters": _session_characters(session)}


def novel_chapter_stats(session: CompositionSession) -> dict:
    """前情提要的可视化数据面：章节数 / 已摘要章数 / 字数（右栏标题行用）。"""
    blocks = _working_blocks(session)
    text, covered, total = _prior_chapters_text(blocks, NOVEL_SUMMARY_INPUT_MAX)
    return {
        "chapters": total,
        "covered_chapters": covered if (session.novel_summary or "").strip() else 0,
        "prior_chars": len(re.sub(r"\s+", "", text or "")),
        "summary_chars": len((session.novel_summary or "").strip()),
    }


def scan_conflicts(
    session: CompositionSession, blocks: list[Block] | None = None
) -> list[dict]:
    """生成块 × 挂卡人物 → ⚠️ 冲突提示（§3.6「AI 生成后自动检查」）。

    **只产提示不改文本**（正典红线：冲突是提示不是拦截）；规则表来自
    services/novel_service（``~/.musespiral/novel_rules.json`` 可覆盖）。规则文件
    写坏（NovelServiceError）时**降级为零提示 + 原因**——生成主流程绝不因
    检测器配置错误而失败（提示面失败不反噬已完成的生成）。
    """
    from .. import novel_service  # 函数体内导入（层内解耦）

    cards = _session_characters(session)
    if not cards or not blocks:
        return []
    try:
        return novel_service.scan_conflicts(blocks, cards)
    except novel_service.NovelServiceError:
        return []
