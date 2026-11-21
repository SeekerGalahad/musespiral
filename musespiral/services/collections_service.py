"""手工集合单一持有者（第二十一期 §1.3/F62）——collections.json 读写。

`~/.musespiral/materials/collections.json`（整文件读-改-写，单人串行无竞态
窗口——正典 §四-Q4/Q5）：

  {"collections": [{
      "id": "sha1(name|created_at)[:12]",   # 稳定 id（同 make_material_id 散列风格）
      "name": "封面备选", "color": "jade",   # 徽色枚举=前端深构文 §1.2 语义槽五色
      "created_at": "ISO",
      "member_ids": ["a1b2c3d4e5f6", ...]    # 存 id 引用不嵌素材（引用不复制纪律）
  }]}

纪律（正典 §1.3）：
  - 墓碑惰性滤除：素材删除（墓碑）后集合**不回写**——读取时 member_ids
    对当前活跃集过滤；member_count=全长、active_member_count=滤除后
    （「n（含 m 已删）」三层可见性，非静默失效）；
  - 删除集合=仅解除组织关系，不删成员素材；
  - AI 不自动入集合（§〇 纪律 3）——本模块无任何 AI 写入路径。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .. import config
from ..jobs.store import now_iso as _now_iso

COLLECTIONS_FILE = "collections.json"
# 徽色枚举（正典 §1.3：前端深构文 §1.2 语义槽五色）
COLLECTION_COLORS: tuple[str, ...] = ("vermilion", "indigo", "jade", "amber", "slate")
MAX_NAME_CHARS = 24


def collections_path() -> Path:
    """集合索引路径：~/.musespiral/materials/collections.json（随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / "materials" / COLLECTIONS_FILE


def _load() -> list[dict[str, Any]]:
    """宽容读集合索引（缺失/坏 JSON/非预期形状 → 空表，不崩读侧）。"""
    try:
        data = json.loads(collections_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("collections") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and row.get("id"):
            out.append(row)
    return out


def _save(rows: list[dict[str, Any]]) -> None:
    """整文件写（原子：临时名+rename；父目录自建）。"""
    path = collections_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    try:
        tmp.write_text(
            json.dumps({"collections": rows}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _validate_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("集合名称必须是非空字符串")
    name = name.strip()
    if len(name) > MAX_NAME_CHARS:
        raise ValueError(f"集合名称上限 {MAX_NAME_CHARS} 字符: {len(name)}")
    return name


def _validate_color(color: Any) -> str:
    if color not in COLLECTION_COLORS:
        raise ValueError(f"color 必须是 {COLLECTION_COLORS} 之一: {color!r}")
    return color


def _active_material_ids() -> set[str]:
    """当前活跃素材 id 集（墓碑滤除的判定基准；services 层内导入解耦）。"""
    from . import materials_service

    return {m["id"] for m in materials_service.list_materials()}


def _validate_member_ids(member_ids: Any) -> list[str]:
    if not isinstance(member_ids, list) or any(not isinstance(x, str) for x in member_ids):
        raise ValueError("member_ids 必须是素材 id 字符串列表")
    known = _active_material_ids()
    ghosts = [x for x in member_ids if x not in known]
    if ghosts:
        raise ValueError(f"member_ids 含不存在（或已删）的素材 id: {ghosts[0]!r}")
    ordered: list[str] = []
    for mid in member_ids:
        if mid not in ordered:  # 去重保序
            ordered.append(mid)
    return ordered


def _with_counts(row: dict[str, Any], active_ids: set[str]) -> dict[str, Any]:
    """读取视图行：存储行 + 双计数（全长 / 墓碑滤除后——非静默缩数）。"""
    members = row.get("member_ids") or []
    return {
        **row,
        "member_count": len(members),
        "active_member_count": sum(1 for mid in members if mid in active_ids),
    }


def list_collections() -> list[dict[str, Any]]:
    """集合清单（创建序原样 + 双计数；宽容读不 500）。"""
    active = _active_material_ids()
    return [_with_counts(row, active) for row in _load()]


def get_collection(collection_id: str) -> dict[str, Any] | None:
    """取单条集合（含双计数）；不存在返回 None。"""
    active = _active_material_ids()
    for row in _load():
        if row.get("id") == collection_id:
            return _with_counts(row, active)
    return None


def create_collection(name: Any, color: Any) -> dict[str, Any]:
    """建集合：id=sha1(name|created_at)[:12]（同 make_material_id 散列风格）。"""
    name = _validate_name(name)
    color = _validate_color(color)
    created_at = _now_iso()
    row = {
        "id": hashlib.sha1(f"{name}|{created_at}".encode("utf-8")).hexdigest()[:12],
        "name": name,
        "color": color,
        "created_at": created_at,
        "member_ids": [],
    }
    rows = _load()
    rows.append(row)
    _save(rows)
    return _with_counts(row, _active_material_ids())


def add_members(collection_id: str, member_ids: Any) -> dict[str, Any]:
    """成员全量替换（提交后该集成员恰为 member_ids——与 triage 全量替换同口径）。"""
    members = _validate_member_ids(member_ids)
    rows = _load()
    for row in rows:
        if row.get("id") == collection_id:
            row["member_ids"] = members
            _save(rows)
            return _with_counts(row, _active_material_ids())
    raise ValueError(f"集合不存在: {collection_id!r}")


def remove_members(collection_id: str, member_ids: Any) -> dict[str, Any]:
    """成员差集移除（现成员 − member_ids）。"""
    if not isinstance(member_ids, list) or any(not isinstance(x, str) for x in member_ids):
        raise ValueError("member_ids 必须是素材 id 字符串列表")
    drop = set(member_ids)
    rows = _load()
    for row in rows:
        if row.get("id") == collection_id:
            row["member_ids"] = [mid for mid in (row.get("member_ids") or []) if mid not in drop]
            _save(rows)
            return _with_counts(row, _active_material_ids())
    raise ValueError(f"集合不存在: {collection_id!r}")


def rename_collection(collection_id: str, name: Any, color: Any = None) -> dict[str, Any]:
    """重命名（名必改；色可选改——色缺省保持现值）。"""
    name = _validate_name(name)
    rows = _load()
    for row in rows:
        if row.get("id") == collection_id:
            row["name"] = name
            if color is not None:
                row["color"] = _validate_color(color)
            _save(rows)
            return _with_counts(row, _active_material_ids())
    raise ValueError(f"集合不存在: {collection_id!r}")


def delete_collection(collection_id: str) -> dict[str, Any]:
    """删除集合=仅解除组织关系（成员素材不动）；返回被解除的成员数。"""
    rows = _load()
    kept = [row for row in rows if row.get("id") != collection_id]
    if len(kept) == len(rows):
        raise ValueError(f"集合不存在: {collection_id!r}")
    _save(kept)
    removed = next(row for row in rows if row.get("id") == collection_id)
    return {"deleted": collection_id, "member_count": len(removed.get("member_ids") or [])}


def active_member_ids(collection_id: str) -> set[str]:
    """某集合的活跃成员 id 集（墓碑惰性滤除；集合不存在 → 空集，宽容读）。"""
    active = _active_material_ids()
    for row in _load():
        if row.get("id") == collection_id:
            return {mid for mid in (row.get("member_ids") or []) if mid in active}
    return set()


def collections_of(material_id: str) -> list[dict[str, Any]]:
    """素材所属集合清单 [{id,name}]（detail 响应连坐声明面，只读）。"""
    return [
        {"id": row.get("id"), "name": row.get("name")}
        for row in _load()
        if material_id in (row.get("member_ids") or [])
    ]
