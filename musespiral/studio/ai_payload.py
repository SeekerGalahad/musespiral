"""Studio F1 AI 治理读面（两个 GET，零写面）。

  - GET /api/ai/usage?days=7：`ai_usage.jsonl` 读侧现算（infra/ai/usage.
    summarize 单一权威——与 CLI `musespiral ai usage` **同数同口径**，任务×档案
    聚合 调用/命中/命中率/chars）。days ∈[1,90] 与 CLI IntRange 同限。
  - GET /api/ai/budget：三层预算读（infra/ai/budget.load——全局
    daily_calls/daily_out_chars + per_task 覆盖，缺省兜底零配置可用）。

设置面 AI 域「近 7 日用量」pbar 行+预算行的数据面（F1）；零 secret
（usage 行本就无 prompt/key，budget 纯数值）。
"""
from __future__ import annotations

from typing import Any

from ..infra.ai import budget as budget_mod
from ..infra.ai import usage as usage_mod
from .security import _HttpError, _StudioContext


def _ai_usage_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/ai/usage?days=7 → summarize 原样（+ok 布尔）。

    days 缺省 7；非整数/超 [1,90] → 400（CLI IntRange(1,90) 同限）。
    """
    raw_days = query.get("days") or []
    days_text = raw_days[-1].strip() if raw_days else "7"
    try:
        days = int(days_text)
    except ValueError:
        raise _HttpError(400, f"days 必须是整数（1-90）: {days_text!r}") from None
    if not 1 <= days <= 90:
        raise _HttpError(400, f"days 必须在 1-90 之间: {days}")
    return {"ok": True, **usage_mod.summarize(days=days)}


def _ai_budget_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/ai/budget → {ok, budget:{daily_calls, daily_out_chars, per_task}}。"""
    return {"ok": True, "budget": budget_mod.load()}
