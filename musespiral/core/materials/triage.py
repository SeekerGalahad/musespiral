"""素材分诊谓词（第二十期 §1.2/§三；第二十一期 §1.2 扩四键）——L2 纯函数，零 I/O。

triage 四键是素材唯一的人工可变面（immutable 原始红线的另一面）：
  read     已读（阅读状态标记）
  starred  星标（P19 收藏语义迁移落点）
  enabled  启用（false=保留但不进推荐——查询默认过滤）
  tags     标签列表（第二十一期 §1.2 修订：纯人工、可逆、不构成血统；
           规范化/限额契约在 tags.py——本批 tags 面唯一契约）

本模块只做三件事：键集钉死（triage_patch 拒绝任何四键之外的写入）、
过滤谓词（filter_materials）、推荐资格判定（is_recommendable）。
"""
from __future__ import annotations

from typing import Any

from . import tags as _tags

# 人工可变四键（正典 §1.2 triage 形状；tags 与三布尔同类窄门）
TRIAGE_KEYS: tuple[str, ...] = ("read", "starred", "enabled", "tags")

# 布尔键缺省（与 model.normalize_material 补默认同口径——裸行也能安全过滤）
_DEFAULTS: dict[str, bool] = {"read": False, "starred": False, "enabled": True}


def triage_patch(triage: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """分诊更新（纯函数）：返回补丁后的新 triage，不改入参。

    键集钉死 + 双轨校验：changes 含 TRIAGE_KEYS 之外的键 → ValueError
    （人工动作只动四键——payload/provenance 的 immutable 由这枚窄门守）；
    三布尔键值非 bool → ValueError；tags 键走 tags.normalize_tags（非
    list[str] / 超 12 个 / 单标签超 24 字符 → ValueError，全量替换语义）。
    """
    patch: dict[str, Any] = {k: v for k, v in changes.items() if k != "tags"}
    if "tags" in changes:
        patch["tags"] = _tags.normalize_tags(changes["tags"])  # 先归一（幂等无损）
    for key, value in patch.items():
        if key not in TRIAGE_KEYS:
            raise ValueError(f"triage 只允许改 {TRIAGE_KEYS} 四键，拒绝: {key!r}")
        if key == "tags":
            continue  # 已按 tags 轨归一校验
        if not isinstance(value, bool):
            raise ValueError(f"triage.{key} 必须是布尔值: {value!r}")
    return {**triage, **patch}


def matches(material: dict[str, Any], **filters: Any) -> bool:
    """单素材分诊谓词：filters 各键 None=不过滤该键，否则须命中。

    只认 TRIAGE_KEYS 内的键，未知键 ValueError（调用方拼错键名不得静默
    变成恒真过滤）。素材缺 triage 或键缺失按默认值参与比较；tags 键的
    期望值为标签名（str，成员判定——标签过滤走本键，服务层透传）。
    """
    triage = material.get("triage") or {}
    for key, expected in filters.items():
        if key not in TRIAGE_KEYS:
            raise ValueError(f"未知分诊键: {key!r}（合法键 {TRIAGE_KEYS}）")
        if expected is None:
            continue
        if key == "tags":
            tags = triage.get("tags")
            tags = tags if isinstance(tags, list) else []
            if expected not in tags:
                return False
            continue
        if triage.get(key, _DEFAULTS[key]) != expected:
            return False
    return True


def filter_materials(
    materials: list[dict[str, Any]],
    *,
    starred: bool | None = None,
    read: bool | None = None,
    enabled: bool | None = None,
) -> list[dict[str, Any]]:
    """分诊过滤（保序）：starred/read/enabled 各键 None=不过滤。

    查询视图的默认面由调用方组装（推荐视图传 enabled=True；星标 tab 传
    starred=True）；本函数只做谓词，不做排序与评分（评分现算归收编后端）。
    """
    return [
        m for m in materials
        if matches(m, starred=starred, read=read, enabled=enabled)
    ]


def is_recommendable(material: dict[str, Any]) -> bool:
    """推荐资格：enabled=True（禁用素材保留在库但不进推荐，正典 §1.2）。"""
    triage = material.get("triage") or {}
    return triage.get("enabled", _DEFAULTS["enabled"]) is True
