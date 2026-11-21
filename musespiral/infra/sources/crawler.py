"""爬虫首切片数据面（B 采集扩展 §二 F67 + 任务书裁决形态）——L1 infra。

F67 §〇 边界：**无自动抓取**——本模块只有「注册表 + 手动粘贴端点」的
数据面（pending 区/去重指纹），一切抓取动作由人工触发（确认闸绝对），
出网统一走 infra/fetcher 咽喉（编排归 services/ingest_service）。

pending 区（~/.musespiral/crawler_pending.jsonl，追加式/同 id 末次胜出——
与 materials.jsonl 同纪律）：
  {"id": url_sha256 前 12 位, "url_sha256", "content_sha"(前 500 字 sha),
   "url", "title", "text", "confidence", "word_count", "license",
   "license_note", "robots", "created", "status": "pending"|"adopted"|"dup"}

去重（任务书裁决口径）：URL sha256 + 内容指纹（前 500 字 sha）——同一
URL 已在素材库（或同内容 pending 未决）→ 标 dup 不入库。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from ... import config
from ...jobs.store import now_iso as _now_iso

PENDING_FILE = "crawler_pending.jsonl"

# 单写者串行（Studio 双 worker 与 tick 共用；与 jobs/materials 同纪律）
_LOCK = threading.Lock()


def pending_path() -> Path:
    """pending 注册表路径（随 MUSESPIRAL_HOME 解析——测试 monkeypatch env 即隔离）。"""
    return config.home_dir() / PENDING_FILE


# ── 指纹（任务书去重口径：URL sha256 + 前 500 字内容 sha）───────────


def url_sha256(url: str) -> str:
    """URL 全串 sha256（hex，64 位）。"""
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def content_sha(text: str) -> str:
    """内容指纹：正文前 500 字 sha256（hex）。"""
    return hashlib.sha256((text or "")[:500].encode("utf-8")).hexdigest()


# ── 注册表读写（追加式 JSONL，同 id 末次胜出）───────────────────────


def _normalize(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    rid = row.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None
    return dict(row)


def read_index() -> dict[str, dict[str, Any]]:
    """读 pending 全量索引：{id: 行}（文件序末次胜出；坏行跳过不崩）。"""
    path = pending_path()
    if not path.exists():
        return {}
    index: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = _normalize(json.loads(line))
        except json.JSONDecodeError:
            continue
        if row is not None:
            index[row["id"]] = row
    return index


def _append(row: dict[str, Any]) -> None:
    path = pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def register_pending(
    *,
    url: str,
    title: str,
    text: str,
    confidence: float,
    word_count: int,
    license_name: str,
    license_note: str,
    robots: dict[str, Any],
    status: str = "pending",
) -> dict[str, Any]:
    """落一条 pending 注册行（同 URL 重爬=同 id 追加，末次胜出）。"""
    row = {
        "id": url_sha256(url)[:12],
        "url_sha256": url_sha256(url),
        "content_sha": content_sha(text),
        "url": url,
        "title": title,
        "text": text,
        "confidence": confidence,
        "word_count": word_count,
        "license": license_name,
        "license_note": license_note,
        "robots": robots,
        "created": _now_iso(),
        "status": status,
    }
    with _LOCK:
        _append(row)
    return row


def list_pending() -> list[dict[str, Any]]:
    """status=="pending" 的待决行（最新在前；体积面裁掉正文长文本）。"""
    rows = [r for r in read_index().values() if r.get("status") == "pending"]
    rows.sort(key=lambda r: str(r.get("created") or ""), reverse=True)
    return rows


def find_by_url(url: str) -> dict[str, Any] | None:
    """按 URL sha 找注册行（任意状态）。"""
    return read_index().get(url_sha256(url)[:12])


def mark_status(pending_id: str, status: str) -> dict[str, Any] | None:
    """追加状态翻转行（adopted/dup——同 id 末次胜出表达状态迁移）。"""
    with _LOCK:
        row = read_index().get(pending_id)
        if row is None:
            return None
        row = {**row, "status": status}
        _append(row)
        return row
