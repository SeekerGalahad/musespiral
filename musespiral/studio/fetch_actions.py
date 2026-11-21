"""Studio 抓取 POST 动作（正典 §八 Phase A「actions 拆出 fetch（抓取动作）」，
行为零变更）：提交期 SSRF 门（actions._assert_fetch_url，400 契约）→ 建
kind="fetch" job，执行体在 services.fetch_service.fetch_to_draft。

actions 顶部导入本模块装配路由表；_assert_fetch_url/_runner 延迟导入——
反向顶部互导会成环（与 payload/scan 的既有纪律一致）。
"""
from __future__ import annotations

from typing import Any

from ..services import fetch_service
from .security import _StudioContext, _rel, _require_str


def _action_fetch(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/fetch {url, confirm:true}：从微信文章 URL 抓取草稿。

    提交期过 _assert_fetch_url（SSRF 白名单，400 契约）→ kind="fetch" job；
    执行体 fetch_service.fetch_to_draft（slug 净化/O_EXCL 冲突递增后缀在
    服务层），产物 root/<slug>.md（frontmatter status: draft 入队可见）。
    job 提交走 actions._submit 统一件（异常→JobFailure 已脱敏）。
    """
    from .actions import _assert_fetch_url, _submit

    url = _require_str(data, "url")
    _assert_fetch_url(url)

    def _body(_job_id: str) -> dict[str, Any]:
        dest = fetch_service.fetch_to_draft(url, ctx.root)
        return {"output": _rel(ctx.root, dest)}

    return _submit(ctx, "fetch", {"url": url}, _body)
