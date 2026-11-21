"""书源模型测试（R0）：键名不可变 round-trip / 旧格式兼容 / 索引投影 / 归一。

键名不可变是生态铁律（社区源按键名发布）——round-trip 断言逐键相等，
是本域最重要的一条机器门。
"""
from __future__ import annotations

import json

import pytest

from musespiral.core.booksource import model


SAMPLE = {
    "bookSourceUrl": "https://www.example.com",
    "bookSourceName": "示例源",
    "bookSourceGroup": "小说,出版",
    "bookSourceType": 0,
    "customOrder": 7,
    "enabled": True,
    "enabledExplore": False,
    "concurrentRate": "1000/2000",
    "header": '{"User-Agent":"Mozilla"}',
    "lastUpdateTime": 1700000000000,
    "respondTime": 1234,
    "weight": 5,
    "searchUrl": "/search?q={{key}}",
    "ruleSearch": {"bookList": ".list@li", "name": "a@title"},
    "ruleBookInfo": {"init": ".info", "tocUrl": "a@href"},
    "ruleToc": {"chapterList": ".ch@a", "nextTocUrl": "option@value"},
    "ruleContent": {"content": "#content@html", "replaceRegex": "净化"},
}


def test_normalize_keeps_all_keys_roundtrip() -> None:
    """键名不可变：归一后每个输入键逐值相等（缺省键另补，不改不删输入键）。"""
    out = model.normalize(SAMPLE)
    assert out is not None
    for key, value in SAMPLE.items():
        assert out[key] == value, f"键 {key} 被改写: {out[key]!r} != {value!r}"
    assert set(SAMPLE) <= set(out), "归一不得丢键"


def test_normalize_fills_defaults_for_missing_keys() -> None:
    """缺省键补默认（Kotlin 声明缺省逐条）。"""
    out = model.normalize({"bookSourceUrl": "https://a.com"})
    assert out is not None
    assert out["bookSourceName"] == ""
    assert out["respondTime"] == 180000
    assert out["enabled"] is True
    assert out["enabledExplore"] is True
    assert out["enabledCookieJar"] is True
    assert out["bookSourceType"] == 0
    assert out["customOrder"] == 0
    assert out["lastUpdateTime"] == 0
    assert out["ruleSearch"] is None
    assert set(model.BOOKSOURCE_KEYS) <= set(out)


def test_normalize_keeps_unknown_keys() -> None:
    """未知键原样保留（生态会加字段——丢弃即数据损失）。"""
    out = model.normalize({"bookSourceUrl": "https://a.com", "futureKey": {"x": 1}})
    assert out is not None
    assert out["futureKey"] == {"x": 1}


def test_normalize_rejects_missing_or_blank_url() -> None:
    assert model.normalize({}) is None
    assert model.normalize({"bookSourceUrl": "   "}) is None
    assert model.normalize({"bookSourceUrl": None}) is None
    assert model.normalize("not-a-dict") is None


def test_legacy_wrapped_rule_group_is_unwrapped() -> None:
    """旧格式兼容：规则字段值是「JSON 字符串再包一层」（INITIAL_GSON 同款）。"""
    legacy = {
        "bookSourceUrl": "https://old.example.com",
        "ruleSearch": json.dumps({"bookList": ".list@li"}),
        "ruleContent": json.dumps({"content": "#content"}),
    }
    out = model.normalize(legacy)
    assert out is not None
    assert out["ruleSearch"] == {"bookList": ".list@li"}
    assert out["ruleContent"] == {"content": "#content"}


def test_legacy_unwrap_is_single_layer_only() -> None:
    """只解一层（legado 同构）：解包后内部仍是字符串的值保持字符串，不递归。"""
    inner = json.dumps({"x": 1})
    wrapped = json.dumps({"bookList": inner})  # 一层包裹，内部值本身也是 JSON 串
    assert model.unwrap_rule_group(wrapped) == {"bookList": inner}
    # 已是对象的值不二次处理
    assert model.unwrap_rule_group({"bookList": ".x"}) == {"bookList": ".x"}
    assert model.unwrap_rule_group(None) is None


def test_legacy_unwrap_keeps_broken_value() -> None:
    """坏 JSON 字符串原样返回（读侧宽容，不抛）。"""
    assert model.unwrap_rule_group("{not json}") == "{not json}"
    assert model.unwrap_rule_group("plain text") == "plain text"


def test_index_row_projection_is_lightweight() -> None:
    """索引投影只含九个轻量键，不含规则大字段。"""
    row = model.index_row(model.normalize(SAMPLE), imported_at="2026-11-18T00:00:00")
    assert tuple(row) == model.INDEX_KEYS
    assert "ruleSearch" not in row and "searchUrl" not in row
    assert row["bookSourceName"] == "示例源"
    assert row["group"] == "小说,出版"
    assert row["imported_at"] == "2026-11-18T00:00:00"


def test_index_row_name_alias_tolerated() -> None:
    """索引读宽容：`name` 别名回填 bookSourceName（人工可编辑缓存）。"""
    row = model.normalize_index_row({"bookSourceUrl": "https://a.com", "name": "别名"})
    assert row is not None and row["bookSourceName"] == "别名"
    assert model.normalize_index_row({"bookSourceName": "无 url"}) is None


def test_group_names_splits_on_all_separators() -> None:
    """分组分隔符 = legado splitGroupRegex [,;，；]（服务层过滤复用）。"""
    src = {"bookSourceGroup": "小说;出版，有声； 玄幻 "}
    assert model.group_names(src) == ["小说", "出版", "有声", "玄幻"]
    assert model.group_names({}) == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"enabled": "false"}, False),
        ({"enabled": 0}, False),
        ({"enabled": "yes"}, True),
        ({"respondTime": "999"}, 999),
        ({"lastUpdateTime": 1.9}, 1),
    ],
)
def test_type_coercion_is_lenient(raw, expected) -> None:
    """类型宽容（GSON 同款）：脏值不崩，按最近语义归一。"""
    out = model.normalize({"bookSourceUrl": "https://a.com", **raw})
    assert out is not None
    key = next(iter(raw))
    assert out[key] == expected
