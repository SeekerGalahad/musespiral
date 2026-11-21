"""compose 包：summary（滚动摘要 + 小说前情——两者同族）。

R-1 六簇拆分的摘要簇：「AI 生成 → 存 session → 注入 prompt 槽」的同一
机制面两份实例——rolling 记「写到哪」的近期脉络（字数跨档自动触发），
novel 记「此前各章」的情节账本（手动/换章触发）。两者各有独立插槽与
硬上限，并存互不覆盖。正稿字数口径 article_chars 也归此簇。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from ...core.composition import (
    ROLLING_SUMMARY_STEP,
    CompositionSession,
    prior_chapters_text as _prior_chapters_text,
)
from ...infra.ai import chat as _chat
from .store import (
    STATUS_COMPOSING,
    ComposeError,
    _load_session,
    _save_meta,
    _serialized,
    _working_blocks,
    _working_markdown,
)

# G-T4 滚动摘要任务模板（§五）：AI 读已写全文 → <200 字脉络。
# prompts.d/rolling_summary.txt 覆盖级联（F47 同纪律）；{{article}} 为全文插槽。
ROLLING_SUMMARY_TEMPLATE = """你是资深编辑，正在为长文作者做「到目前为止写了什么」的脉络小结，供后续续写时保持一致。

请只输出脉络摘要本身——200 字以内，不要标题、不要列表、不要解释、不要复述原文句子。
摘要须覆盖：全文主线、各节已确立的论点与结论、已出现的比喻与术语（便于前后呼应）、尚未展开的伏笔。

【已写全文】（<!--b:id--> 为块锚，可忽略）
{{article}}"""

# 摘要输出硬上限（§五「<200 字」；超长按字符截断兜底——模型不守约时不放大）
ROLLING_SUMMARY_MAX = 200

# 摘要输入预算（防巨稿撑爆上下文：取尾部优先——近期内容对续写最相关）
ROLLING_SUMMARY_INPUT_MAX = 12000

# rolling_summary 插槽标题（注入到 prompt 的节标题）
_ROLLING_HEADER = "【到目前为止的文章脉络】（滚动摘要，供保持前后一致；与当前真稿冲突时以真稿为准）"

# ── E-5 小说模式（正典 §3.6）：前情提要 + 人物卡两插槽 ─────────────────
#
# 前情提要（novel_summary）= AI 对**前 N-1 章**的摘要（<300 字，正典
# §3.6「AI 摘要前 N-1 章（<300字）」），与滚动摘要并存：rolling 记「写到
# 哪」的近期脉络、novel 记「此前各章」的情节账本。两插槽独立注入——文章
# 会话两者都空（整节缺席），小说会话两者都用。
NOVEL_SUMMARY_TEMPLATE = """你是资深小说编辑，正在为长篇连载做「前情提要」，供作者续写下一章时保持一致。

请只输出前情提要本身——300 字以内，不要标题、不要列表、不要解释、不要复述原文句子。
提要须覆盖：主线进展、已发生的关键事件与转折、人物关系变化、尚未回收的伏笔（标「伏笔」）。

【此前各章正文】（<!--b:id--> 为块锚，可忽略）
{{article}}"""

# 前情提要输出硬上限（§3.6「<300字」；超长按字符截断兜底）
NOVEL_SUMMARY_MAX = 300
# 前情提要输入预算（取尾部：越近的章节对续写越相关）
NOVEL_SUMMARY_INPUT_MAX = 12000

_NOVEL_HEADER = "【前情提要】（此前各章的情节账本，供保持连贯；与当前真稿冲突时以真稿为准）"

# ---- G-T4 滚动摘要（§五）：阈值 / 生成 / 注入 / 用户编辑 ----------------
#
# 正稿字数口径 = 块文本去空白后的字符数（与编辑器字数徽标同口径
# `EA.value.replace(/\s+/g,'').length`，前端 ed-chars 对齐）。
# 档位 = floor(字数 / 1500)：跨档才触发重算（滚动摘要_at 记已完成的档位，
# 回退到低档后再跨档会再次触发——删稿后重写也能刷新摘要）。


def article_chars(text: str) -> int:
    """正稿字数口径（去空白字符数）；空稿 0。"""
    return len(re.sub(r"\s+", "", text or ""))


def rolling_level(chars: int) -> int:
    """字数 → 已跨过的 1500 字档位（0 表示未到第一档）。"""
    return max(int(chars) // ROLLING_SUMMARY_STEP, 0)


def rolling_block(summary: str) -> str:
    """摘要 → prompt 注入节（自带节标题）；空摘要返回空串（调用方不注入）。"""
    text = (summary or "").strip()
    if not text:
        return ""
    return f"{_ROLLING_HEADER}\n{text}"


# ---- E-5 小说模式（§3.6）：前情提要 / 人物卡 的注入与生成 --------------
#
# 与 rolling 同机制面（「有内容即注入、空则整节缺席」），但触发与钩子不同：
#   - 前情提要是**手动/换章触发**（正典 §3.6 右栏「🔄 重新生成摘要」），
#     不像 rolling 那样字数跨档自动跑——小说一章写完是一次显式动作；
#   - 人物卡是**会话挂载**面（characters=素材 id 列表），生成时现取素材库
#     真值（卡改了不需要重挂）。
# 两插槽并存互不覆盖（正典 §3.6 明确要求：前情 + 滚动各记各的）。


def novel_block(summary: str) -> str:
    """前情提要 → prompt 注入节（自带节标题）；空返回空串（不注入空占位）。"""
    text = (summary or "").strip()
    if not text:
        return ""
    return f"{_NOVEL_HEADER}\n{text}"


def _novel_messages(session: CompositionSession) -> list[dict[str, str]]:
    """前情提要的 AI 输入消息：已写全文 → 只取前 N-1 章（末章=正在写）。"""
    from ...infra.ai.prompts import load_override

    blocks = _working_blocks(session)
    text, covered, total = _prior_chapters_text(blocks, NOVEL_SUMMARY_INPUT_MAX)
    if covered < 1 or not text.strip():
        raise ComposeError(
            f"尚无前情可摘要（当前共 {total} 章）——至少写完两章（第 1 章为前情、第 2 章为当前章）"
        )
    template = load_override("novel_summary").strip() or NOVEL_SUMMARY_TEMPLATE
    prompt = template.replace("{{article}}", text).strip()
    return [{"role": "user", "content": prompt}]


def _clean_novel_summary(text: str) -> str:
    """前情提要清洗：剥 ``` 围栏 → 归一空白 → 硬截断 300 字（§3.6 硬上限）。"""
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:NOVEL_SUMMARY_MAX]


@_serialized
def refresh_novel_summary(
    slug: str,
    root: str | Path | None = None,
    *,
    chat_fn=None,
    force: bool = True,
) -> dict:
    """（重）生成前情提要并落 session.json（§3.6；POST /api/compose/novel-summary）。

    - AI 输入=**前 N-1 章**正文（末章=当前正在写的一章，不进摘要）；
    - 输出 <300 字提要 → session.novel_summary + novel_summary_chapters=覆盖章数；
    - **与 rolling_summary 并存**：互不清空对方的字段（两摘要各记各的）；
    - **封版（adopted）会话拒绝**——与 rolling 不同：前情提要的唯一消费者是
      「下一章的生成」，会话封版后不再有生成，此时刷新只是白花一次 AI 调用；
      rolling 记的是「正稿已写多长」这一既成事实，故那里允许（判例分野写明）。
    - 全程持会话元数据锁（@_serialized，与 rolling 刷新同纪律：AI 往返期间的
      用户手改作为更晚的写落盘，不丢更新）；
    - chat_fn 仅供测试注入。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再生成前情提要")
    if not _working_blocks(session):
        raise ComposeError("尚无正稿块（当前真稿为空）——无可摘要内容")
    blocks = _working_blocks(session)
    _, covered, total = _prior_chapters_text(blocks, NOVEL_SUMMARY_INPUT_MAX)
    if not force and session.novel_summary and int(session.novel_summary_chapters) >= covered:
        return {
            "summary": session.novel_summary,
            "chapters": total,
            "covered_chapters": int(session.novel_summary_chapters),
            "refreshed": False,
        }
    messages = _novel_messages(session)
    content = chat_fn(messages, task="novel_summary", temperature=0.3) if chat_fn else _chat(
        messages, task="novel_summary", temperature=0.3
    )
    summary = _clean_novel_summary(content if isinstance(content, str) else "")
    if not summary:
        raise ComposeError("AI 响应为空（未收到前情提要内容）")
    session.novel_summary = summary
    session.novel_summary_chapters = covered
    _save_meta(session, d)
    return {
        "summary": summary,
        "chapters": total,
        "covered_chapters": covered,
        "refreshed": True,
    }


@_serialized
def set_novel_summary(
    slug: str, summary: str, root: str | Path | None = None
) -> dict:
    """用户手改前情提要（§3.6 右栏「✏️ 手动编辑」）——AI 的理解可能不准。

    手改**不推进** novel_summary_chapters（这一档已人工定稿，换章才重算）；
    空串=清空（后续 prompt 不再注入前情节）。300 字硬上限同刷新路径。
    """
    if not isinstance(summary, str):
        raise ComposeError("summary 必须是字符串")
    session, d = _load_session(slug, root)
    session.novel_summary = summary.strip()[:NOVEL_SUMMARY_MAX]
    _save_meta(session, d)
    return {"summary": session.novel_summary}


def rolling_due(session: CompositionSession) -> bool:
    """是否已跨到新档位（应当（重）算摘要）。

    不区分 status：触发点本身是「adopt 之后」（§五），那时会话已封版——
    摘要服务的是「正稿已写多长」这一事实，而非「还能否再生成」。
    """
    chars = article_chars(_working_markdown(session))
    return rolling_level(chars) > int(session.rolling_summary_level or 0)


def _rolling_input_text(session: CompositionSession) -> str:
    """摘要输入=已写全文（块视图→Markdown）；超预算取尾部（近期最相关）。"""
    text = _working_markdown(session)
    if len(text) > ROLLING_SUMMARY_INPUT_MAX:
        text = "…（前文过长已截去开头）\n" + text[-ROLLING_SUMMARY_INPUT_MAX:]
    return text


def _rolling_messages(session: CompositionSession) -> list[dict[str, str]]:
    from ...infra.ai.prompts import load_override

    template = load_override("rolling_summary").strip() or ROLLING_SUMMARY_TEMPLATE
    prompt = template.replace("{{article}}", _rolling_input_text(session)).strip()
    return [{"role": "user", "content": prompt}]


def _clean_summary(text: str) -> str:
    """摘要清洗：剥 ``` 围栏 → 归一空白 → 硬截断 200 字（§五 硬上限）。"""
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body[:ROLLING_SUMMARY_MAX]


@_serialized
def refresh_rolling_summary(
    slug: str,
    root: str | Path | None = None,
    *,
    chat_fn=None,
    force: bool = True,
) -> dict:
    """（重）生成滚动摘要并落 session.json（§五；POST /api/compose/rolling-summary
    手动刷新 = force=True；adopt 后异步自动触发 = maybe_refresh_after_adopt
    先判阈值再调本函数）。

    - AI 输入=已写全文（真稿；真稿空时取末轮块——见 _working_blocks）
      → chat(task="rolling_summary") → <200 字脉络；
    - 结果写 session.rolling_summary + rolling_summary_level=当前档位；
    - **封版（adopted）会话也可刷新**：触发点就在 adopt 之后（§五「触发=
      正稿字数跨阈值」），摘要服务的是「正稿已写多长」这一事实；被封版拦掉
      会让自动触发永远空转。
    - **全程持会话元数据锁（@_serialized）**：AI 调用期间同会话的其它写
      （用户手改摘要、generate、大纲写）排队等待——手改发生在刷新飞行中时，
      手改作为更晚的写落在 AI 结果之后（不丢失）；并发写不再整份覆盖互丢
      （2026-11-19 集成修复批）。
    - chat_fn 仅供测试注入（缺省走 infra.ai.chat 真实入口）。
    """
    session, d = _load_session(slug, root)
    if not _working_blocks(session):
        raise ComposeError("尚无正稿块（当前真稿为空）——无可摘要内容")
    chars = article_chars(_working_markdown(session))
    if not force and rolling_level(chars) <= int(session.rolling_summary_level or 0):
        return {
            "summary": session.rolling_summary,
            "chars": chars,
            "level": int(session.rolling_summary_level or 0),
            "refreshed": False,
        }
    messages = _rolling_messages(session)
    content = chat_fn(messages, task="rolling_summary", temperature=0.3) if chat_fn else _chat(
        messages, task="rolling_summary", temperature=0.3
    )
    summary = _clean_summary(content if isinstance(content, str) else "")
    if not summary:
        raise ComposeError("AI 响应为空（未收到摘要内容）")
    session.rolling_summary = summary
    session.rolling_summary_level = rolling_level(chars)
    _save_meta(session, d)
    return {
        "summary": summary,
        "chars": chars,
        "level": session.rolling_summary_level,
        "refreshed": True,
    }


@_serialized
def set_rolling_summary(
    slug: str, summary: str, root: str | Path | None = None
) -> dict:
    """用户手改摘要（§五「用户可在右栏查看/编辑」）——AI 的理解可能不准，人可修正。

    手改不推进档位（rolling_summary_level 保持原值）：这一档的摘要已人工定稿，
    下次跨档才会再自动重算（不覆盖人工修正）。空串=清空摘要（后续 prompt
    不再注入）。
    """
    if not isinstance(summary, str):
        raise ComposeError("summary 必须是字符串")
    session, d = _load_session(slug, root)
    session.rolling_summary = summary.strip()[:ROLLING_SUMMARY_MAX]
    _save_meta(session, d)
    return {"summary": session.rolling_summary}


def _rolling_lock_for(slug: str) -> threading.Lock:
    with _ROLLING_LOCKS_GUARD:
        lock = _ROLLING_LOCKS.get(slug)
        if lock is None:
            lock = _ROLLING_LOCKS[slug] = threading.Lock()
        return lock


def maybe_refresh_after_adopt(slug: str, root: str | Path | None = None) -> threading.Thread | None:
    """adopt 后异步检查字数跨档 → 后台线程算摘要（不阻塞主流程，§五 约束 2）。

    - 未跨档 → 返回 None（零线程零 AI 调用）；
    - 已跨档 → 起 daemon 线程跑 refresh_rolling_summary(force=True)；同会话
      的元数据写经 @_serialized 串行（并发触发排队不叠写，锁在 refresh 内部）；
    - 线程内异常一律吞掉（摘要失败绝不反噬已完成的 adopt——主流程已返回）。
    """
    try:
        session, _ = _load_session(slug, root)
    except ComposeError:
        return None
    if not rolling_due(session):
        return None

    def _run() -> None:
        try:
            refresh_rolling_summary(slug, root, force=True)
        except Exception:  # 摘要失败不反噬 adopt（后台尽力语义）
            pass

    thread = threading.Thread(target=_run, name=f"rolling-summary-{slug}", daemon=True)
    thread.start()
    return thread
