"""Studio 书籍域端点（R3 接驳 + R3.5 提炼，2026-11-18）——契约 §3.10 五条
（GET +3 / POST +2；路由面双锁见 tests/test_architecture.py 与
tests/test_book_studio.py）。

  - POST /api/book/search          {confirm, key, scope?, page?} → job book_search
                                    （submit 前引擎探活：缺席 200 {ok:false} 灰灯语义）
  - POST /api/materials/from-chapters {confirm, sourceUrl, bookUrl, book, chapters,
                                    digest?} → job book_fetch（**提交零落盘**——
                                    正文/缓存/素材全部由 job 运行期写）
  - GET  /api/book/original        ?ref=&full?：章节缓存原文（三道门：book_cache
                                    内边界 + .txt 白名单 + 50k 折叠两段）
  - GET  /api/book/info            ?sourceUrl=&bookUrl=：引擎详情代理（同步）
  - GET  /api/book/toc             ?sourceUrl=&bookUrl=：引擎目录代理（同步）
  - GET  /api/book/chapter         ?sourceUrl=&bookUrl=&chapterUrl=：单章正文代理
                                    （2026-11-24 源中枢重设计批：阅读面板
                                    「未入库先读章」——**零落盘**，要入库走
                                    from-chapters；正文截断 CHAPTER_CAP）

confirm 闸门由 server.py 全局收口；形状校验 400 在本层，业务可读错误
200 {ok:false}（引擎缺席/提交校验失败——错误双层语义 §1.3）。
"""
from __future__ import annotations

from typing import Any

from ..infra.bookengine import BookEngineUnavailable
from ..services import book_fetch_service
from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _query_str,
    _sanitize_error,
)

# 原文折叠阈值（R3.5 正典 §4.1：>50k 字符折叠为「前 20k 字 + 展开全部」两段）
ORIG_FOLD_CHARS = 50_000
ORIG_HEAD_CHARS = 20_000
# 单文件读入上限（引擎侧有抓取上限，这里是防御面——异常大文件不整体进内存）
ORIG_READ_CAP = 2_000_000
# 读章正文上限（2026-11-24）：超长章节截断，防撑爆前端渲染
CHAPTER_CAP = 200_000


def _runner():
    """借用 Studio 共享 runner（actions._runner：首取即孤儿恢复）。"""
    from .actions import _runner as studio_runner

    return studio_runner()


# ── POST 面 ──────────────────────────────────────────────────────


def _action_book_search(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/book/search：聚合搜索 → job book_search（引擎代理）。"""
    key = data.get("key")
    if not isinstance(key, str) or not key.strip():
        raise _HttpError(400, "缺少必填字段 key（搜索关键词）")
    page = data.get("page", 1)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise _HttpError(400, "page 必须是 ≥1 的整数")
    scope = data.get("scope", "all")
    if not isinstance(scope, (str, list)):
        raise _HttpError(400, "scope 必须是字符串或字符串数组（缺省 all）")
    try:
        out = book_fetch_service.submit_search(_runner(), key, sources=scope, page=page)
    except book_fetch_service.BookFetchError as exc:
        raise _HttpError(400, str(exc)) from None
    except BookEngineUnavailable as exc:
        return _error_payload(exc, ctx.root)  # 灰灯语义：absent 是业务拒绝非 400
    return {"ok": True, "job_id": out["job_id"]}


def _action_from_chapters(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/from-chapters：选章入素材库 → job book_fetch。

    「入素材库」这一人工动作=决策点（设计 §三裁定）；端点闸即 confirm——
    **提交期零落盘**（preview 零落盘纪律：materials/book_cache 全部由 job 写）。
    """
    source_url = data.get("sourceUrl")
    if not isinstance(source_url, str) or not source_url.strip():
        raise _HttpError(400, "缺少必填字段 sourceUrl（书源地址）")
    book_url = data.get("bookUrl")
    if not isinstance(book_url, str) or not book_url.strip():
        raise _HttpError(400, "缺少必填字段 bookUrl（书籍详情页地址）")
    book = data.get("book")
    if not isinstance(book, dict):
        raise _HttpError(400, "缺少必填字段 book（含 name/author）")
    chapters = data.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise _HttpError(400, "缺少必填字段 chapters（非空章节数组）")
    digest = data.get("digest")
    if digest is None:
        digest = True  # R3.5 正典 §2.1：AI 提炼 switch 默认开
    if not isinstance(digest, bool):
        raise _HttpError(400, "digest 必须是布尔值（AI 提炼开关）")
    try:
        out = book_fetch_service.submit_to_runner(
            _runner(),
            source_url,
            book_url,
            book,
            chapters,
            digest=digest,
        )
    except book_fetch_service.BookFetchError as exc:
        raise _HttpError(400, str(exc)) from None
    except BookEngineUnavailable as exc:
        return _error_payload(exc, ctx.root)  # 引擎缺席灰灯同 search
    return {"ok": True, "job_id": out["job_id"]}


# ── GET 面 ──────────────────────────────────────────────────────


def _book_original_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/book/original?ref=&full=1：素材详情「查看原始章节」的数据面。

    三道门（materials file 同款纪律的纯文本版）：ref 必须解析落
    `~/.musespiral/book_cache/` 内（`resolve_original_ref`）+ 后缀 .txt 白名单 +
    50k 折叠（>50k → 前 20k + folded:true；`full=1` 全文——前端「展开全部」）。
    """
    ref = _query_str(query, "ref")
    if ref is None:
        raise _HttpError(400, "缺少必填参数 ref（meta.original_ref.path）")
    full = (_query_str(query, "full") or "").strip().lower() in ("1", "true")
    try:
        path = book_fetch_service.resolve_original_ref(ref)
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    if not path.is_file():
        raise _HttpError(404, f"章节缓存不存在: {ref}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:ORIG_READ_CAP]
    except OSError as exc:
        raise _HttpError(404, _sanitize_error(exc, ctx.root)) from None
    chars = len(text)
    folded = False
    if chars > ORIG_FOLD_CHARS and not full:
        text = text[:ORIG_HEAD_CHARS]
        folded = True
    return {"ok": True, "chars": chars, "folded": folded, "text": text}


def _engine_absent_payload(exc: BookEngineUnavailable) -> dict[str, Any]:
    """引擎缺席的统一读面形态（与 /api/booksource/engine 的 absent 语义对齐）。"""
    return {"ok": True, "status": "absent", "error": str(exc)}


def _book_info_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/book/info?sourceUrl=&bookUrl=：详情（同步引擎代理；#books 详情态数据面）。"""
    source_url = _query_str(query, "sourceUrl")
    book_url = _query_str(query, "bookUrl")
    if source_url is None or book_url is None:
        raise _HttpError(400, "缺少必填参数 sourceUrl 与 bookUrl")
    from ..infra.engine_select import get_engine_client

    try:
        payload = get_engine_client().book_info(source_url, book_url)
    except BookEngineUnavailable as exc:
        return _engine_absent_payload(exc)
    return {"ok": True, "status": "ok", "info": payload.get("info") or {}}


def _book_toc_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/book/toc?sourceUrl=&bookUrl=：目录（同步引擎代理）。"""
    source_url = _query_str(query, "sourceUrl")
    book_url = _query_str(query, "bookUrl")
    if source_url is None or book_url is None:
        raise _HttpError(400, "缺少必填参数 sourceUrl 与 bookUrl")
    from ..infra.engine_select import get_engine_client

    try:
        payload = get_engine_client().toc(source_url, book_url)
    except BookEngineUnavailable as exc:
        return _engine_absent_payload(exc)
    chapters = [c for c in (payload.get("chapters") or []) if isinstance(c, dict)]
    return {"ok": True, "status": "ok", "chapters": chapters, "total": payload.get("total", len(chapters))}


def _book_chapter_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/book/chapter?sourceUrl=&bookUrl=&chapterUrl=：单章正文（同步代理）。

    阅读面板「先读后决定」的数据面：**零落盘**——只把正文取回来展示，
    不入缓存、不入素材（要入库走 /api/materials/from-chapters 的 job）。
    引擎缺席与 info/toc 同语义：200 + status:"absent"。
    """
    source_url = _query_str(query, "sourceUrl")
    book_url = _query_str(query, "bookUrl")
    chapter_url = _query_str(query, "chapterUrl")
    if source_url is None or book_url is None or chapter_url is None:
        raise _HttpError(400, "缺少必填参数 sourceUrl / bookUrl / chapterUrl")
    from ..infra.engine_select import get_engine_client

    try:
        payload = get_engine_client().content(source_url, book_url, chapter_url)
    except BookEngineUnavailable as exc:
        return _engine_absent_payload(exc)
    text = payload.get("content") or ""
    if not isinstance(text, str):
        text = str(text)
    truncated = len(text) > CHAPTER_CAP
    if truncated:
        text = text[:CHAPTER_CAP]
    return {
        "ok": True,
        "status": "ok",
        "title": payload.get("title") or "",
        "content": text,
        "chars": len(text),
        "truncated": truncated,
    }

__all__ = [
    "_action_book_search",
    "_action_bookengine_demo",
    "_action_from_chapters",
    "_book_info_payload",
    "_book_original_payload",
    "_book_toc_payload",
    "_book_chapter_payload",
]

def _action_bookengine_demo(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/bookengine_demo：体验模式开关（2026-11-23 用户反馈批）。

    mode=on/off/status（缺省 on）。写用户配置文件后**需重启 Studio 生效**
    ——launchd 进程不继承 shell env，故走配置而非环境变量（响应里明说）。
    """
    from ..services import bookengine_admin

    mode = str(data.get("mode") or "on").strip().lower()
    if mode not in ("on", "off", "status"):
        raise _HttpError(400, "mode 必须是 on/off/status")
    message = bookengine_admin.set_demo(mode)
    return {"ok": True, "mode": mode, "message": message,
            "need_restart": mode != "status"}
