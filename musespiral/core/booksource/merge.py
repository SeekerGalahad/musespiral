"""导入三态判定 + importKeep 偏好（R0，L2 纯）——前置分析 §3.2 的 Python 同构。

三态（legado `resolveImportBookSourceStatus`，前置分析 §3.2 表）：

    isNew      本地无此源                                  → 默认选中
    isUpdate   本地 lastUpdateTime < 导入 lastUpdateTime   → 默认选中
    latest     否则（本地更新或相等）                       → 默认不选，可手选

importKeep*（`importSelect` 落库前应用；前置分析 §3.2）：
    keep_name    → 保留本地 bookSourceName
    keep_group   → 保留本地 bookSourceGroup
    keep_enable  → 保留本地 enabled + enabledExplore
    customOrder  → **恒定保留本地**（不受开关控制——legado 原语义：
                   `source.customOrder = it.customOrder` 恒执行）
"""
from __future__ import annotations

from typing import Any

STATUS_NEW = "isNew"
STATUS_UPDATE = "isUpdate"
STATUS_LATEST = "latest"
STATUSES: tuple[str, ...] = (STATUS_NEW, STATUS_UPDATE, STATUS_LATEST)


def resolve_status(local_last_update: Any, import_last_update: Any) -> str:
    """三态判定。local 为 None 表示本地无此源（isNew）。

    时间戳宽容取整（脏值按 0）：legado 是 Long 强类型，JSON 文本可能给
    字符串，归一在此吸收——判定语义与 `localSource == null` /
    `localSource.lastUpdateTime < list.lastUpdateTime` 逐条同构。
    """
    if local_last_update is None:
        return STATUS_NEW
    local = _as_int(local_last_update)
    imported = _as_int(import_last_update)
    if local < imported:
        return STATUS_UPDATE
    return STATUS_LATEST


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def default_selected(status: str) -> bool:
    """三态默认勾选（isNew/isUpdate 选中；latest 不选）。"""
    return status in (STATUS_NEW, STATUS_UPDATE)


def _present(value: Any) -> bool:
    """legado 的 `it` 判空是「本地存在」——这里本地行来自索引，恒存在。"""
    return isinstance(value, dict)


def apply_keep(
    imported: dict[str, Any],
    local: dict[str, Any] | None,
    *,
    keep_name: bool = False,
    keep_group: bool = False,
    keep_enable: bool = False,
) -> dict[str, Any]:
    """importKeep 应用：返回**新 dict**（不改调用方对象）。

    本地不存在（isNew）时无本地偏好可保留——原样返回浅拷贝。
    customOrder 恒保留本地（无开关）——这是 legado 原语义，不要「顺手
    加个开关」。
    """
    out = dict(imported)
    if not _present(local):
        return out
    if keep_name and isinstance(local.get("bookSourceName"), str):
        out["bookSourceName"] = local["bookSourceName"]
    if keep_group:
        local_group = local.get("bookSourceGroup")
        if isinstance(local_group, str) or local_group is None:
            out["bookSourceGroup"] = local_group
    if keep_enable:
        out["enabled"] = bool(local.get("enabled", True))
        out["enabledExplore"] = bool(local.get("enabledExplore", True))
    out["customOrder"] = _as_int(local.get("customOrder", 0))
    return out
