"""Studio 本地书籍域端点（B1 本地书籍线，2026-11-26）——契约 §3.27 七条
（GET +4 / POST +3；路由面双锁见 tests/test_architecture.py 与
tests/test_localbook_studio.py）。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2 端点表：

  - POST /api/localbook/add       {path, title?, author?, rule?, confirm}
                                  → 读文件→探测编码→解析章节→入书架（同步：
                                  单书解析，返回统计；「大文件解析」在本实现里
                                  不单开 job——见下「为什么 add 同步」）
  - POST /api/localbook/remove    {id, confirm}       → 下架（用户原文件零触碰）
  - POST /api/localbook/reparse   {id, rule?, confirm} → 换规则重解析
  - GET  /api/localbook/list                          → 书架清单
  - GET  /api/localbook/info?id=                      → meta + 章节数 + 规则命中
  - GET  /api/localbook/toc?id=                       → 目录（bookengine 同形）
  - GET  /api/localbook/chapter?id=&n=                → 正文（cap 200k）
  - POST /api/materials/from-local-chapters {id, chapters, book?, case?, confirm}
                                  → job localbook_fetch（选章入素材库）

**为什么 add 是同步而不是 job**：设计 §1.2 端点表标了「job：大文件解析」，但
解析成本 = 一次 read_bytes + 一次 decode + 一次逐行正则扫描；64MB 上限下最坏
也是秒级，且 `_resolve_path` 的体积门在提交期就 fail-closed。开 job 会把
「导入一本书」变成「轮询一个任务才看到书架」，对单本导入是体验倒退。如实
记录在 notes：与设计字面有偏差，理由=成本实测（不是「懒得做」）。

confirm 闸门由 server.py 全局收口；形状校验 400 在本层，业务可读错误
400（`LocalBookError` 是 ValueError 子类，走 `_http_400_on`）。
"""
from __future__ import annotations

from typing import Any

from ..services import localbook_service
from .security import (
    _http_400_on,
    _HttpError,
    _query_str,
    _require_str,
    _StudioContext,
)

_JOB_KIND = "localbook_fetch"


def _runner():
    """借用 Studio 共享 runner（actions._runner：首取即孤儿恢复）。"""
    from .actions import _runner as studio_runner

    return studio_runner()


def _book_id_query(query: dict[str, list[str]]) -> str:
    """取 `?id=`（GET 面共用；缺失 → 400）。"""
    book_id = _query_str(query, "id")
    if book_id is None:
        raise _HttpError(400, "缺少必填参数 id（书 id）")
    return book_id


# ── POST 面 ──────────────────────────────────────────────────────


def _action_localbook_add(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/localbook/add {path, title?, author?, rule?}：导入一本书。

    引用型（设计 §五）：只在书架写 meta/章节偏移，**不复制文件**。同路径重复
    导入 = 覆盖重解析（同 id upsert），不产生第二本——「重新导入」是用户的
    自然修复动作，不该变成两本同名书。
    """
    path = _require_str(data, "path")
    for key in ("title", "author", "rule"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise _HttpError(400, f"{key} 必须是字符串: {data[key]!r}")
    with _http_400_on(localbook_service.LocalBookError):
        meta = localbook_service.add_book(
            path,
            title=data.get("title") or "",
            author=data.get("author") or "",
            rule=data.get("rule") or "",
        )
    return {"ok": True, "book": meta}


def _action_localbook_remove(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/localbook/remove {id}：下架一本书（原文件零触碰）。"""
    book_id = _require_str(data, "id")
    with _http_400_on(localbook_service.LocalBookError):
        result = localbook_service.remove_book(book_id)
    return result


def _action_localbook_reparse(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/localbook/reparse {id, rule?}：换章节规则重解析（插件入口）。"""
    book_id = _require_str(data, "id")
    rule = data.get("rule")
    if rule is not None and not isinstance(rule, str):
        raise _HttpError(400, f"rule 必须是字符串（规则 id）: {rule!r}")
    with _http_400_on(localbook_service.LocalBookError):
        meta = localbook_service.reparse_book(book_id, rule=rule or "")
    return {"ok": True, "book": meta}


def _action_from_local_chapters(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/from-local-chapters：选章入素材库 → job localbook_fetch。

    形状对齐网络书 `/api/materials/from-chapters`（`{confirm, id, chapters,
    book?, case?}`）：`chapters` 是**章号数组**（不是 url 对象——本地书没有
    章节 URL，章号是唯一标识）。提交期零落盘（预览纪律），素材全部由 job 写。
    `book` 只用于 job 参数展示（书名以 meta 为准，不信客户端自报）。
    """
    book_id = _require_str(data, "id")
    chapters = data.get("chapters")
    book = data.get("book")
    if book is not None and not isinstance(book, dict):
        raise _HttpError(400, "book 必须是对象（含 name）或省略")
    case = data.get("case")
    if case is not None and not isinstance(case, dict):
        raise _HttpError(400, "case 必须是对象或省略")
    with _http_400_on(localbook_service.LocalBookError):
        out = localbook_service.submit_to_runner(
            _runner(), book_id, chapters, case=case or None
        )
    return {"ok": True, "job_id": out["job_id"], "chapters": out["chapters"]}


# ── GET 面 ──────────────────────────────────────────────────────


def _localbook_list_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/localbook/list：书架清单（索引顺序=导入顺序）。"""
    return localbook_service.list_books()


def _localbook_info_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/localbook/info?id=：meta + 章节数 + 规则命中 + 文件现存状态。"""
    book_id = _book_id_query(query)
    with _http_400_on(localbook_service.LocalBookError):
        return localbook_service.book_info(book_id)


def _localbook_toc_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/localbook/toc?id=：目录（bookengine 同形，url=`localbook:<id>#<n>`）。"""
    book_id = _book_id_query(query)
    with _http_400_on(localbook_service.LocalBookError):
        return localbook_service.book_toc(book_id)


def _localbook_chapter_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/localbook/chapter?id=&n=：单章正文（cap 200k，引用型现取）。"""
    book_id = _book_id_query(query)
    raw_n = _query_str(query, "n")
    if raw_n is None:
        raise _HttpError(400, "缺少必填参数 n（章号）")
    try:
        n = int(raw_n)
    except ValueError:
        raise _HttpError(400, f"n 必须是整数: {raw_n!r}") from None
    with _http_400_on(localbook_service.LocalBookError):
        return localbook_service.book_chapter(book_id, n)


# ---- 域内读面：注册在 routing.py 的精确路由表（契约 GET+4）----
# 不用「GET_ROUTES 字典」形态：routes 自省脚本（scripts/route_manifest.py）
# 把 studio 下任何含 "/api/..." 键的 dict 一律当**动作面（POST）**统计，字典
# 形态会让四读面被误记为双态路径（test_route_manifest 双态锁红）。四个函数
# 由 routing.py 的 if 链逐个分派——与既有 /api/book/* 三读面同形态。
#
# info 已带 `rule/rule_name`，规则清单不单开端点：前端下拉的「当前规则」在
# 详情里，「可选规则」由内置三档常量给出（加自定义规则=放一个 JSON）。

# ---- 域内动作片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Any] = {
    "/api/localbook/add": _action_localbook_add,
    "/api/localbook/remove": _action_localbook_remove,
    "/api/localbook/reparse": _action_localbook_reparse,
    "/api/materials/from-local-chapters": _action_from_local_chapters,
}

__all__ = [
    "ROUTES",
    "_action_from_local_chapters",
    "_action_localbook_add",
    "_action_localbook_remove",
    "_action_localbook_reparse",
    "_localbook_chapter_payload",
    "_localbook_info_payload",
    "_localbook_list_payload",
    "_localbook_toc_payload",
]
