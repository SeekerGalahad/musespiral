"""AI 输出缓存（Phase D F79.1，L1 纯文件态）。

**key 口径（设计 §二 F79.1 精确公式）**：

    key = sha1("{task}|{profile_name}|{temperature}|{template_sha}|{prompt_sha}").hexdigest()[:16]

五元组任一变 → 新 key；**不做部分命中/不做前缀匹配**（全 key 精确相等，
语义最简）。各分量口径：

- `profile_name`  路由解析后的**生效档案名**（`profiles.resolve_profile_name`）——
                  换档案/换模型自然失效，旧模型产出不会被新模型误读；
- `temperature`   `float` 的 `repr` 归一（`0.6` 与 `0.60` 同 key——两者
                  `repr()` 都是 `'0.6'`，这正是选 repr 而非 `"%f"` 的理由）；
- `template_sha`  sha1(生效模板文本)——模板/覆盖文件一改，该 task 全部缓存
                  自然失效（key 变=永不命中，旧条目等 LRU 淘汰）；
- `prompt_sha`    sha1(`json.dumps(messages, ensure_ascii=False)`)——**保持
                  插入序，不 sort_keys**（role/content 序列即语义）。

**条目形状**（`~/.musespiral/cache/ai/<key>.json`）：
`{key, task, profile, temperature, template_sha, text, created_at, chars}`
——**只有 text（输出正文）**，**不存 prompt 原文**（红线：治理文件无 prompt
正文；重放所需的一切都已在 key 里，prompt 可随时重建）。

**生命周期**：无时间 TTL（正确性由五元组 key 保证，陈旧性由 `template_sha`
捕获，不靠时钟）；条目上限 `MAX_ENTRIES=200`，超限按 `created_at` 淘汰最旧；
`clear()` 全清零副作用（派生状态非资产——重建=重调 chat）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ... import config
from ..storage.filesystem import atomic_write_bytes

# 缓存目录（随 MUSESPIRAL_HOME 解析）
CACHE_DIR = "cache/ai"

# 条目上限（设计 §1.2/§二 F79.1：200 条，LRU by created_at）
MAX_ENTRIES = 200

# 条目键集（结构断言面：绝不含 prompt 正文/密钥字段）
ENTRY_KEYS: tuple[str, ...] = (
    "key", "task", "profile", "temperature", "template_sha", "text", "created_at", "chars",
)


def cache_dir() -> Path:
    """缓存目录 `~/.musespiral/cache/ai`（每次现读 config——MUSESPIRAL_HOME 隔离即时生效）。"""
    return config.home_dir() / CACHE_DIR


def prompt_sha(messages: list[dict[str, str]]) -> str:
    """prompt 摘要：`json.dumps(messages, ensure_ascii=False)` 的 sha1（保插入序）。"""
    payload = json.dumps(messages, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def template_sha(template: str | None) -> str:
    """模板摘要：生效模板文本的 sha1（None/空 → 空串占位，仍参与 key 计算）。"""
    return hashlib.sha1((template or "").encode("utf-8")).hexdigest()


def cache_key(
    *,
    task: str,
    profile: str | None,
    temperature: float,
    template_sha_value: str,
    prompt_sha_value: str,
) -> str:
    """五元组 → 16 位十六进制 key（设计 §二 F79.1 公式的唯一实现）。

    `temperature` 用 `repr(float(...))` 归一：`0.6` 与 `0.60` 都得到 `'0.6'`。
    `profile` 为 None 时按空串参与（未配置档案的一次调用也有稳定 key）。
    """
    joined = "|".join(
        (
            str(task),
            str(profile or ""),
            repr(float(temperature)),
            str(template_sha_value),
            str(prompt_sha_value),
        )
    )
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def entry_path(key: str) -> Path:
    """条目路径；key 形状非法（非 16 位十六进制）→ ValueError（防路径穿越）。"""
    if not isinstance(key, str) or len(key) != 16 or any(c not in "0123456789abcdef" for c in key):
        raise ValueError(f"缓存 key 必须是 16 位十六进制: {key!r}")
    return cache_dir() / f"{key}.json"


# ── 读 ───────────────────────────────────────────────────────────────


def get(key: str) -> dict[str, Any] | None:
    """读一个条目；缺失/坏 JSON/形状不对 → None（缓存不可靠即视为 miss）。"""
    try:
        path = entry_path(key)
    except ValueError:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None
    return raw


def read_text(key: str) -> str | None:
    """只取命中正文（gateway 的调用面）；未命中 → None。"""
    entry = get(key)
    return entry["text"] if entry is not None else None


# ── 写 ───────────────────────────────────────────────────────────────


def _iter_entries() -> list[tuple[str, dict[str, Any]]]:
    """枚举目录内全部合法条目 [(key, entry)]；坏文件跳过不删（读侧宽容）。"""
    d = cache_dir()
    try:
        paths = sorted(d.glob("*.json"))
    except OSError:
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and isinstance(raw.get("text"), str):
            out.append((path.stem, raw))
    return out


def evict(max_entries: int = MAX_ENTRIES) -> int:
    """LRU 淘汰：条目数超上限时按 `created_at` 升序删最旧，返回删除数。

    `created_at` 缺失/非字符串的条目排最前（当作最旧——形状残缺优先淘汰）。
    """
    entries = _iter_entries()
    overflow = len(entries) - max_entries
    if overflow <= 0:
        return 0
    entries.sort(key=lambda kv: str(kv[1].get("created_at") or ""))
    removed = 0
    for key, _entry in entries[:overflow]:
        try:
            entry_path(key).unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            continue
    return removed


def put(
    *,
    key: str,
    task: str,
    profile: str | None,
    temperature: float,
    template_sha_value: str,
    text: str,
    created_at: str,
) -> dict[str, Any]:
    """写一个条目（原子写 tmp+replace）并触发 LRU 淘汰；返回落盘条目。

    `created_at` 由调用方给（cache 无时钟——时间源留在 gateway，便于测试
    注入确定时间戳）。
    """
    path = entry_path(key)
    entry = {
        "key": key,
        "task": task,
        "profile": profile or "",
        "temperature": float(temperature),
        "template_sha": template_sha_value,
        "text": text,
        "created_at": created_at,
        "chars": len(text),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(entry, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, data.encode("utf-8"))
    evict()
    return entry


# ── 运维面（stats / clear）──────────────────────────────────────────


def stats() -> dict[str, Any]:
    """条目概览：总数/按 task 分布/最旧条目时间（`musespiral ai cache --stats`）。"""
    entries = _iter_entries()
    by_task: dict[str, int] = {}
    oldest: str | None = None
    total_chars = 0
    for _key, entry in entries:
        task = str(entry.get("task") or "")
        by_task[task] = by_task.get(task, 0) + 1
        chars = entry.get("chars")
        total_chars += chars if isinstance(chars, int) else len(str(entry.get("text") or ""))
        created = entry.get("created_at")
        if isinstance(created, str) and (oldest is None or created < oldest):
            oldest = created
    return {
        "entries": len(entries),
        "max_entries": MAX_ENTRIES,
        "by_task": dict(sorted(by_task.items())),
        "oldest": oldest,
        "chars": total_chars,
        "dir": str(cache_dir()),
    }


def clear() -> int:
    """全清缓存目录，返回删除条目数（**零副作用**：删的是派生状态非资产）。"""
    entries = _iter_entries()
    removed = 0
    for key, _entry in entries:
        try:
            entry_path(key).unlink(missing_ok=True)
            removed += 1
        except (OSError, ValueError):
            continue
    return removed
