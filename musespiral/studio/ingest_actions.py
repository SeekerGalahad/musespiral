"""Studio 采集扩展动作（B 采集扩展 §二 F65/F66/F67 + 任务书端点裁决形态）。

四端点（POST 三 + GET 一；全部经 server 级 confirm 闸门）：
  - POST /api/ingest/refresh {source_id}：单源刷新进 job（kind=ingest，
    复用 refresh_source 串行锁与 rss 分派）；
  - POST /api/ingest/clip {action: preview|adopt, …}：剪藏两形态——
    preview（网络长动作 → job kind=clip，结果含正文随台账留痕，素材库
    零行）与 adopt（同步瞬时，人工确认落库）；
  - POST /api/ingest/crawl {url}：手动粘贴爬取 → job kind=clip →
    fetch+extract → crawler pending 区（无自动抓取，确认闸绝对）；
  - GET /api/ingest/status：采集面状态（源清单+健康/last_refresh、pending
    待决区、串行锁占用）。

URL 提交期形状门 = fetcher.assert_public_url（与取数期同一道门）：
IP 字面量/userinfo/非 80/443 端口 → 400 且零网络请求（E2E-5）。
"""
from __future__ import annotations

from typing import Any

from ..infra import fetcher
from .security import _HttpError, _StudioContext, _error_payload, _require_str


def _gate_url(data: dict[str, Any], key: str = "url") -> str:
    """提交期 URL 形状门（fetcher 门同源；拒 → 400，零网络请求）。"""
    url = _require_str(data, key)
    try:
        return fetcher.assert_public_url(url)
    except fetcher.RefusedError as err:
        raise _HttpError(400, str(err)) from None


def _action_ingest_refresh(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/ingest/refresh {source_id}：单源刷新进 job（kind=ingest）。"""
    from ..services import ingest_service
    from .actions import _submit  # 函数体内导入（导入期解耦，fetch_actions 判例）

    sid = _require_str(data, "source_id")

    def _body(_job_id: str) -> dict[str, Any]:
        return ingest_service.refresh_source(sid, articles_root=ctx.root)

    return _submit(ctx, "ingest", {"source_id": sid}, _body)


def _action_ingest_clip(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/ingest/clip：preview（job kind=clip，零素材落盘）/ adopt（同步落库）。"""
    from ..services import ingest_service
    from .actions import _submit  # 函数体内导入（导入期解耦）

    action = data.get("action")
    if action == "preview":
        url = _gate_url(data)

        def _body(_job_id: str) -> dict[str, Any]:
            return ingest_service.clip_url(url)

        return _submit(ctx, "clip", {"action": "preview", "url": url}, _body)
    if action == "adopt":
        url = _gate_url(data)
        title = _require_str(data, "title")
        mode = data.get("mode")
        if mode not in ("crawled", "link"):
            raise _HttpError(400, "mode 必须是 crawled/link 之一")
        text = data.get("text")
        if not isinstance(text, str):
            raise _HttpError(400, "text 必须是字符串（预览正文原样回传，可经人工编辑）")
        if mode == "crawled" and not text.strip():
            raise _HttpError(400, "mode=crawled 时 text 不能为空白（降级存链接请用 mode=link）")
        confidence = data.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise _HttpError(400, "confidence 必须是 0-1 的数值")
        word_count = data.get("word_count", 0)
        if not isinstance(word_count, int) or isinstance(word_count, bool) or word_count < 0:
            raise _HttpError(400, "word_count 必须是非负整数")
        license_name = data.get("license")
        if license_name is not None and license_name not in ("CC-BY", "ALL-RIGHTS", "UNKNOWN"):
            raise _HttpError(400, "license 必须是 CC-BY/ALL-RIGHTS/UNKNOWN 之一（人工可改）")
        license_note = data.get("license_note", "")
        if not isinstance(license_note, str):
            raise _HttpError(400, "license_note 必须是字符串")
        pending_id = data.get("pending_id")
        if pending_id is not None and not isinstance(pending_id, str):
            raise _HttpError(400, "pending_id 必须是字符串或 null")
        try:
            result = ingest_service.adopt_clip(
                url=url,
                title=title,
                text=text,
                mode=str(mode),
                confidence=float(confidence),
                word_count=word_count,
                license_name=str(license_name) if license_name else None,
                license_note=license_note,
                pending_id=pending_id,
            )
        except ValueError as exc:  # add_many immutable 冲突等业务拒绝 → {ok:false}
            return _error_payload(exc, ctx.root)
        return {"ok": True, "material": result["material"], "mode": result["mode"],
                "added": result["added"], "skipped": result["skipped"]}
    raise _HttpError(400, "action 必须是 preview/adopt 之一")


def _action_ingest_crawl(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/ingest/crawl {url}：手动粘贴 → fetch+extract → pending 区。"""
    from ..services import ingest_service
    from .actions import _submit  # 函数体内导入（导入期解耦）

    url = _gate_url(data)

    def _body(_job_id: str) -> dict[str, Any]:
        return ingest_service.crawl_manual(url)

    return _submit(ctx, "clip", {"url": url, "mode": "crawl_manual"}, _body)


def _ingest_status_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/ingest/status：源健康/last_refresh + pending 待决区 + 锁占用。"""
    from ..infra.sources import crawler
    from ..services import ingest_service, sources_service

    sources = [
        {
            "id": s["id"],
            "type": s.get("type"),
            "enabled": s.get("enabled") is True,
            "health": s.get("health") or {},
        }
        for s in sources_service.list_sources()
    ]
    pending = crawler.list_pending()
    return {
        "ok": True,
        "sources": sources,
        "pending": pending,
        "pending_count": len(pending),
        "busy": ingest_service._REFRESH_LOCK.locked(),
    }
