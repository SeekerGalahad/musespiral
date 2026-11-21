"""三态合并 + importKeep 测试（R0）——前置分析 §3.2 语义逐条同构。"""
from __future__ import annotations

import pytest

from musespiral.core.booksource import merge


@pytest.mark.parametrize(
    "local,imported,expected",
    [
        (None, 0, "isNew"),                    # 本地无 → 新增
        (None, 1700000000000, "isNew"),
        (100, 200, "isUpdate"),                # 本地较旧 → 更新
        (200, 100, "latest"),                  # 本地较新 → 最新
        (200, 200, "latest"),                  # 相等 → 最新
        (0, 0, "latest"),                      # 均零 → 最新（不是 isNew）
        (None, None, "isNew"),                 # 导入无时间戳但本地无 → 新增
        (100, None, "latest"),                 # 导入无时间戳、本地有 → 最新
        ("100", "200", "isUpdate"),            # 字符串时间戳宽容
        ("bad", 1, "isUpdate"),                # 脏值按 0
    ],
)
def test_resolve_status_semantics(local, imported, expected) -> None:
    """三态判定（legado resolveImportBookSourceStatus 逐条同构）。"""
    assert merge.resolve_status(local, imported) == expected


def test_default_selected_only_for_new_and_update() -> None:
    assert merge.default_selected("isNew") is True
    assert merge.default_selected("isUpdate") is True
    assert merge.default_selected("latest") is False


def test_apply_keep_on_new_source_is_noop() -> None:
    """本地无 → 无本地偏好可保留（原样返回，且不改调用方对象）。"""
    imported = {"bookSourceUrl": "https://a.com", "bookSourceName": "新名"}
    out = merge.apply_keep(imported, None, keep_name=True, keep_group=True, keep_enable=True)
    assert out == imported
    assert out is not imported


def test_apply_keep_preserves_local_fields() -> None:
    """importKeep 三开关各自保留本地值；未开的不保留。"""
    imported = {
        "bookSourceUrl": "https://a.com", "bookSourceName": "导入名",
        "bookSourceGroup": "导入组", "enabled": True, "enabledExplore": True,
        "customOrder": 0,
    }
    local = {
        "bookSourceUrl": "https://a.com", "bookSourceName": "本地名",
        "bookSourceGroup": "本地组", "enabled": False, "enabledExplore": False,
        "customOrder": 42,
    }
    kept = merge.apply_keep(
        imported, local, keep_name=True, keep_group=True, keep_enable=True
    )
    assert kept["bookSourceName"] == "本地名"
    assert kept["bookSourceGroup"] == "本地组"
    assert kept["enabled"] is False
    assert kept["enabledExplore"] is False
    assert kept["customOrder"] == 42

    plain = merge.apply_keep(imported, local)
    assert plain["bookSourceName"] == "导入名"
    assert plain["bookSourceGroup"] == "导入组"
    assert plain["enabled"] is True


def test_custom_order_always_preserved() -> None:
    """customOrder 恒保留本地（legado 原语义，不受开关控制）。"""
    imported = {"bookSourceUrl": "https://a.com", "customOrder": 0}
    local = {"bookSourceUrl": "https://a.com", "customOrder": 99}
    assert merge.apply_keep(imported, local)["customOrder"] == 99


def test_apply_keep_does_not_mutate_inputs() -> None:
    imported = {"bookSourceUrl": "https://a.com", "bookSourceName": "A"}
    local = {"bookSourceUrl": "https://a.com", "bookSourceName": "B", "customOrder": 3}
    before_imported, before_local = dict(imported), dict(local)
    merge.apply_keep(imported, local, keep_name=True)
    assert imported == before_imported
    assert local == before_local
