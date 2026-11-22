"""materials 包：triage（分诊窄门 + 标签频次 + 置信度判别 + KB 星标）。

原 materials_service.py 按 ARCH-2 拆出的分诊簇：update_triage（triage
三键窄门 + 追加全量新行——immutable 红线下唯一的人工改分诊面）、
_confidence_of（AI 自评置信度的宽容读）、tag_counts（F63 标签频次表）、
set_kb_starred（档A discover/star 动作面下沉，M4 起读写 materials）与
migrate_p19_stars/_load_p19_stars（迁移件二：P19 星标 → triage.starred，
幂等）。
"""
from __future__ import annotations

import json
from typing import Any

from ... import config
from ...core.materials import model
from ...core.materials import triage  # core.materials.triage（域层分诊谓词/窄门）
from ...jobs.store import now_iso as _now_iso
from .crud import STARS_FILE, _append, add_material, get_material, list_materials


def update_triage(material_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """分诊更新：triage 窄门（core.triage.triage_patch 五键——三布尔+tags+
    verified，confidence 不入人工窄门）+ 追加全量新行。

    新行=当前行原样 + 补丁后 triage（payload/provenance 逐字节不动——
    immutable 由「只改 triage 后整行重写」的构造路径保证）；同 id 末次
    胜出读序下旧行自然失效。素材不存在/已删 → ValueError。
    """
    target = get_material(material_id)
    if target is None:
        raise ValueError(f"素材不存在或已删: {material_id!r}")
    new_triage = triage.triage_patch(target.get("triage") or {}, changes)
    if new_triage == target.get("triage"):
        return target  # 零变化零追加（幂等）
    row = {**target, "triage": new_triage}
    _append(row)
    return row


def _confidence_of(material: dict[str, Any]) -> float | None:
    """素材的 AI 自评置信度（归一后恒合法；防御性再归一供纯读路径）。"""
    value = (material.get("triage") or {}).get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if 0.0 <= value <= 1.0 else None


def tag_counts() -> list[dict[str, Any]]:
    """标签频次表（F63：全库现算，count 降序、同频按名升序——确定性终值）。"""
    counts: dict[str, int] = {}
    for material in list_materials():
        tags = (material.get("triage") or {}).get("tags")
        if not isinstance(tags, list):
            continue  # 宽容读：脏数据按无标签
        for tag in tags:
            if isinstance(tag, str) and tag:
                counts[tag] = counts.get(tag, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ── KB 收藏星标（档A discover/star 动作面；M4 起读写 materials）─────


def set_kb_starred(
    path_rel: str, starred: bool, *, source_id: str = "kb"
) -> dict[str, Any]:
    """收藏/取消收藏一条 KB 引用（引用不复制：payload.local_path 指回）。

    与 migrate_p19_stars 同语义（原 studio/actions._action_discover_star 的
    业务逻辑下沉，行为零变更）：素材在册 → update_triage；不在册且收藏 →
    落一条 kb 引用素材（triage.starred=True）；取消收藏对不在册素材零动作。
    KB 根界检查归表现层（越界/绝对路径 → 400 先于本函数）。

    返回 {"starred": bool, "ts": str}——收藏时 ts=回写行 fetched_at，
    取消收藏 ts=""（与原动作响应逐键一致）。
    """
    mid = model.make_material_id(source_id, "text", path_rel)
    existing = get_material(mid)
    if starred:
        if existing is None:
            add_material(
                model.make_material(
                    mtype="text",
                    source_id=source_id,
                    uri=path_rel,
                    title="",
                    payload={"local_path": path_rel},
                    triage={"read": False, "starred": True, "enabled": True, "tags": []},
                    fetched_at=_now_iso(),
                )
            )
        else:
            update_triage(mid, {"starred": True})
        row = get_material(mid)
        ts = str((row or {}).get("fetched_at") or "")
    else:
        if existing is not None:
            update_triage(mid, {"starred": False})
        ts = ""
    return {"starred": starred, "ts": ts}


# ── 迁移件二：P19 星标 → triage.starred（幂等）─────────────────────


def migrate_p19_stars(kb_source_id: str = "kb") -> dict[str, Any]:
    """discover_stars.json 迁移到 triage.starred（迁移三件之二，M4 执行）。

    幂等以「每颗星标的语义已体现在素材库」为完成标志——重复执行零追加
    （任务约束：迁移幂等=重复执行零重复行）：

      - uri 已有活跃素材且 starred=True → 跳过（零追加）；
      - uri 已有活跃素材未星标 → update_triage 追加 starred 行（二跑
        命中上一条分支，不再追加）；
      - uri 无素材 → 落一条 kb 引用素材（type=text、origin.source_id=
        kb 源 id、payload.local_path 指回——引用不复制红线），fetched_at
        取星标时间戳（收藏时序可追溯）；二跑时 add_material 幂等收敛。

    旧文件 discover_stars.json 只读不删（端点改读写 materials 归 M2
    收编切换；档A actions 微端点在切换前继续可用）。
    """
    stars = _load_p19_stars()
    migrated = skipped = 0
    for relpath, ts in sorted(stars.items()):
        mid = model.make_material_id(kb_source_id, "text", relpath)
        existing = get_material(mid)
        if existing is not None:
            if (existing.get("triage") or {}).get("starred") is True:
                skipped += 1
                continue
            update_triage(mid, {"starred": True})
            migrated += 1
            continue
        material = model.make_material(
            mtype="text",
            source_id=kb_source_id,
            uri=relpath,
            title="",
            payload={"local_path": relpath},
            triage={"read": False, "starred": True, "enabled": True, "tags": []},
            material_id=mid,
            fetched_at=ts or _now_iso(),
        )
        add_material(material)
        migrated += 1
    return {"migrated": migrated, "skipped": skipped, "total": len(stars)}


def _load_p19_stars() -> dict[str, str]:
    """宽容读 P19 星标旧存储（{KB相对路径: ISO ts}；缺/坏 → 空表）。"""
    path = config.home_dir() / STARS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k).strip(): str(v)
        for k, v in data.items()
        if isinstance(k, str) and k.strip()
    }
