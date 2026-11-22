"""compose 包：generate——生成 / 三态裁决 / 双模型对比（R-1 六簇之一）。

generate/blocks_so_far/decide/adopt 与 G-T3 双模型对比三件套。
"""
from __future__ import annotations

import json
from pathlib import Path

from ...core.composition import (STATUS_ADOPTED, STATUS_COMPOSING, STATUS_EXPANDED,
                                 Block, Round, markdown_from_blocks, match_round,
                                 parse_ai_output,
                                 apply_decisions as _apply_decisions,
                                 habits_pairs as _habits_pairs,
                                 validate_decision as _validate_decision)
from ...core.habits import extract_habits, save_habits
from ...infra.ai import chat as _chat
from ...infra.ai import chat_stream as _chat_stream
from ...infra.ai import load_ai_config
from ...infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ...infra.storage.filesystem import backup_article as _backup_article
from ...jobs.store import now_iso as _now_iso
from .characters import scan_conflicts
from .foreshadowing import open_foreshadowing
from .outline import _load_outline
from .prompt import build_messages
from .store import (ComposeError, DECISIONS_SUFFIX, FEEDBACK_FILE, _draft_path,
                    _load_decisions, _load_session, _rounds_dir, _save_meta,
                    _serialized, _write_json_atomic, sessions_root)
from .summary import maybe_refresh_after_adopt

# ---- generate / decide / adopt ----


@_serialized
def generate(
    slug: str,
    root: str | Path | None = None,
    *,
    job_id: str | None = None,
    chat_fn=None,
    on_delta=None,
) -> dict:
    """按反馈再生成一轮（§2.3 编排动作；Studio 走 job compose_generate）。

    产出链：chat(task="compose") → rounds/N.md 即时落盘（存证+habits 原料，
    生成中断不丢已产出）→ 定界解析 → 无锚回退匹配（对上轮块；首轮对
    draft——支持对人工预置稿的迭代）→ 轮次块记录入 session.json。

    chat_fn 仅供测试注入（缺省走 infra.ai.chat 真实入口）。on_delta（E-4
    流式骨架批，2026-11-19）：真流式路径的增量回调——chat_fn 未注入且
    on_delta 给出时走 infra.ai.chat_stream（同请求体同错误面，SSE delta
    到达即回调），供 Studio job 在生成期发布 partial 块；两者都缺省时
    行为与本批之前逐字节一致。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再生成")
    messages = build_messages(session)
    # job_id 仅为 Studio job 闭包的接缝参数（服务层无 runner 依赖，不消费）
    if chat_fn is not None:
        content = chat_fn(messages, task="compose", temperature=0.6)
    elif on_delta is not None:
        content = _chat_stream(
            messages, task="compose", temperature=0.6, on_delta=on_delta)
    else:
        content = _chat(messages, task="compose", temperature=0.6)
    if not isinstance(content, str) or not content.strip():
        raise ComposeError("AI 响应为空（未收到内容）")

    n = (session.rounds[-1].n if session.rounds else 0) + 1
    rounds_dir = _rounds_dir(d)
    rounds_dir.mkdir(parents=True, exist_ok=True)
    (rounds_dir / f"{n}.md").write_text(content, encoding="utf-8")  # 即时落盘

    previous = session.rounds[-1].blocks if session.rounds else session.draft
    blocks = match_round(parse_ai_output(content), previous, session.taken_ids())
    session.rounds.append(Round(n=n, blocks=blocks))
    _save_meta(session, d)
    _write_json_atomic(rounds_dir / f"{n}{DECISIONS_SUFFIX}", [])
    return {
        "round": n,
        "blocks": [b.to_dict() for b in blocks],
        "raw": str(rounds_dir / f"{n}.md"),
        # E-5（§3.6「AI 生成后自动检查」）：冲突=⚠️ 提示列表，**不拦截不改写**
        "conflicts": scan_conflicts(session, blocks),
        # FEAT-4 伏笔检查：未回收（planted/escalated）清单随结果回传——提示
        # 不拦截（同 conflicts 红线：账本是给作者看的，不拦生成不改文本）
        "foreshadowing": open_foreshadowing(session.foreshadowing),
    }


def blocks_so_far(text: str) -> list[dict]:
    """生成期的「骨架已到货」视图（E-4 流式骨架批）：对累积中的 AI 原文跑
    同一定界解析，返回 ``[{kind, text}]``（display 面——无 id 无归并，id 与
    无锚匹配只在终态 generate 里做）。

    部分文本的尾块随增量增长（parse_ai_output 对未闭合末段容错收编），
    调用方（Studio job 的 partial 发布）重绘即可得到「上一块定格、尾块打字」
    的流式观感；空文本返回空列表。
    """
    return [{"kind": b.kind, "text": b.text} for b in parse_ai_output(text or "")]


# ---- G-T3 双模型对比（设计 §四）：同 prompt × 双档案并行 → 选优进正常裁决流 ---
#
# 对比是**秒级探索**：同步端点、零 job、零落盘（不写 rounds/N.md）——两版原文
# 只在响应里回给前端，用户点「选 A/B」才经 apply_dual_pick 落进正常块裁决流；
# 「保留双版」经 keep_both_drafts 存两份独立草稿。两路 chat 走 chat_policed
# （task=compose，profile 各异），F79 usage 自动记账，prompt 与 generate 完全
# 同源（build_messages——素材/真稿/裁决/滚动摘要同一份，只有档案不同）。

DUAL_DRAFT_A = "draft_v_a.md"
DUAL_DRAFT_B = "draft_v_b.md"


def dual_compose(
    slug: str,
    profile_a: str,
    profile_b: str,
    root: str | Path | None = None,
    *,
    chat_fn=None,
) -> dict:
    """同 prompt 双档案并行对比 → 两版正文 + 各自用量（§四；同步零 job）。

    `chat_fn` 仅供测试注入（签名=infra.ai.client.chat；经 chat_policed 的
    chat_fn 参落下）。两路共享同一 messages 实例，只有 profile 不同——这是
    「供应商可转换的科学验证」的前提：prompt 必须逐字一致。

    并发用 ThreadPoolExecutor(max_workers=2)——两个 chat 真并行（本地网关
    T+7 类慢响应时对比耗时=max(A,B) 而非 sum）；Studio 是 ThreadingHTTPServer，
    阻塞本请求不阻塞其他请求。任一版空响应 → ComposeError（可读，不把半成品
    交给前端选）。会话已采纳（adopted）→ 拒绝（与 generate 同口径）。
    """
    from concurrent.futures import ThreadPoolExecutor

    from ...infra.ai.gateway import chat_policed

    session, _d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能对比生成")
    messages = build_messages(session)

    def _one(profile: str) -> dict:
        return chat_policed(
            messages,
            task="compose",
            temperature=0.6,
            profile=profile,
            cache=False,  # 生成类恒不缓存（F79 §八-3）
            chat_fn=chat_fn,
        )

    # 并发语义由 `tests/test_compose_dual.py::test_dual_compose_is_really_parallel`
    # （barrier 探针）看守——改回串行 for 循环该用例即红，勿只改这里。
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_one, profile_a)
        future_b = pool.submit(_one, profile_b)
        res_a, res_b = future_a.result(), future_b.result()

    text_a = str(res_a.get("text") or "").strip()
    text_b = str(res_b.get("text") or "").strip()
    if not text_a or not text_b:
        raise ComposeError("AI 返回空稿（双模型对比需要两版都有内容）")

    def _usage(profile: str, res: dict) -> dict:
        return {
            "profile": profile,
            "chars_in": int(res.get("chars_in") or 0),
            "chars_out": int(res.get("chars_out") or 0),
            "cache_hit": bool(res.get("cache_hit")),
        }

    return {
        "ok": True,
        "ok_a": True,
        "ok_b": True,
        "text_a": text_a,
        "text_b": text_b,
        "usage_a": _usage(profile_a, res_a),
        "usage_b": _usage(profile_b, res_b),
    }


@_serialized
def apply_dual_pick(
    slug: str, text: str, root: str | Path | None = None, *, pick: str = "a"
) -> dict:
    """选 A/B → 选定稿替换 session 最新 round 的 blocks（§四「进正常裁决流」）。

    复用 generate 的同一定界解析与无锚匹配（parse_ai_output + match_round），
    以「上一轮（首轮=draft）」为基线保留稳定块 id——用户随后在创作工坊对
    这些块做 keep/adopt_edited/discard 三态裁决，与正常生成毫无二致。
    该轮 decisions 清空（旧裁决针对的是被替换掉的稿，不能沿用）。

    无轮次时新建第 1 轮（对比可直接作为首稿入口，不必先 generate）。原始
    rounds/N.md 同步覆写为选定稿——存证与块视图保持一致。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再裁决")
    if not isinstance(text, str) or not text.strip():
        raise ComposeError("选定稿不能为空")
    parsed = parse_ai_output(text)
    if not parsed:
        raise ComposeError("选定稿无有效块（定界符后正文为空）")

    rounds_dir = _rounds_dir(d)
    rounds_dir.mkdir(parents=True, exist_ok=True)
    if session.rounds:
        target = session.rounds[-1]
        pos = len(session.rounds) - 1
        previous = session.rounds[pos - 1].blocks if pos > 0 else session.draft
        # taken 排除被替换轮自身——否则它的 id 会被 match_round 当作已占用，
        # 同文块只能另起新 id（无谓打断跨轮裁决历史）。
        taken = {b.id for b in session.draft}
        for r in session.rounds:
            if r is not target:
                taken.update(b.id for b in r.blocks)
    else:
        target = Round(n=1)
        previous = session.draft
        taken = session.taken_ids()
        session.rounds.append(target)

    target.blocks = match_round(parsed, previous, taken)
    target.decisions = []
    (rounds_dir / f"{target.n}.md").write_text(text, encoding="utf-8")
    _write_json_atomic(rounds_dir / f"{target.n}{DECISIONS_SUFFIX}", [])
    _save_meta(session, d)
    return {
        "ok": True,
        "slug": slug,
        "round": target.n,
        "pick": pick,
        "blocks": [b.to_dict() for b in target.blocks],
    }


@_serialized
def keep_both_drafts(
    slug: str,
    text_a: str,
    text_b: str,
    root: str | Path | None = None,
    *,
    profile_a: str = "",
    profile_b: str = "",
) -> dict:
    """保留双版 → 各存为独立正稿草稿（§四）。

    落在会话目录 ``<slug>.compose/draft_v_{a,b}.md``（与 draft.md 同处，
    自包含、不进文章队列——扫描器已排除 .compose 子目录）。带 frontmatter
    （title/status/ai_model/profile），用户可自行移出会话目录发布。
    """
    import frontmatter

    session, d = _load_session(slug, root)
    out: list[Path] = []
    for name, text, profile in (
        (DUAL_DRAFT_A, text_a, profile_a),
        (DUAL_DRAFT_B, text_b, profile_b),
    ):
        if not isinstance(text, str) or not text.strip():
            raise ComposeError(f"{name} 正文不能为空")
        post = frontmatter.Post(
            text.strip(),
            title=session.title or slug,
            status="draft",
            ai_model=profile or None,
        )
        path = d / name
        _atomic_write(path, frontmatter.dumps(post, allow_unicode=True, sort_keys=False).encode("utf-8"))
        out.append(path)
    return {"ok": True, "slug": slug, "drafts": [str(p) for p in out]}


def decide(
    slug: str,
    round_n: int,
    block_id: str,
    action: str,
    *,
    text: str | None = None,
    direction: str | None = None,
    root: str | Path | None = None,
) -> dict:
    """逐块三态裁决：校验 → N.decisions.json 更新 + feedback.jsonl 追加。"""
    session, d = _load_session(slug, root)
    target = next((r for r in session.rounds if r.n == round_n), None)
    if target is None:
        raise ComposeError(f"轮次不存在: 第 {round_n} 轮（会话共 {len(session.rounds)} 轮）")
    if not any(b.id == block_id for b in target.blocks):
        known = "、".join(b.id for b in target.blocks)
        raise ComposeError(f"块不存在于第 {round_n} 轮: {block_id}（该轮块: {known}）")
    decision = _validate_decision(action, text, direction)
    record = {"block": block_id, **decision, "ts": _now_iso()}

    # 同块改主意：末次胜出——落盘记录去重保留原序+新记录
    path = _rounds_dir(d) / f"{round_n}{DECISIONS_SUFFIX}"
    kept = [x for x in _load_decisions(d, round_n) if x.get("block") != block_id]
    decisions = kept + [record]
    _write_json_atomic(path, decisions)

    line = json.dumps(
        {"ts": record["ts"], "session": slug, "round": round_n, **record},
        ensure_ascii=False,
    )
    with (d / FEEDBACK_FILE).open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    target.decisions = decisions
    return record


@_serialized
def adopt(slug: str, root: str | Path | None = None) -> dict:
    """采纳入稿（§2.4/§2.6——块级人工最后一步）。

    本轮 decisions → merged → draft.md 原子写（旧稿先入 F43 版本池）→
    habits learn（adopt_edited 差分对）→ 正稿 <slug>.md 入队列（frontmatter
    带 source=主源（首素材）+source_extra=协作者 + ai_model）。会话终态
    adopted（正稿已存在时拒绝重复采纳）。
    """
    import frontmatter

    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能重复采纳")
    if not session.rounds:
        raise ComposeError("尚无生成轮次，无稿可采纳（先 generate）")
    final = sessions_root(root) / f"{slug}.md"
    if final.exists():
        raise ComposeError(f"正稿已存在（拒绝覆盖）: {final.name}")
    latest = session.rounds[-1]
    # 合并口径按会话形态分流（2026-11-19 集成修复判例——大纲多节采纳丢前序
    # 各节）：
    # - 大纲模式（outline.json 存在 expanded 节）：逐节展开的每一轮都是
    #   「增量节内容」（正典 §三流程：每节独立请求进块协议），采纳必须按
    #   轮序合并**全部轮次**——只取末轮会把前序各节全部丢出正稿；
    # - 普通 compose 模式：每轮 generate 都是对全文的整稿重写，保持原
    #   「末轮 + 末轮裁决」语义不变。
    # 块级人工红线两种口径一致：未裁决块不入稿（apply_decisions），大纲
    # 模式且任一轮整轮未裁决 → 可读拒绝（比静默丢节诚实）。
    expanded_outline = any(s.status == STATUS_EXPANDED for s in _load_outline(d)[0])
    if expanded_outline:
        for r_ in session.rounds:
            if not r_.decisions:
                raise ComposeError(
                    f"第 {r_.n} 轮尚无任何裁决（先 decide 再采纳；大纲模式采纳会合并全部轮次）"
                )
        merged: list[Block] = []
        for r_ in session.rounds:
            merged.extend(_apply_decisions(r_.blocks, r_.decisions))
    else:
        if not latest.decisions:
            raise ComposeError(f"第 {latest.n} 轮尚无任何裁决（先 decide 再采纳）")
        merged = _apply_decisions(latest.blocks, latest.decisions)

    draft_path = _draft_path(d)
    if draft_path.exists() and draft_path.read_bytes():
        _backup_article(draft_path)  # F43：adopt 前版本池（撤销可撤销）
    _atomic_write(draft_path, markdown_from_blocks(merged).encode("utf-8"))

    # habits 飞轮：全部轮次的 adopt_edited 差分 → 训练对 → learn（只存差分
    # 不改行为）。逐轮收集——每轮「AI 稿 vs 人工裁决稿」差分都是燃料（§〇），
    # 不只看末轮（末轮 keep 的块可能源自早轮的人工改稿）。
    from ...core.habits import Habit  # 局部导入：仅此处在服务层拼训练对

    found: list[Habit] = []
    for r in session.rounds:
        for ai_text, human_text in _habits_pairs(r.blocks, r.decisions):
            found.extend(extract_habits(ai_text, human_text))
    if found:
        save_habits(found)
    learned = len(found)

    session.draft = merged
    session.status = STATUS_ADOPTED
    _save_meta(session, d)

    base = sessions_root(root)
    post = frontmatter.Post(
        markdown_from_blocks(merged).strip(),
        title=session.title or slug,
        status="draft",
        ai_model=load_ai_config("compose").get("model", ""),
    )
    if session.sources:  # §2.6 归因：首素材=主源，其余=协作者
        post.metadata["source"] = session.sources[0].path
        if len(session.sources) > 1:
            post.metadata["source_extra"] = [s.path for s in session.sources[1:]]
    final.write_text(
        frontmatter.dumps(post, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    # G-T4 滚动摘要（§五 约束 2）：入稿**后**异步判档——正稿字数跨 1500 字
    # 阈值才起后台线程重算；不阻塞本函数返回，摘要失败也不反噬已完成的采纳。
    maybe_refresh_after_adopt(slug, root)
    return {
        "article": str(final),
        "merged_blocks": len(merged),
        "habits_learned": learned,
        "round": latest.n,
    }
