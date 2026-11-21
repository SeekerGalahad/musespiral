"""Studio GET /api/topic_radar 载荷（Phase D F78.4 读面；契约 §3.6-5）。

只读 latest.json——触发面=CLI（musespiral topic-radar，job kind=topic_radar），
本端点零触发零写面（M0 零前端字节；POST 触发端点属远景须再裁决）。
无快照 → {ok:true, snapshot:null} 空态（对齐 GET /api/discover 空
candidates 惯例：不 404 不 500，空态引导跑一次 CLI）。
"""
from __future__ import annotations

from typing import Any

from ..services import topic_radar_service
from .security import _StudioContext


def _topic_radar_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/topic_radar：{ok:true, snapshot:{...}|null}。"""
    return {"ok": True, "snapshot": topic_radar_service.latest()}


__all__ = ["_topic_radar_payload"]
