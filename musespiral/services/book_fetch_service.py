"""书籍取文与提炼管线服务（R3 接驳 + R3.5 提炼，L3）——设计正典
《musespiral-书籍内容提炼管线设计-2026-11-18.md》§2.1 管线全景的编排落点。

两条 job 线（KINDS 15→17）：

  - `book_search`：`POST /api/book/search` 引擎代理聚合搜索（submit_search）；
  - `book_fetch`：`POST /api/materials/from-chapters` 取文入素材库
    （submit_to_runner——**提交期只校验零落盘**，正文/缓存/素材全部由
    job 运行期写入，preview 零落盘纪律同 ingest）。

book_fetch 全链（run_fetch，人工「入素材库」动作=决策点，设计 §三）：

  ① 逐章引擎取文（client.content）→ `clean_chapter` 清洗 → 落章节缓存
     `~/.musespiral/book_cache/<bookHash>/<idx>-<title>.txt`（原始全文 immutable
     不进 materials 大字段——「按需取」的可核验性面）；
  ② digest=true 时 `build_book_digest` plan + interp 执行（**零新编排代码**，
     Phase D 引擎免费给步级进度/预算对账/缓存/失败存证）；步级明细经
     `open_attached_run`/`_finish_progress_run` 挂 `agent_runs/<job_id>`
     （GET /api/agent/runs?id=<job_id> 可查——前端步级双源的第二源）；
     AI 失败章**降级原始文本入库**（meta.book.distilled=false——降级优先，
     无 AI 金路径不断）；AI 未配置→全章降级；预算触顶→剩余章全降级
     （内容不丢优先于提炼完成）；
  ③ 综述步：≥5 章有提炼卡时补跑（digest 步 cache=true 全命中零成本，仅
     综述新出账——「失败续跑后补综述」的免费实现）；
  ④ 素材落库：提炼卡=正文（license=AI-GENERATED + meta.ai_generated）/
     降级章=清洗后原文（license=UNKNOWN）；meta.book 全字段 + meta.original_ref
     + chain=[{from_id:bookSourceUrl, op:book_fetch}] + **triage.enabled=false
     初始**（批量动作=决策点、库内治理=质量闸——设计 §三裁定的落点）。

部分成功是合法终态（§六验收 3）：取文失败章/降级章在 result 里明列
（M 完成/N 失败），不静默丢。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import config
from ..core.agent.interp import run_plan
from ..core.agent.plans_builtin import (
    BOOK_DIGEST_MAX_CHAPTERS,
    BOOK_SYNTHESIS_MIN_CHAPTERS,
    PLAN_BOOK_DIGEST,
    build_book_digest,
)
from ..core.booksource.clean import clean_chapter
from ..core.materials import model
from ..infra.ai import TASKS, ai_configured
from ..infra.ai.gateway import chat_policed
from ..infra.bookengine import BookEngineClient, BookEngineUnavailable
from ..infra.engine_select import get_engine_client
from ..infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ..jobs.runner import JobFailure
from ..jobs.store import now_iso as _now_iso
from . import materials_service
from .agent_service import apply_prompt_overrides, finish_attached_run, open_attached_run, step_templates

# 章节缓存根（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移）
BOOK_CACHE_DIR = "book_cache"
# 搜索结果进 job result 的条数上限（result 持久化，防大结果刷爆 jobs.jsonl）
SEARCH_ITEMS_CAP = 50
# 「AI 失败续跑」的连续降级守门：连续 N 章因 AI 失败降级即停（全降级剩余），
# 防止 AI 服务整体宕机时逐章空跑 N 轮计划
MAX_CONSECUTIVE_AI_DEGRADES = 3
# 单章标题进文件名的安全化上限
_TITLE_MAX = 40

ChatFn = Callable[..., dict[str, Any]]

# 素材虚拟源标记（G1 的 "ai_gen"/synthesis 的 "synthesis" 同款：book 域不进
# sources.json 注册表，uri 承载全部身份）；uri scheme 同为幂等锚——同书同
# 章恒同 uri → 稳定素材 id
BOOK_SOURCE_ID = "book"
BOOK_URI_SCHEME = "book"


class BookFetchError(ValueError):
    """提交期校验失败（构造期即抛——零 job；studio 端点转 200 {ok:false}）。"""


# ── 形状与路径 ─────────────────────────────────────────────────────


def _book_hash(source_url: str, book_url: str) -> str:
    """书身份散列：sha1(sourceUrl|bookUrl) 前 16 位（缓存目录名）。"""
    return hashlib.sha1(f"{source_url}|{book_url}".encode("utf-8")).hexdigest()[:16]


_SAFE_TITLE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def _safe_title(title: str) -> str:
    """标题文件名安全化：非法字符折叠为 _、截断 40、空兜底 untitled。"""
    safe = _SAFE_TITLE_RE.sub("_", title.strip())[:_TITLE_MAX].strip("._") or "untitled"
    return safe


def chapter_cache_rel(book_hash: str, index: int, title: str) -> str:
    """章节缓存相对路径（original_ref.path 的存值；设计 §2.1 ⑤）。"""
    return f"{book_hash}/{index}-{_safe_title(title)}.txt"


def cache_root() -> Path:
    """章节缓存根目录（~/.musespiral/book_cache；随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / BOOK_CACHE_DIR


def resolve_original_ref(ref: str) -> Path:
    """original_ref → book_cache 内绝对路径（**三道门**：相对路径 + 越界拒
    + .txt 白名单——materials file 纯文本版；GET /api/book/original 的咽喉层）。

    非法/越界/非 txt → ValueError（可读）；不校验存在性（404 由调用方区分）。
    """
    raw = (ref or "").strip()
    if not raw:
        raise ValueError("ref 不能为空（章节缓存相对路径）")
    if "\x00" in raw:
        raise ValueError("ref 含非法字符（NUL）")
    if Path(raw).is_absolute():
        raise ValueError(f"只接受 book_cache 内相对路径（禁止绝对路径）: {raw}")
    root = cache_root().resolve()
    try:
        resolved = (root / raw).resolve()
    except (OSError, ValueError):
        raise ValueError(f"ref 路径非法: {raw}") from None
    if not resolved.is_relative_to(root):
        raise ValueError(f"ref 越界（必须仍在 book_cache 内）: {raw}")
    if resolved.suffix != ".txt":
        raise ValueError(f"只接受 .txt 章节缓存（白名单）: {raw}")
    return resolved


# ── 提交期形状校验（零 job 零落盘）─────────────────────────────────


def _check_chapters_input(chapters: Sequence[dict[str, Any]], *, digest: bool) -> list[dict[str, Any]]:
    """入参章节形状校验：[{url:str, index:int, title:str}]，1..上限。"""
    items = list(chapters)
    if not items:
        raise BookFetchError("chapters 不能为空（至少选择一章）")
    cap = BOOK_DIGEST_MAX_CHAPTERS if digest else 2 * BOOK_DIGEST_MAX_CHAPTERS
    if len(items) > cap:
        raise BookFetchError(
            f"章节数超上限：{len(items)} 章（提炼模式 ≤{BOOK_DIGEST_MAX_CHAPTERS}、"
            f"原文直入 ≤{cap}；更多章请分批取文）"
        )
    seen: set[int] = set()
    for i, ch in enumerate(items):
        if not isinstance(ch, dict):
            raise BookFetchError(f"chapters[{i}] 必须是对象（含 url/index/title）")
        for key in ("url", "title"):
            value = ch.get(key)
            if not isinstance(value, str) or not value.strip():
                raise BookFetchError(f"chapters[{i}].{key} 必须是非空字符串")
        index = ch.get("index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise BookFetchError(f"chapters[{i}].index 必须是 ≥0 的整数，实际: {index!r}")
        if index in seen:
            raise BookFetchError(f"chapters[{i}].index 重复: {index}")
        seen.add(index)
    return items


def _check_book_meta(book: Any) -> dict[str, str]:
    """book 元数据归一：{name, author}（name 必填非空；author 宽容空串）。"""
    if not isinstance(book, dict):
        raise BookFetchError("book 必须是对象（含 name/author）")
    name = book.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BookFetchError("book.name 必须是非空字符串（书名）")
    author = book.get("author")
    if author is not None and not isinstance(author, str):
        raise BookFetchError("book.author 必须是字符串或省略")
    return {"name": name.strip(), "author": (author or "").strip()}


def _source_name(source_url: str) -> str:
    """书源显示名：规范 JSON / 索引投影按 url 查（缺失空串——展示位非闸门）。"""
    from ..infra import booksource_store

    source = booksource_store.load_source(source_url)
    if source is not None:
        name = source.get("bookSourceName")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for row in booksource_store.load_index():
        if isinstance(row, dict) and row.get("bookSourceUrl") == source_url:
            name = row.get("bookSourceName")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return ""


# ── 素材构造（唯一写面经 materials_service.add_material）──────────


def _make_chapter_material(
    *,
    source_url: str,
    book_hash: str,
    book_meta: dict[str, str],
    index: int,
    title: str,
    body: str,
    distilled: bool,
    cache_rel_path: str,
    original_chars: int,
    fetched_at: str,
) -> dict[str, Any]:
    """一章一条素材（分章默认形态——R4 正典 §4.3）。

    distilled=true：正文=提炼卡，license=AI-GENERATED + meta.ai_generated
    （G1 裁决 4 双保险）；false：正文=清洗后原文，license=UNKNOWN（第三方
    站点内容，用户自担）。两形态恒带 meta.book 全字段 + meta.original_ref
    + 单条 book_fetch 链（validate_material 豁免条二）+ enabled=false 初始。
    """
    meta: dict[str, Any] = {
        "book": {
            "name": book_meta["name"],
            "author": book_meta["author"],
            "source_name": book_meta["source_name"],
            "chapter_index": index,
            "chapter_title": title,
            "original_chars": original_chars,
            "distilled": distilled,
        },
        "original_ref": {"path": cache_rel_path, "chars": original_chars},
    }
    license_value = "AI-GENERATED" if distilled else "UNKNOWN"
    if distilled:
        meta["ai_generated"] = True
    return model.make_material(
        mtype="text",
        source_id=BOOK_SOURCE_ID,
        uri=f"{BOOK_URI_SCHEME}://{book_hash}/{index}",
        title=f"《{book_meta['name']}》第 {index} 章 {title}".strip(),
        license=license_value,
        payload={"text": body, "meta": meta},
        provenance={"chain": [{"from_id": source_url, "op": "book_fetch"}], "derived_from": []},
        triage={"read": False, "starred": False, "enabled": False, "tags": []},
        fetched_at=fetched_at,
    )


def _make_synthesis_material(
    *,
    source_url: str,
    book_hash: str,
    book_meta: dict[str, str],
    chapter_count: int,
    body: str,
    fetched_at: str,
) -> dict[str, Any]:
    """全书综述素材（一条；meta.book.synthesis=true 标记综述态）。"""
    return model.make_material(
        mtype="text",
        source_id=BOOK_SOURCE_ID,
        uri=f"{BOOK_URI_SCHEME}://{book_hash}/synthesis",
        title=f"《{book_meta['name']}》全书综述",
        license="AI-GENERATED",
        payload={
            "text": body,
            "meta": {
                "book": {
                    "name": book_meta["name"],
                    "author": book_meta["author"],
                    "source_name": book_meta["source_name"],
                    "synthesis": True,
                    "chapters": chapter_count,
                },
                "ai_generated": True,
            },
        },
        provenance={"chain": [{"from_id": source_url, "op": "book_fetch"}], "derived_from": []},
        triage={"read": False, "starred": False, "enabled": False, "tags": []},
        fetched_at=fetched_at,
    )


def _adopt(row: dict[str, Any]) -> tuple[str, str]:
    """落一条素材 → (material_id, outcome)；outcome ∈ {added, conflict}。

    **immutable 冲突**（同 id 异内容，如换源后重采同章 / 上轮已降级本轮提炼
    成功）按「已存在跳过」记录，既有行原样保留（用户分诊态与既有正文优先；
    「重新提炼」替换正文的窄门属 R4 批次，不在本批）。真·非法行（validate
    Material 失败）不吞——原样抛出（脏数据不能静默丢）。
    """
    try:
        saved = materials_service.add_material(row)
    except ValueError as exc:
        if "immutable" in str(exc):
            return row["id"], "conflict"
        raise
    return saved["id"], "added"


# ── 提炼执行（interp 复用；AI 失败逐章降级）─────────────────────────


def _chat_step(
    job_id: str, templates: dict[str, str] | None, chat_fn: ChatFn | None
) -> Callable[..., dict[str, Any]]:
    """interp 的 chat_fn 闭包：gateway 治理（cache/budget/usage）+ run_id 对账。"""

    def _step(prompt: str, *, task: str, cache: bool) -> dict[str, Any]:
        return chat_policed(
            [{"role": "user", "content": prompt}],
            task=task,
            cache=cache,
            run_id=job_id,
            template=templates.get(task) if templates is not None else None,
            chat_fn=chat_fn,
        )

    return _step


def _chapter_prompt(index: int, title: str, cleaned: str) -> str:
    """ctx.materials[i] 的形状：章节头一行（模型知道「这是哪一章」）+ 清洗后全文。"""
    return f"【章节】第 {index} 章 {title}\n{cleaned}"


def _run_digest_pass(
    pending: list[dict[str, Any]],
    *,
    job_id: str,
    chat_fn: ChatFn | None,
    synthesis: bool = False,
) -> dict[str, Any]:
    """对 pending 章节跑一趟 plan（digest 步 + 可选综述步）→ interp RunResult。

    plan 经 apply_prompt_overrides（prompts.d/chapter_digest.txt 等覆盖级联
    「自动继承」的落点——interp 是 L2 不能 import infra，级联在服务层组装）。
    """
    plan = build_book_digest([{"url": f["url"], "title": f["title"]} for f in pending], synthesis=synthesis)
    plan = apply_prompt_overrides(plan)
    ctx = {
        "materials": [
            _chapter_prompt(f["index"], f["title"], f["cleaned"]) for f in pending
        ]
    }
    return run_plan(plan, ctx, _chat_step(job_id, step_templates(plan), chat_fn), tasks=TASKS)


def _digest_loop(
    fetched: list[dict[str, Any]],
    *,
    job_id: str,
    chat_fn: ChatFn | None,
    on_phase: Callable[[str], None] | None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[dict[int, str], list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """失败续跑循环：跑 plan→已完成章收卡→首个失败章降级→剩余章重跑。

    `should_stop`（取消检查点）：**pass 界生效**（pass 内 plan 是原子执行单元，
    不可中断）——观察到取消即在下一 pass 前收束，剩余章**放弃不降级**（取消
    ≠AI 失败：降级是 AI 故障的兜底，放弃是用户决策，两条路径不混）。

    返回 (cards: {index: 提炼卡}, degraded: [章行含 reason], synthesis_text,
    progress_steps: 跨 pass 累积的步级计数)。步 id 用**真实章节 index**
    （digest_<index>）而非 pass 内序号——多 pass 重跑不撞名，前端步级行
    「第 N 章提炼」即真实章号（run 面经 finish_attached_run 挂 agent_runs/
    <job_id>，GET /api/agent/runs?id=<job_id> 的第二进度源）。
    预算触顶 / 连续 MAX_CONSECUTIVE_AI_DEGRADES 章失败 → 剩余全降级（AI 整体
    不可用时不再逐章空跑）。每轮循环消化 ≥1 章，循环次数 ≤ 章数。
    """
    cards: dict[int, str] = {}
    degraded: list[dict[str, Any]] = []
    progress_steps: list[dict[str, Any]] = []
    pending = list(fetched)
    consecutive = 0
    while pending:
        if should_stop is not None and should_stop():
            break  # 取消：剩余章放弃（不降级不落库）
        if on_phase is not None:
            on_phase(f"提炼中 {len(cards)}/{len(fetched)}")
        result = _run_digest_pass(pending, job_id=job_id, chat_fn=chat_fn)
        slots = result.get("slots") or {}
        steps_meta = result.get("steps") or []
        remaining: list[dict[str, Any]] = []
        for i, chapter in enumerate(pending, 1):
            card = slots.get(f"digest_{i}")
            if isinstance(card, str) and card.strip():
                cards[chapter["index"]] = card
                meta_i = steps_meta[i - 1] if i - 1 < len(steps_meta) else {}
                progress_steps.append({
                    "id": f"digest_{chapter['index']}",
                    "task": "chapter_digest",
                    "status": "done",
                    "chars_in": int(meta_i.get("chars_in") or 0),
                    "chars_out": int(meta_i.get("chars_out") or len(card)),
                    "cache_hit": bool(meta_i.get("cache_hit")),
                    "attempts": int(meta_i.get("attempts") or 1),
                })
            else:
                remaining.append(chapter)
        error_text = str(result.get("error") or "")
        if result.get("status") == "done":
            break  # 全部完成（remaining 必空）
        if not remaining:
            break  # 防御：状态非 done 但无缺口（不该发生，不炸）
        if should_stop is not None and should_stop():
            break  # 取消：剩余章放弃（不降级不落库）——降级是 AI 故障兜底，取消是用户决策
        if "预算触顶" in error_text:
            for chapter in remaining:
                degraded.append({**chapter, "reason": "AI 预算触顶，降级原文"})
            break
        consecutive += 1
        first, rest = remaining[0], remaining[1:]
        degraded.append({**first, "reason": "AI 提炼失败，降级原文"})
        if consecutive >= MAX_CONSECUTIVE_AI_DEGRADES and rest:
            for chapter in rest:
                degraded.append({**chapter, "reason": "AI 连续失败，剩余章降级原文"})
            break
        pending = rest
    # 综述补跑：≥5 张卡时对已成功章重建 plan（digest 步 cache=true 全命中，
    # 仅综述步新出账）
    synthesis_text: str | None = None
    distilled_chapters = [f for f in fetched if f["index"] in cards]
    if (
        len(distilled_chapters) >= BOOK_SYNTHESIS_MIN_CHAPTERS
        and ai_configured("book_digest")
        and not (should_stop is not None and should_stop())
    ):
        if on_phase is not None:
            on_phase("全书综述")
        result = _run_digest_pass(
            distilled_chapters, job_id=job_id, chat_fn=chat_fn, synthesis=True
        )
        text = (result.get("slots") or {}).get("synthesis")
        if result.get("status") == "done" and isinstance(text, str) and text.strip():
            synthesis_text = text
            synth_meta = next(
                (s for s in (result.get("steps") or []) if s.get("id") == "book_synthesis"),
                {},
            )
            progress_steps.append({
                "id": "book_synthesis",
                "task": "book_digest",
                "status": "done",
                "chars_in": int(synth_meta.get("chars_in") or 0),
                "chars_out": int(synth_meta.get("chars_out") or len(text)),
                "cache_hit": bool(synth_meta.get("cache_hit")),
                "attempts": int(synth_meta.get("attempts") or 1),
            })
    return cards, degraded, synthesis_text, progress_steps


def _finish_progress_run(
    job_id: str,
    cards: dict[int, str],
    synthesis_text: str | None,
    progress_steps: list[dict[str, Any]],
    *,
    error: str = "",
) -> None:
    """提炼进度 run 终态回填（agent_runs/<job_id>——前端步级双源的第二源）。

    合成回填计划（steps[].out=id 对齐 slots 键）+ 累积 result；cards 空且带
    error 即 AI 全败（job 本身已按降级终态 done，run 面如实记 failed）。
    回填失败不炸宿主 job（进度是增强不是依赖——降级优先：存证写不进时
    前端仍有 jobs phase 粗粒度源）。
    """
    try:
        slots: dict[str, str] = {f"digest_{i}": t for i, t in cards.items()}
        if synthesis_text:
            slots["book_synthesis"] = synthesis_text
        in_chars = sum(int(s.get("chars_in") or 0) for s in progress_steps)
        out_chars = sum(int(s.get("chars_out") or 0) for s in progress_steps)
        status = "failed" if (error and not cards) else "done"
        plan = {
            "plan": PLAN_BOOK_DIGEST,
            "steps": [{"id": s["id"], "out": s["id"]} for s in progress_steps],
            "budget": {"steps": len(progress_steps), "in_chars": in_chars, "out_chars": out_chars},
        }
        result: dict[str, Any] = {
            "status": status,
            "steps": list(progress_steps),
            "slots": slots,
            "budget": {"steps": len(progress_steps), "in_chars": in_chars, "out_chars": out_chars},
        }
        if error:
            result["error"] = error
        finish_attached_run(job_id, plan, result)
    except OSError:
        pass


# ── 主执行体（job 闭包内）──────────────────────────────────────────


def run_fetch(
    source_url: str,
    book_url: str,
    book: dict[str, Any],
    chapters: Sequence[dict[str, Any]],
    *,
    digest: bool = True,
    client: BookEngineClient | None = None,
    chat_fn: ChatFn | None = None,
    on_phase: Callable[[str], None] | None = None,
    job_id: str = "book_fetch",
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """取文全链 → 计数 result（正文不进 job result——jobs result 只存计数纪律）。

    `should_stop`（2026-11-18 集成轮，R3.5 正典 §2.2 取消语义）：协作式取消
    检查点谓词（job 闭包传 `runner.is_cancelled`）——取文逐章、提炼逐 pass
    生效（pass 内不可中断，plan 是原子执行单元）；取消=**已完成章保留入库
    （有提炼卡者），未完成章放弃**（不落原文——放弃是用户显式决策，与 AI
    失败降级为原文是两条路径）。result 带 `cancelled:true` + note 明列
    「已入库 M 章，未完成 N 章已放弃」。

    返回 {ok, status: done|failed, book, chapters_total, fetched_n,
    fetch_failed:[{index,title,error}], distilled_n, degraded_n, raw_n,
    materials:[id...], synthesis:{id,chars}|null, skipped:[index...], note?,
    cancelled?}。
    status=failed 当且仅当「一章都没取到」（JobFailure 映射在 job_fn）。
    """
    items = _check_chapters_input(chapters, digest=digest)
    book_meta = _check_book_meta(book)
    cli = client if client is not None else get_engine_client()
    book_hash = _book_hash(source_url, book_url)
    total = len(items)

    def _stopped() -> bool:
        return bool(should_stop is not None and should_stop())

    # ① 取文 → 清洗 → 章节缓存（逐章检查点；缓存照写——内容不丢，入库与否由取消语义决定）
    fetched: list[dict[str, Any]] = []
    fetch_failed: list[dict[str, Any]] = []
    for i, chapter in enumerate(items, 1):
        if _stopped():
            break
        index, title, url = chapter["index"], chapter["title"].strip(), chapter["url"].strip()
        if on_phase is not None:
            on_phase(f"取文 {i}/{total} 第 {index} 章")
        try:
            payload = cli.content(source_url, book_url, url)
            text = payload.get("content")
            if not isinstance(text, str) or not text.strip():
                raise BookEngineUnavailable("引擎返回空正文")
            cleaned = clean_chapter(text)
            if not cleaned:
                raise BookEngineUnavailable("清洗后正文为空")
        except BookEngineUnavailable as exc:
            fetch_failed.append({"index": index, "title": title, "error": str(exc)})
            continue
        rel = chapter_cache_rel(book_hash, index, title)
        path = cache_root() / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, cleaned.encode("utf-8"))
        fetched.append(
            {"index": index, "title": title, "url": url, "cleaned": cleaned, "cache_rel": rel}
        )

    cancelled = _stopped()
    if not fetched:
        if cancelled:
            # 取消且零取文（提交即取消/首章前取消）：job done 部分终态，不误报 failed
            return {
                "ok": True,
                "status": "done",
                "cancelled": True,
                "book": book_meta,
                "chapters_total": total,
                "fetched_n": 0,
                "fetch_failed": fetch_failed,
                "distilled_n": 0,
                "degraded_n": 0,
                "raw_n": 0,
                "materials": [],
                "synthesis": None,
                "skipped": [],
                "note": f"用户取消：已入库 0 章，未完成 {total - len(fetch_failed)} 章已放弃",
            }
        sample = "；".join(f"第 {r['index']} 章: {r['error']}" for r in fetch_failed[:3])
        return {
            "ok": False,
            "status": "failed",
            "book": book_meta,
            "chapters_total": total,
            "fetched_n": 0,
            "fetch_failed": fetch_failed,
            "error": f"全部 {total} 章取文失败（{sample}…）",
        }

    # ② 提炼（digest=true 且 chapter_digest 有档案；否则降级说明）。
    #    进度 run（agent_runs/<job_id>）随提炼开/关：开=running 立即可查（前端
    #    GET /api/agent/runs?id=<job_id> 步级双源），终局回填 done/failed；AI
    #    未配置不建 run（无步可记——前端退 jobs phase 粗粒度源，降级优先）。
    #    取消（取文期已观察到）→ 不开提炼不建 run——未完成章放弃（正典 §2.2）。
    cards: dict[int, str] = {}
    degraded: list[dict[str, Any]] = []
    synthesis_text: str | None = None
    note = ""
    book_meta_full = {**book_meta, "source_name": _source_name(source_url)}
    if digest and not cancelled:
        if ai_configured("chapter_digest"):
            open_attached_run(
                job_id,
                build_book_digest(
                    [{"url": ch["url"], "title": ch["title"]} for ch in fetched]
                ),
            )
            try:
                cards, degraded, synthesis_text, progress_steps = _digest_loop(
                    fetched, job_id=job_id, chat_fn=chat_fn, on_phase=on_phase,
                    should_stop=should_stop,
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                _finish_progress_run(job_id, {}, None, [], error=str(exc) or "提炼异常")
                raise
            _finish_progress_run(job_id, cards, synthesis_text, progress_steps)
        else:
            note = "AI 未配置（chapter_digest 无可用档案），全部章节按原始文本入库"
    elif not digest:
        note = "未开启 AI 提炼，全部章节按原始文本入库"
    # digest 且取消 → 无降级说明 note（取消注记在下方统一追加）

    # ④ 素材落库（提炼卡/降级原文两形态 + 综述一条）。
    #    取消语义（正典 §2.2）：已完成章（有提炼卡）保留入库；未完成章放弃
    #    ——不落原文（放弃是用户显式决策，与 AI 失败降级为原文是两条路径）。
    cancelled = _stopped()  # 取一遍新值：提炼 pass 界的取消在落库前也生效
    fetched_at = _now_iso()
    material_ids: list[str] = []
    skipped: list[int] = []
    for chapter in fetched:
        index = chapter["index"]
        card = cards.get(index)
        distilled = card is not None
        if cancelled and not distilled:
            continue
        body = card if distilled else chapter["cleaned"]
        row = _make_chapter_material(
            source_url=source_url,
            book_hash=book_hash,
            book_meta=book_meta_full,
            index=index,
            title=chapter["title"],
            body=body,
            distilled=distilled,
            cache_rel_path=chapter["cache_rel"],
            original_chars=len(chapter["cleaned"]),
            fetched_at=fetched_at,
        )
        mid, outcome = _adopt(row)
        if outcome == "conflict":
            skipped.append(index)
        else:
            material_ids.append(mid)
    synthesis_out: dict[str, Any] | None = None
    if synthesis_text:
        row = _make_synthesis_material(
            source_url=source_url,
            book_hash=book_hash,
            book_meta=book_meta_full,
            chapter_count=len(cards),
            body=synthesis_text,
            fetched_at=fetched_at,
        )
        mid, outcome = _adopt(row)
        if outcome == "added":
            synthesis_out = {"id": mid, "chars": len(synthesis_text)}
        else:
            skipped.append(-1)

    degraded_indexes = {r["index"] for r in degraded}
    landed_indexes = set(cards) if cancelled else {f["index"] for f in fetched}
    raw_n = sum(1 for f in fetched if f["index"] not in cards and f["index"] in landed_indexes)
    result: dict[str, Any] = {
        "ok": True,
        "status": "done",
        "book": book_meta_full,
        "chapters_total": total,
        "fetched_n": len(fetched),
        "fetch_failed": fetch_failed,
        "distilled_n": len(cards),
        "degraded_n": sum(1 for f in fetched if f["index"] in degraded_indexes),
        "raw_n": raw_n,
        "materials": material_ids,
        "synthesis": synthesis_out,
        "skipped": skipped,
    }
    if cancelled:
        abandoned = total - len(material_ids) - len(fetch_failed) - len(skipped)
        result["cancelled"] = True
        cancel_note = f"用户取消：已入库 {len(material_ids)} 章，未完成 {abandoned} 章已放弃"
        note = f"{note}；{cancel_note}" if note else cancel_note
    if note:
        result["note"] = note
    return result


# ── jobs 接驳（对齐 agent/pipeline submit 先例）─────────────────────


def job_fn(
    source_url: str,
    book_url: str,
    book: dict[str, Any],
    chapters: list[dict[str, Any]],
    *,
    digest: bool = True,
    runner: Any = None,
    client: BookEngineClient | None = None,
    chat_fn: ChatFn | None = None,
) -> Callable[[str], dict[str, Any]]:
    """把一次取文包成 jobs 闭包（runner 调 fn(job_id)；job_id 即 AI run_id）。

    取消检查点：runner.is_cancelled 注入 run_fetch（runner None 时恒 False
    ——直接服务调用不可取消）。"""

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(text: str) -> None:
            if runner is not None:
                runner.set_phase(job_id, text)

        def _should_stop() -> bool:
            return bool(runner is not None and runner.is_cancelled(job_id))

        result = run_fetch(
            source_url,
            book_url,
            book,
            chapters,
            digest=digest,
            client=client,
            chat_fn=chat_fn,
            on_phase=_phase,
            job_id=job_id,
            should_stop=_should_stop,
        )
        if result.get("status") == "failed":
            raise JobFailure(str(result.get("error") or "取文失败"), result=result)
        return result

    return _run


def submit_to_runner(
    runner: Any,
    source_url: str,
    book_url: str,
    book: dict[str, Any],
    chapters: Sequence[dict[str, Any]],
    *,
    digest: bool = True,
    client: BookEngineClient | None = None,
) -> dict[str, Any]:
    """提交一次取文 job（kind=book_fetch）→ {job_id}。

    构造期校验（形状/上限/书名）与引擎探活在 `runner.submit` **之前**完成
    ——校验不通过抛 BookFetchError / BookEngineUnavailable，零 job 零落盘。
    """
    if not isinstance(source_url, str) or not source_url.strip():
        raise BookFetchError("缺少必填字段 sourceUrl（书源地址）")
    if not isinstance(book_url, str) or not book_url.strip():
        raise BookFetchError("缺少必填字段 bookUrl（书籍详情页地址）")
    items = _check_chapters_input(chapters, digest=digest)
    _check_book_meta(book)
    cli = client if client is not None else get_engine_client()
    if not cli.available():
        raise BookEngineUnavailable(
            "bookengine 未就绪——取文暂不可用（引擎服务 R1-R2 建设中或未运行）"
        )
    fn = job_fn(
        source_url.strip(),
        book_url.strip(),
        book,
        items,
        digest=digest,
        runner=runner,
        client=cli,
    )
    job_id = runner.submit(
        "book_fetch",
        {
            "sourceUrl": source_url.strip(),
            "bookUrl": book_url.strip(),
            "book": {"name": book["name"], "author": book.get("author") or ""},
            "chapters": items,
            "digest": bool(digest),
        },
        fn,
    )
    return {"job_id": job_id}


# ── book_search job（引擎代理聚合搜索）──────────────────────────────


def run_search(
    key: str,
    *,
    sources: Any = "all",
    page: int = 1,
    client: BookEngineClient | None = None,
) -> dict[str, Any]:
    """聚合搜索一次（引擎代理）→ 计数 result（items 截断 SEARCH_ITEMS_CAP）。"""
    cli = client if client is not None else get_engine_client()
    payload = cli.search(key, sources=sources, page=page)
    items = payload.get("items")
    rows = [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []
    return {
        "ok": True,
        "key": key,
        "page": int(page),
        "items": rows[:SEARCH_ITEMS_CAP],
        "total": payload.get("total", len(rows)),
        "searched": payload.get("searched", 0),
    }


def submit_search(
    runner: Any,
    key: str,
    *,
    sources: Any = "all",
    page: int = 1,
    client: BookEngineClient | None = None,
) -> dict[str, Any]:
    """提交一次搜索 job（kind=book_search）→ {job_id}。

    引擎探活在提交前（缺席 → BookEngineUnavailable，零 job；studio 转
    200 {ok:false} 灰灯语义——与 engine_status 的 absent 同源数据）。
    """
    if not isinstance(key, str) or not key.strip():
        raise BookFetchError("缺少必填字段 key（搜索关键词）")
    cli = client if client is not None else get_engine_client()
    if not cli.available():
        raise BookEngineUnavailable(
            "bookengine 未就绪——搜索暂不可用（引擎服务 R1-R2 建设中或未运行）"
        )

    def _run(_job_id: str) -> dict[str, Any]:
        return run_search(key.strip(), sources=sources, page=page, client=cli)

    job_id = runner.submit("book_search", {"key": key.strip(), "sources": sources, "page": page}, _run)
    return {"job_id": job_id}


__all__ = [
    "BOOK_CACHE_DIR",
    "BOOK_SOURCE_ID",
    "BOOK_URI_SCHEME",
    "SEARCH_ITEMS_CAP",
    "BookFetchError",
    "cache_root",
    "chapter_cache_rel",
    "job_fn",
    "resolve_original_ref",
    "run_fetch",
    "run_search",
    "submit_search",
    "submit_to_runner",
]
