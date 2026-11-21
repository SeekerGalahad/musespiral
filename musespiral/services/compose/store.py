"""compose 包：store（会话存储与 CRUD，含锁、原子写）。

原 compose_service.py（1734 行单文件）按 R-1 六簇拆分的存储簇：
目录布局常量、slug 校验、session.json 装载/落盘、按 slug 串行的元数据锁
（@_serialized）、会话 CRUD（new/add_source/remove_source）与读面
（list/detail/status）。块视图助手 _working_blocks/_working_markdown 也落
此处——「到目前为止写了什么」是存储层的会话读面，summary/characters 两簇
共用（放任何一簇都会造成簇间反向依赖）。
"""
from __future__ import annotations

import functools
import json
import re
import threading
from pathlib import Path

from ...core.composition import (
    KIND_ARTICLE,
    SESSION_KINDS,
    STATUS_COMPOSING,
    Block,
    CompositionSession,
    SourceRef,
    blocks_from_markdown,
    markdown_from_blocks,
)
from ...core.discovery import scanner
from ...infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ...jobs.store import now_iso as _now_iso
from ..discover_service import DEFAULT_ARTICLES_ROOT

# ---- 会话目录布局（§2.1 落盘形态）----

SESSION_DIR_SUFFIX = ".compose"
DRAFT_FILE = "draft.md"
META_FILE = "session.json"
ROUNDS_DIR = "rounds"
DECISIONS_SUFFIX = ".decisions.json"
FEEDBACK_FILE = "feedback.jsonl"

# slug 白名单：字母数字开头，限 [A-Za-z0-9._-]（防路径穿越/怪文件名）
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ComposeError(ValueError):
    """创作台业务错误（可读消息；CLI/Studio 转 4xx/ok:false）。"""


# ---- 路径与装载 ----


def sessions_root(root: str | Path | None = None) -> Path:
    """会话所在的 articles root（缺省 = Studio launchd 同款约定）。"""
    return Path(root).expanduser().resolve() if root is not None else DEFAULT_ARTICLES_ROOT


def session_dir(slug: str, root: str | Path | None = None) -> Path:
    if not _SLUG_RE.match(slug or ""):
        raise ComposeError(f"非法 slug: {slug!r}（字母数字开头，仅限 [A-Za-z0-9._-]）")
    return sessions_root(root) / f"{slug}{SESSION_DIR_SUFFIX}"


def _meta_path(d: Path) -> Path:
    return d / META_FILE


def _draft_path(d: Path) -> Path:
    return d / DRAFT_FILE


def _rounds_dir(d: Path) -> Path:
    return d / ROUNDS_DIR


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ComposeError(f"读取失败: {path.name}（{exc}）") from None
    except json.JSONDecodeError:
        raise ComposeError(f"文件损坏（非合法 JSON）: {path.name}") from None
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, obj: dict) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _load_decisions(d: Path, n: int) -> list[dict]:
    path = _rounds_dir(d) / f"{n}{DECISIONS_SUFFIX}"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []


def _load_session(slug: str, root: str | Path | None = None) -> tuple[CompositionSession, Path]:
    """装在会话（meta + draft.md 块视图 + 各轮 decisions）。"""
    d = session_dir(slug, root)
    if not d.is_dir():
        raise ComposeError(f"会话不存在: {slug}（先 musespiral compose new）")
    meta = _read_json(_meta_path(d))
    draft_text = ""
    try:
        draft_text = _draft_path(d).read_text(encoding="utf-8")
    except OSError:
        pass
    # 锚破坏防撞回：无锚块的新 id 避让会话既有 id（draft+轮次记录）
    prior_ids = {
        str(b.get("id"))
        for r in (meta.get("rounds") or [])
        for b in (r.get("blocks") or [])
        if b.get("id")
    }
    session = CompositionSession.from_meta(
        meta, blocks_from_markdown(draft_text, avoid=prior_ids)
    )
    _overlay_origins(session)
    for r in session.rounds:
        r.decisions = _load_decisions(d, r.n)
    return session, d


def _overlay_origins(session: CompositionSession) -> None:
    """draft.md 只持久化 id——血统按最近一轮块记录回填（无记录的块=人工
    新写 → human）。"""
    records: dict[str, str] = {}
    for r in session.rounds:
        for b in r.blocks:
            records[b.id] = b.origin
    if not records:
        return
    session.draft = [
        Block(b.id, b.kind, b.text, records.get(b.id, "human")) for b in session.draft
    ]


def _save_meta(session: CompositionSession, d: Path) -> None:
    _write_json_atomic(_meta_path(d), session.to_meta())


# ---- 会话元数据串行化（2026-11-19 集成修复批）---------------------------
#
# session.json 是整份覆写的单文件真值：两个「读-改-写」并发（如 adopt 后
# 的后台摘要线程 vs 用户手改摘要、generate vs 摘要刷新）会互相丢更新——
# 后写者用自己装进内存的旧快照整份覆盖，前写者的字段静默回退（探针实测
# 摘要被清空、手改被 AI 结果冲掉、并发生成丢轮次）。所有 load→save_meta
# 的写路径统一按 slug 串行（@_serialized，读-改-写全程持锁——含 AI 调用
# 在内；同会话的 AI 调用本就应互斥，单用户 Studio 无吞吐代价）；读面
# （session_detail 等）依赖 atomic_write 的整文件原子替换，无需加锁。

_META_LOCKS: dict[str, threading.RLock] = {}
_META_LOCKS_GUARD = threading.Lock()


def _meta_lock(slug: str) -> threading.RLock:
    with _META_LOCKS_GUARD:
        lock = _META_LOCKS.get(slug)
        if lock is None:
            lock = _META_LOCKS[slug] = threading.RLock()
        return lock


def _serialized(fn):
    """写路径装饰器：slug=首参，读-改-写全程按会话串行。"""

    @functools.wraps(fn)
    def wrapper(slug: str, *args, **kwargs):
        with _meta_lock(str(slug)):
            return fn(slug, *args, **kwargs)

    return wrapper


# ---- 读面：status / list / detail ----


def list_sessions(root: str | Path | None = None) -> list[dict]:
    """会话清单（按 slug 排序；宽容读——坏 session.json 跳过不塌）。"""
    base = sessions_root(root)
    if not base.is_dir():
        return []
    out: list[dict] = []
    for d in sorted(base.glob(f"*{SESSION_DIR_SUFFIX}")):
        if not d.is_dir():
            continue
        try:
            meta = _read_json(_meta_path(d))
        except ComposeError:
            continue
        if not meta:
            continue
        out.append(
            {
                "slug": meta.get("slug") or d.name[: -len(SESSION_DIR_SUFFIX)],
                "title": meta.get("title") or "",
                "status": meta.get("status") or STATUS_COMPOSING,
                # E-5：清单徽标区分小说会话（右栏前情区只在 novel 显示）
                "kind": str(meta.get("kind") or KIND_ARTICLE),
                "sources": len(meta.get("sources") or []),
                "rounds": len(meta.get("rounds") or []),
                "created": meta.get("created") or "",
            }
        )
    return out


def session_detail(slug: str, root: str | Path | None = None) -> dict:
    """单会话详情（UI 三栏数据面：素材/当前稿/轮次）。"""
    # 聚合读面横跨 outline/summary/characters 三簇——函数体内导入避免
    # store ↔ 业务簇的循环依赖（拆分前同文件无此问题，语义不变）。
    from . import outline as _outline
    from .characters import _session_characters, novel_chapter_stats
    from .summary import article_chars

    session, d = _load_session(slug, root)
    final = sessions_root(root) / f"{slug}.md"
    return {
        "slug": session.slug,
        "title": session.title,
        "status": session.status,
        # E-5 小说模式（§3.6）：kind + 前情提要 + 挂卡三面随详情下发
        "kind": session.kind,
        "sources": [s.to_dict() for s in session.sources],
        "draft": [b.to_dict() for b in session.draft],
        "rounds": [
            {
                "n": r.n,
                "blocks": [b.to_dict() for b in r.blocks],
                "decisions": r.decisions,
            }
            for r in session.rounds
        ],
        "final_article": final.name if final.is_file() else None,
        "outline": _outline.outline_get(slug, root),  # G-T2 大纲（§三；随详情下发，零新 GET 端点）
        # G-T4 滚动摘要（§五）：右栏只读+编辑面数据；字数/档位供「下次何时自动
        # 重算」可视化（前端据此提示距下一档还差多少字）
        "rolling_summary": session.rolling_summary,
        "rolling_summary_chars": article_chars(_working_markdown(session)),
        "rolling_summary_level": int(session.rolling_summary_level or 0),
        # E-5 前情提要（§3.6）：摘要 + 章节统计（右栏「第1-14章 · 共42,000字」行）
        "novel_summary": session.novel_summary,
        "novel_summary_chapters": int(session.novel_summary_chapters or 0),
        "novel": novel_chapter_stats(session),
        # E-5 人物卡挂载面：挂卡完整视图（前端右栏卡片渲染；空列表=未挂卡）
        "characters": _session_characters(session),
    }


def status(slug: str | None = None, root: str | Path | None = None) -> dict:
    """CLI/概览读面：无 slug 返回清单，有 slug 返回详情。"""
    if slug is None:
        return {"sessions": list_sessions(root)}
    return session_detail(slug, root)


# ---- new / add_source ----


def new_session(
    slug: str,
    title: str,
    root: str | Path | None = None,
    *,
    kind: str = KIND_ARTICLE,
) -> Path:
    """建会话（目录+session.json+空 draft.md）；已存在拒绝（不覆盖）。

    kind（E-5 §3.6）：article 缺省 / novel（小说创作——右栏显示前情提要，
    prompt 注入前情提要 + 人物卡）。枚举外 → ComposeError。
    """
    if kind not in SESSION_KINDS:
        raise ComposeError(f"kind 必须是 {'/'.join(SESSION_KINDS)} 之一: {kind!r}")
    d = session_dir(slug, root)
    if d.exists():
        raise ComposeError(f"会话已存在（拒绝覆盖）: {slug}")
    final = sessions_root(root) / f"{slug}.md"
    if final.exists():
        raise ComposeError(f"正稿已存在（拒绝覆盖）: {final.name}")
    (_rounds_dir(d)).mkdir(parents=True, exist_ok=False)
    meta = {
        "slug": slug,
        "title": title or slug,
        "sources": [],
        "status": STATUS_COMPOSING,
        "rounds": [],
        "created": _now_iso(),
        # E-5：会话种类（article/novel）+ 小说域两字段（空态显式落盘，
        # 读侧不必猜缺键语义）
        "kind": kind,
        "novel_summary": "",
        "novel_summary_chapters": 0,
        "characters": [],
    }
    _write_json_atomic(_meta_path(d), meta)
    _atomic_write(_draft_path(d), b"")
    return d


def list_source_sections(path: str | Path) -> list[str]:
    """素材章节清单（复用 discovery scanner 的 sections 口径——`## ` 标题）。

    文件不存在抛 OSError（调用方转可读错误）；非 .md 抛 ValueError。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"素材不存在: {p}")
    if p.suffix.lower() != ".md":
        raise ValueError(f"只接受 Markdown 素材: {p}")
    return scanner.scan_file(p)["sections"]


@_serialized
def add_source(
    slug: str,
    path: str | Path | None = None,
    sections: list[str] | None = None,
    root: str | Path | None = None,
    *,
    material_id: str | None = None,
    article_slug: str | None = None,
) -> dict:
    """素材入池（章节勾选校验后记入 session.json）。

    三种来源（N-2「引用自己」2026-11-21 起三选一）：
      - path：既有文件形态（绝对路径或 KB 相对路径，CLI 兼容面不变）；
      - material_id：素材库直引——查 materials_service 活跃行，池内以虚拟
        定位符 ``material://<id>`` 记 path（prompt 侧经素材库取 payload.
        text；素材已删时生成期跳过不塌，同文件被移走语义）；忽略 sections
        （素材无章节勾选面，全文走预算截断）；
      - article_slug：文章根 ``<slug>.md``（sessions_root 同根约定）解析为
        文件 path——与既有文件形态同一读取路径；slug 白名单同 _SLUG_RE
        （防路径穿越）。

    - sections=None → 不勾选（生成时取全文，走预算截断）；
    - 给定 sections 必须逐个命中 scanner 的章节清单（错拼早失败，不静默丢）；
    - 同 path 重复添加 = 更新勾选（不重复占预算）。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），不能再加素材")
    picked: list[str] = []
    known: list[str] = []
    ref_material_id = ""
    if material_id is not None:
        from .. import materials_service  # 函数体内导入（导入期解耦）

        mid = str(material_id).strip()
        material = materials_service.get_material(mid)
        if material is None:
            raise ComposeError(f"素材不存在或已删: {mid!r}")
        src_path = f"material://{mid}"
        ref_material_id = mid
    else:
        if article_slug is not None:
            art_slug = str(article_slug).strip()
            if not _SLUG_RE.match(art_slug):
                raise ComposeError(f"非法 article_slug: {art_slug!r}（字母数字开头，仅限 [A-Za-z0-9._-]）")
            art = sessions_root(root) / f"{art_slug}.md"
            if not art.is_file():
                raise ComposeError(f"文章不存在（文章根下无 <slug>.md）: {art_slug}")
            path = art
        if path is None:
            raise ComposeError("缺少素材来源（path / material_id / article_slug 三选一）")
        known = list_source_sections(path)
        picked = [str(s).strip() for s in (sections or [])]
        unknown = [s for s in picked if s not in known]
        if unknown:
            raise ComposeError(
                f"章节不存在于素材: {'、'.join(unknown)}（可用: {'、'.join(known) or '（无二级标题，全文即一章）'}）"
            )
        src_path = str(Path(path).expanduser().resolve())
    others = [s for s in session.sources if s.path != src_path]
    session.sources = others + [SourceRef(src_path, picked, ref_material_id)]
    _save_meta(session, d)
    out: dict = {"path": src_path, "sections": picked, "known_sections": known}
    if ref_material_id:
        out["material_id"] = ref_material_id
    return out


@_serialized
def remove_source(slug: str, path: str | Path, root: str | Path | None = None) -> dict:
    """素材撤出素材池（F-C，2026-11-18）：session.json sources 删该条+原子重写。

    - 与 add_source 同口径按 resolve 后绝对路径匹配（相对路径也可传入；
      素材库直引条按 ``material://<id>`` 原样匹配）；
    - 不要求素材文件仍存在（文件被移走后的池清理是合法场景）；
    - 封版会话拒绝（同 add_source——采纳后素材归因已写入正稿，池属归档
      快照；连坐文案语义也只对 composing 态成立）；
    - 返回被移除源的章节勾选信息（前端成功 toast/池行恢复数据面）。
    """
    session, d = _load_session(slug, root)
    if session.status != STATUS_COMPOSING:
        raise ComposeError(f"会话已采纳（{session.status}），素材池已封存，不可移除素材")
    raw = str(path)
    if raw.strip().startswith("material://"):
        # 素材库直引条（N-2）：虚拟定位符不经文件路径解析，直接按池内
        # path 原样匹配（Path.resolve 会把它错拼成 cwd 相对路径）
        src_path = raw.strip()
        target = next((s for s in session.sources if s.path == src_path), None)
    else:
        src_path = str(Path(path).expanduser().resolve())
        target = next((s for s in session.sources if s.path == src_path), None)
    if target is None:
        pool = "、".join(Path(s.path).name for s in session.sources) or "（空）"
        raise ComposeError(f"素材不在池中: {Path(src_path).name}（当前池内: {pool}）")
    session.sources = [s for s in session.sources if s.path != src_path]
    _save_meta(session, d)  # atomic_write_bytes：tmp+os.replace 原子重写
    return {
        "path": src_path,
        "sections": list(target.sections),
        "sources_left": len(session.sources),
    }


def _working_blocks(session: CompositionSession) -> list[Block]:
    """「到目前为止写了什么」的块视图：真稿优先，空则用最近一轮 AI 草稿。

    触发点落在 adopt 之后（§五）——那时 draft = 合并定稿；而会话内两次
    generate 之间 draft 仍为空（draft.md 只在 adopt 时写），此时以末轮块
    为「已写全文」，摘要才有意义。两者都空 → 空列表。
    """
    if session.draft:
        return session.draft
    return session.rounds[-1].blocks if session.rounds else []


def _working_markdown(session: CompositionSession) -> str:
    blocks = _working_blocks(session)
    return markdown_from_blocks(blocks) if blocks else ""
