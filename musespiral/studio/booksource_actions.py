"""Studio 书源域端点（R0 + R0.5 F-X1/X3 响应扩展）——/api/booksource 族 9 端点=4 GET+5 POST（契约 §3.9）。

  - POST /api/booksource/import        导入预览（confirm:true；**零落库**）
  - POST /api/booksource/import_apply  三态确认落库（confirm:true；唯一写面）
  - POST /api/booksource/refresh       手动刷新订阅（confirm:true；interval 闸门旁路）
  - POST /api/booksource/subs/add      新增/更新订阅（confirm:true）
  - POST /api/booksource/subs/del      删除订阅（confirm:true）
  - GET  /api/booksource               索引列表（?group=&q=；轻量投影）
  - GET  /api/booksource/one           单源详情（?url=；含规则组）
  - GET  /api/booksource/subs          订阅清单
  - GET  /api/booksource/engine        引擎状态（ok|absent——前端灰灯数据源）

R0.5 响应扩展（service 层透传，本层只文档化契约；前端弹窗批 B 消费）：

  import        响应增 `kind`（六类：bookSource/theme/tts/replace/layout/unknown）、
                `skipped:[{index, reason}]`（脏源清单）、`counts.dupUrls`
                （包内重复 URL 数）；非书源包 items 为空 + `message` 类型化
                提示（ok 仍 True——可读边界说明而非 400）。
  import_apply  响应增 `kind/message` + `stored`（去重后落库数）/
                `deduped_from`（落库前选择条数，F-X3 去重透明）。
                注意 skipped 键语义在两端点不同：import=脏源对象清单、
                import_apply=未选中 url 串清单。

confirm 闸门由 server.py 全局收口（§1.2）；形状校验 400 在本层，业务可读
错误 200 {ok:false}（错误双层语义 §1.3，与 media/ingest 面同纪律）。
"""
from __future__ import annotations

from typing import Any

from ..core.booksource.importer import BookSourceImportError
from ..infra.booksource_fetch import BookSourceFetchError
from ..services import booksource_service
from .security import (
    _HttpError,
    _StudioContext,
    _query_str,
    _sanitize_error,
)


def _import_text(data: dict[str, Any]) -> str:
    """导入请求体取文本（text_or_url；两种键名都收，前端契约用 text_or_url）。"""
    for key in ("text_or_url", "text", "url"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise _HttpError(400, "缺少必填字段 text_or_url（书源 JSON 文本或订阅 URL）")


def _keep_flags(data: dict[str, Any]) -> dict[str, bool]:
    """importKeep 偏好三键（缺省 false——全用导入值）。"""
    return {
        "keep_name": data.get("keep_name") is True,
        "keep_group": data.get("keep_group") is True,
        "keep_enable": data.get("keep_enable") is True,
    }


def _action_import(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/booksource/import：三态预览，零落库。"""
    text = _import_text(data)
    try:
        return booksource_service.import_preview(text, **_keep_flags(data))
    except (BookSourceImportError, BookSourceFetchError) as err:
        raise _HttpError(400, _sanitize_error(err, ctx.root)) from None


def _action_import_apply(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/booksource/import_apply：按 selections 落库。

    selections 省略 = 按默认三态（isNew/isUpdate 全落、latest 跳过）。
    """
    text = _import_text(data)
    selections = data.get("selections")
    try:
        return booksource_service.import_apply(text, selections, **_keep_flags(data))
    except (BookSourceImportError, BookSourceFetchError) as err:
        raise _HttpError(400, _sanitize_error(err, ctx.root)) from None


def _action_refresh(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/booksource/refresh：刷新单条（url）或全部（url 省略）。

    force 默认 True——手动刷新即显式旁路间隔闸门（设计 §四 R0 行原文）。
    """
    url = data.get("url")
    force = data.get("force") is not False  # 缺省 True（手动语义）
    try:
        if isinstance(url, str) and url.strip():
            return booksource_service.refresh_subscription(url.strip(), force=force)
        return {"ok": True, "results": booksource_service.refresh_all(force=force)}
    except ValueError as err:
        raise _HttpError(400, _sanitize_error(err, ctx.root)) from None


def _action_subs_add(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/booksource/subs/add：新增/更新订阅（同 url upsert）。"""
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise _HttpError(400, "缺少必填字段 url（订阅地址）")
    name = data.get("name")
    if name is not None and not isinstance(name, str):
        raise _HttpError(400, "name 必须是字符串或省略")
    interval = data.get("update_interval_h", 0)
    if not isinstance(interval, int) or isinstance(interval, bool):
        raise _HttpError(400, "update_interval_h 必须是整数（小时；0=不限）")
    try:
        row = booksource_service.add_sub(
            url.strip(),
            name=name or "",
            update_interval_h=interval,
            silent_update=data.get("silent_update") is not False,
            auto_update=data.get("auto_update") is not False,
        )
    except (ValueError, BookSourceFetchError) as err:
        raise _HttpError(400, _sanitize_error(err, ctx.root)) from None
    return {"ok": True, "sub": row}


def _action_subs_del(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/booksource/subs/del：删除订阅。"""
    url = data.get("url")
    if not isinstance(url, str) or not url.strip():
        raise _HttpError(400, "缺少必填字段 url（订阅地址）")
    try:
        return booksource_service.del_sub(url.strip())
    except ValueError as err:
        raise _HttpError(400, _sanitize_error(err, ctx.root)) from None


# ── GET 面 ───────────────────────────────────────────────────────


def _booksource_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/booksource?group=&q=：索引列表（轻量投影）。"""
    return {
        "ok": True,
        "sources": booksource_service.list_sources(
            group=_query_str(query, "group") or "",
            q=_query_str(query, "q") or "",
        ),
    }


def _booksource_one_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/booksource/one?url=：单源详情（含规则组）。"""
    url = _query_str(query, "url")
    if url is None:
        raise _HttpError(400, "缺少必填参数 url")
    source = booksource_service.get_source(url)
    if source is None:
        raise _HttpError(404, f"书源不存在: {url}")
    return {"ok": True, "source": source}


def _booksource_subs_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/booksource/subs：订阅清单。"""
    return {"ok": True, "subs": booksource_service.list_subs()}


def _booksource_engine_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/booksource/engine：引擎状态（absent 是返回值不是异常）。"""
    return booksource_service.engine_status()


__all__ = [
    "_action_import",
    "_action_import_apply",
    "_action_refresh",
    "_action_subs_add",
    "_action_subs_del",
    "_booksource_payload",
    "_booksource_one_payload",
    "_booksource_subs_payload",
    "_booksource_engine_payload",
]
