"""compose 包：foreshadowing——FEAT-4 伏笔账本（会话级 CRUD + prompt 面）。

数据面：session.foreshadowing = ``[{id, note, chapter, status}]``（status ∈
planted/escalated/payoff，core.composition.novel 三态单一持有者）。本簇管：

  - CRUD（add/update/remove）：@_serialized 串行 + 封版拒绝（与挂卡同口径）
    + 上限 FORESHADOWING_MAX（账本不是垃圾桶）；
  - prompt 注入面：未回收（status != payoff）清单 → {{foreshadowing}} 插槽
    （空账本/全部回收 → 整节缺席——同 novel/characters 的缺省纪律）；
  - 生成期检查面：open_foreshadowing 随 generate 返回（job.result.foreshadowing）
    ——**提示不拦截**（正典红线同冲突检测：账本是给作者看的，不是闸门）。
"""
from __future__ import annotations

from pathlib import Path

from ...core.composition import (
    FORESHADOWING_MAX,
    FORE_STATUS_PLANTED,
    CompositionSession,
    normalize_foreshadowing_entry,
    open_foreshadowing as _open_foreshadowing,
    render_foreshadowing as _render_foreshadowing,
    validate_foreshadowing,
)
from .store import (
    STATUS_COMPOSING,
    ComposeError,
    _load_session,
    _save_meta,
    _serialized,
)

# 伏笔 prompt 插槽标题（注入到 prompt 的节标题——build_prompt 回补时复用）
FORESHADOWING_HEADER = "【未回收伏笔】（已埋设尚未回收——写作时择机推进或回收，不要遗忘）"


def foreshadowing_block(items: list[dict]) -> str:
    """未回收伏笔 → prompt 注入节（自带节标题）；空 → 空串（整节缺席）。"""
    body = _render_foreshadowing(items)
    if not body:
        return ""
    return f"{FORESHADOWING_HEADER}\n{body}"


def open_foreshadowing(items: list[dict]) -> list[dict]:
    """未回收伏笔（status != payoff）——generate 返回与前端徽章计数的数据面。"""
    return _open_foreshadowing(items)


def _next_id(session: CompositionSession) -> str:
    """下一个伏笔 id：f1/f2… 按账本现有最大序号 +1（删了 f2 后再建不回收 id
    ——id 是稳定标识，重号比跳号危险）。"""
    max_n = 0
    for item in session.foreshadowing:
        fid = str(item.get("id") or "")
        if fid.startswith("f") and fid[1:].isdigit():
            max_n = max(max_n, int(fid[1:]))
    return f"f{max_n + 1}"


@_serialized
def add_foreshadowing(
    slug: str,
    note: str,
    chapter: str = "",
    root: str | Path | None = None,
    *,
    status: str = FORE_STATUS_PLANTED,
) -> dict:
    """埋一条伏笔（缺省 planted）→ 落 session.json foreshadowing。"""
    entry = {"note": note, "chapter": chapter, "status": status}
    errors = validate_foreshadowing([entry])
    if errors:
        raise ComposeError("; ".join(errors))
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），伏笔账本已封存")
    if len(session.foreshadowing) >= FORESHADOWING_MAX:
        raise ComposeError(f"伏笔过多（{len(session.foreshadowing)} 条，上限 {FORESHADOWING_MAX}）")
    record = normalize_foreshadowing_entry(entry, _next_id(session))
    session.foreshadowing.append(record)
    _save_meta(session, d)
    return {"slug": slug, "foreshadowing": list(session.foreshadowing), "added": record}


@_serialized
def update_foreshadowing(
    slug: str,
    fid: str,
    root: str | Path | None = None,
    *,
    status: str | None = None,
    note: str | None = None,
    chapter: str | None = None,
) -> dict:
    """改一条伏笔（状态推进 planted→escalated→payoff / 手改 note/chapter）。

    只改给出的字段；status 须在 planted/escalated/payoff 内（自由流转——
    回退 payoff→planted 也是合法编辑：作者可能觉得回收得不对）。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），伏笔账本已封存")
    target = next((x for x in session.foreshadowing if str(x.get("id")) == str(fid)), None)
    if target is None:
        pool = "、".join(str(x.get("id")) for x in session.foreshadowing) or "（空）"
        raise ComposeError(f"伏笔不存在: {fid!r}（账本: {pool}）")
    merged = {
        "id": target.get("id"),
        "note": target.get("note") if note is None else note,
        "chapter": target.get("chapter") if chapter is None else chapter,
        "status": target.get("status") if status is None else status,
    }
    errors = validate_foreshadowing([merged])
    if errors:
        raise ComposeError("; ".join(errors))
    target.clear()
    target.update(normalize_foreshadowing_entry(merged, str(fid)))
    _save_meta(session, d)
    return {"slug": slug, "foreshadowing": list(session.foreshadowing)}


@_serialized
def remove_foreshadowing(
    slug: str, fid: str, root: str | Path | None = None
) -> dict:
    """删一条伏笔（误录清理；回收完成走 status=payoff 而不是删——账本留痕）。"""
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），伏笔账本已封存")
    before = len(session.foreshadowing)
    session.foreshadowing = [
        x for x in session.foreshadowing if str(x.get("id")) != str(fid)
    ]
    if len(session.foreshadowing) == before:
        pool = "、".join(str(x.get("id")) for x in session.foreshadowing) or "（空）"
        raise ComposeError(f"伏笔不存在: {fid!r}（账本: {pool}）")
    _save_meta(session, d)
    return {"slug": slug, "foreshadowing": list(session.foreshadowing)}
