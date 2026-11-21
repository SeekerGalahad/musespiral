"""AI 用量台账（Phase D F79，L1）。

`~/.musespiral/ai_usage.jsonl` 追加式 JSONL，**只记计量不记内容**（设计 §1.2）：

    行 = {ts, task, profile, cache_hit, chars_in, chars_out, run_id?}

**红线（设计 §二 F79.6）**：无 prompt 正文、无 api_key、无输出文本。
`ROW_KEYS` 是结构 schema 断言面——测试对写出的每行断言键集合**恒等于**
该元组（不含 run_id 时）或其子集加 run_id；canary 注入后全文件 grep 零命中。

**为什么是 JSONL 追加而非聚合计数**：日汇总是**读侧现算**（`summarize`），
不回写、不落中间状态。这样并发写（CLI + Studio 双入口，设计 §四-Q5
「append 末次胜出」）不需要任何锁，坏行也只污染自己那一行。

**为什么「命中只记计量不出账」**：`cache_hit:true` 的行照写（可审计「这次
复跑花了多少钱=0」），但 `summarize` 的 calls/chars 合计**只统计未命中行**
——预算与成本统计的口径因此天然一致（缓存的意义即免计费复跑）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import config
from ..storage.jsonl import read_rows

USAGE_FILE = "ai_usage.jsonl"

# 行键集（schema 断言面：绝不含 prompt/text/api_key 字段；run_id 可选）
ROW_KEYS: tuple[str, ...] = (
    "ts", "task", "profile", "cache_hit", "chars_in", "chars_out", "run_id",
)


def usage_path() -> Path:
    """台账路径 `~/.musespiral/ai_usage.jsonl`（随 MUSESPIRAL_HOME 解析）。"""
    return config.home_dir() / USAGE_FILE


def append(
    *,
    ts: str,
    task: str,
    profile: str | None,
    cache_hit: bool,
    chars_in: int,
    chars_out: int,
    run_id: str | None = None,
) -> dict[str, Any]:
    """追加一行计量（**只接受计量字段**——正文与密钥无处可传，红线在签名上）。

    `ts` 由调用方给（本模块无时钟）；`run_id` 省略时该键不落盘（编排步
    才有关联 run 的必要）。
    """
    row: dict[str, Any] = {
        "ts": str(ts),
        "task": str(task),
        "profile": str(profile or ""),
        "cache_hit": bool(cache_hit),
        "chars_in": max(int(chars_in or 0), 0),
        "chars_out": max(int(chars_out or 0), 0),
    }
    if run_id is not None:
        row["run_id"] = str(run_id)
    path = usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_all() -> list[dict[str, Any]]:
    """宽容读全部行（走 infra/storage/jsonl.read_rows 单一权威口径）。"""
    return read_rows(usage_path())


def _is_hit(row: dict[str, Any]) -> bool:
    return row.get("cache_hit") is True


def today_summary(ts_prefix: str) -> dict[str, Any]:
    """某日（`ts` 前缀，形如 `2026-11-17`）的用量：**只统计未命中行**。

    返回 `{calls, out_chars, hits}`：
      - `calls` / `out_chars` = 未命中行（真正出账的那部分——预算对账口径）
      - `hits` = 命中行数（计量展示用，不进预算）
    """
    calls = 0
    out_chars = 0
    hits = 0
    for row in read_all():
        ts = row.get("ts")
        if not isinstance(ts, str) or not ts.startswith(ts_prefix):
            continue
        if _is_hit(row):
            hits += 1
            continue
        calls += 1
        try:
            out_chars += int(row.get("chars_out") or 0)
        except (TypeError, ValueError):
            continue
    return {"calls": calls, "out_chars": out_chars, "hits": hits}


def task_summary(task: str, ts_prefix: str) -> dict[str, Any]:
    """某任务某日的用量（`per_task` 覆盖全局预算的对账面）。"""
    calls = 0
    out_chars = 0
    hits = 0
    for row in read_all():
        if row.get("task") != task:
            continue
        ts = row.get("ts")
        if not isinstance(ts, str) or not ts.startswith(ts_prefix):
            continue
        if _is_hit(row):
            hits += 1
            continue
        calls += 1
        try:
            out_chars += int(row.get("chars_out") or 0)
        except (TypeError, ValueError):
            continue
    return {"calls": calls, "out_chars": out_chars, "hits": hits}


def summarize(days: int = 7, *, today: str | None = None) -> dict[str, Any]:
    """日汇总表（`musespiral ai usage --days N` 的数据面，读侧现算不回写）。

    `days` 个自然日（含今天）逐「任务×档案」聚合：
    `{days, rows: [{date, task, profile, calls, hits, hit_rate, chars_in,
    chars_out}], totals: {calls, hits, chars_in, chars_out}}`。
    `calls`/`chars_*` 只计未命中行；`hits` 与 `hit_rate` 展示命中情况。
    """
    from datetime import date, timedelta

    end = date.fromisoformat(today) if today else date.today()
    window = [(end - timedelta(days=i)).isoformat() for i in range(max(days, 0) - 1, -1, -1)]
    wanted = set(window)

    agg: dict[tuple[str, str, str], dict[str, int]] = {}
    for row in read_all():
        ts = row.get("ts")
        if not isinstance(ts, str) or ts[:10] not in wanted:
            continue
        key = (ts[:10], str(row.get("task") or ""), str(row.get("profile") or ""))
        slot = agg.setdefault(key, {"calls": 0, "hits": 0, "chars_in": 0, "chars_out": 0})
        if _is_hit(row):
            slot["hits"] += 1
            continue
        slot["calls"] += 1
        for field in ("chars_in", "chars_out"):
            try:
                slot[field] += int(row.get(field) or 0)
            except (TypeError, ValueError):
                continue

    rows: list[dict[str, Any]] = []
    totals = {"calls": 0, "hits": 0, "chars_in": 0, "chars_out": 0}
    for (day, task, profile), slot in sorted(agg.items()):
        total = slot["calls"] + slot["hits"]
        rows.append(
            {
                "date": day,
                "task": task,
                "profile": profile,
                "calls": slot["calls"],
                "hits": slot["hits"],
                "hit_rate": round(slot["hits"] / total, 3) if total else 0.0,
                "chars_in": slot["chars_in"],
                "chars_out": slot["chars_out"],
            }
        )
        for field in totals:
            totals[field] += slot[field]
    return {"days": max(days, 0), "window": window, "rows": rows, "totals": totals}
