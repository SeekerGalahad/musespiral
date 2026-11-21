"""书源库存储（R0，L1）——§3.2 持有者三件套的唯一读写实现。

    ~/.musespiral/booksource_subs.json      订阅表（原子写）
    ~/.musespiral/booksources/index.json    轻量投影索引（原子写；派生缓存，坏行宽容）
    ~/.musespiral/booksources/<sha1前16>.json   规范 JSON **原样**（键名不可变）

纪律：
  - 原子写（tmp + os.replace）复用 infra/storage/filesystem.atomic_write_bytes
    单一实现，不另写副本；写失败清理 tmp 不留半写产物；
  - 单源文件按 sha1(bookSourceUrl) 前 16 位命名——url 含 `:` `/` 等不可
    作文件名，且这样重命名/大小写差异天然归一到同一槽位；
  - 本模块只做落盘/读盘，不裁决合并语义（归 services/booksource_service）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import config
from .storage.filesystem import atomic_write_bytes

# 持有者文件名（config.home_dir() 下）
SUBS_FILE = "booksource_subs.json"
SOURCES_DIR = "booksources"
INDEX_FILE = "index.json"


def subs_path() -> Path:
    """订阅表路径（随 MUSESPIRAL_HOME 解析——测试 monkeypatch env 即隔离）。"""
    return config.home_dir() / SUBS_FILE


def sources_dir() -> Path:
    """书源库目录（非单文件——书源 JSON 可能数百 KB/个）。"""
    return config.home_dir() / SOURCES_DIR


def index_path() -> Path:
    """轻量索引路径。"""
    return sources_dir() / INDEX_FILE


def source_file(url: str) -> Path:
    """单源文件路径：sha1(bookSourceUrl) 前 16 位 hex（见模块 docstring）。

    url 含 `:` `/` 等不可作文件名的字符，散列后重命名/大小写差异天然归一到
    同一槽位。**本模块是该命名规则的唯一实现**（core 层不感知落盘细节）。
    """
    key = hashlib.sha1(url.strip().encode("utf-8")).hexdigest()[:16]
    return sources_dir() / f"{key}.json"


def _write_json(path: Path, obj: Any) -> Path:
    """JSON 原子写（父目录自建）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return path


def _read_json(path: Path) -> Any:
    """JSON 宽容读：缺失/坏 JSON → None（人工可编辑文件，读侧永不崩）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


# ── 订阅表 ────────────────────────────────────────────────────────


def load_subs() -> list[dict[str, Any]]:
    """订阅表读（宽容；坏行丢弃）。"""
    raw = _read_json(subs_path())
    rows = raw.get("subs") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        out.append(_normalize_sub(row))
    return out


def _normalize_sub(row: dict[str, Any]) -> dict[str, Any]:
    """订阅行归一（缺键补默认，类型宽容；url 已保证非空）。"""
    out = dict(row)
    out["url"] = str(row["url"]).strip()
    sid = row.get("id")
    out["id"] = sid if isinstance(sid, str) and sid.strip() else out["url"]
    name = row.get("name")
    out["name"] = name.strip() if isinstance(name, str) and name.strip() else out["url"]
    out["type"] = 0  # RuleSub type: 0 书源（本域固定；RSS/替换规则非本书语义）
    out["auto_update"] = bool(row.get("auto_update", True))
    interval = row.get("update_interval_h", 0)
    out["update_interval_h"] = (
        interval if isinstance(interval, int) and not isinstance(interval, bool) and interval >= 0 else 0
    )
    out["silent_update"] = bool(row.get("silent_update", True))
    last_fetch = row.get("last_fetch")
    out["last_fetch"] = last_fetch if isinstance(last_fetch, (int, float)) else None
    state = row.get("fetch_state")
    out["fetch_state"] = state if state in ("ok", "failed", "pending") else "pending"
    err = row.get("error")
    out["error"] = err if isinstance(err, str) else ""
    return out


def save_subs(subs: list[dict[str, Any]]) -> Path:
    """订阅表原子写（{version, subs:[...]}）。"""
    return _write_json(subs_path(), {"version": 1, "subs": list(subs)})


# ── 索引（轻量投影）──────────────────────────────────────────────
# 本模块只做结构性读写；索引行归一归 core.booksource.model（L2），
# 服务层组合两者（infra 不上探 core 是架构门 rule4）。


def load_index() -> list[dict[str, Any]]:
    """索引原样读（只丢非 dict 行；字段归一见 service）。"""
    raw = _read_json(index_path())
    rows = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def save_index(rows: list[dict[str, Any]]) -> Path:
    """索引原子写（{version, sources:[...]}）。"""
    return _write_json(index_path(), {"version": 1, "sources": list(rows)})


# ── 单源规范 JSON（原样——键名不可变的物理面）────────────────────


def load_source(url: str) -> dict[str, Any] | None:
    """读单源规范 JSON（缺失/坏 → None；不做归一——原样返回保 round-trip）。"""
    raw = _read_json(source_file(url))
    return raw if isinstance(raw, dict) else None


def save_source(source: dict[str, Any]) -> Path:
    """写单源规范 JSON（原样 dump；url 必填校验归调用方/core）。"""
    url = source.get("bookSourceUrl")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("bookSourceUrl 缺失或为空")
    return _write_json(source_file(url), source)


def delete_source(url: str) -> bool:
    """删单源文件（不存在返回 False——幂等）。"""
    path = source_file(url)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True
