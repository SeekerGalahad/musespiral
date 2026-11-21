"""导入四分支测试（R0）——前置分析 §3.1 逐分支 + 网络注入（零真实联网）。"""
from __future__ import annotations

import json

import pytest

from musespiral.core.booksource import importer


def _src(url: str, name: str = "", lu: int = 0) -> dict:
    return {"bookSourceUrl": url, "bookSourceName": name or url, "lastUpdateTime": lu}


def test_branch1_json_array() -> None:
    """① Sources：JSON 数组 → 源清单（R0.5 起返回 {kind, valid, skipped, message}）。"""
    text = json.dumps([_src("https://a.com"), _src("https://b.com")])
    out = importer.import_source(text)
    assert out["kind"] == "bookSource" and out["message"] == "" and out["skipped"] == []
    assert [s["bookSourceUrl"] for s in out["valid"]] == ["https://a.com", "https://b.com"]


def test_branch1_json_single_object() -> None:
    """① Sources：单对象 → 单源。"""
    out = importer.import_source(json.dumps(_src("https://a.com", "A")))
    assert len(out["valid"]) == 1 and out["valid"][0]["bookSourceName"] == "A"


def test_branch2_absolute_url_fetch() -> None:
    """② 绝对 URL → HTTP GET → ③ 解析（fetcher 注入，零真实联网）。"""
    body = json.dumps([_src("https://fetched.com")])
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        calls.append(url)
        return body

    out = importer.import_source("https://sub.example.com/list.json", fake_fetch)
    assert calls == ["https://sub.example.com/list.json"]
    assert out["valid"][0]["bookSourceUrl"] == "https://fetched.com"


def test_branch2_url_without_fetcher_is_readable_error() -> None:
    """未注入 fetcher 遇 URL → 可读错误（不静默降级）。"""
    with pytest.raises(importer.BookSourceImportError, match="需要网络拉取"):
        importer.import_source("https://sub.example.com/list.json")


def test_branch3_fetched_content_does_not_recurse_source_urls() -> None:
    """③ 拉回内容里的 sourceUrls 不再递归（legado allowSourceUrls=False）。"""
    body = json.dumps({"sourceUrls": ["https://other.com/x.json"]})
    with pytest.raises(importer.BookSourceImportError, match="不再递归"):
        importer.import_source("https://sub.example.com/list.json", lambda _u: body)


def test_source_urls_branch_recurses_once() -> None:
    """① SourceUrls 形态：逐项递归拉取（二级间接导入；仅文本直给时生效）。"""
    def fake_fetch(url: str) -> str:
        return json.dumps([_src(url.replace(".json", ""))])

    text = json.dumps({"sourceUrls": [
        "https://one.example.com/a.json", "https://two.example.com/b.json",
    ]})
    out = importer.import_source(text, fake_fetch)
    assert [s["bookSourceUrl"] for s in out["valid"]] == [
        "https://one.example.com/a", "https://two.example.com/b",
    ]


def test_branch4_js_source_gives_actionable_error() -> None:
    """④ 纯 JS 单文件源 → 可读错误（引擎 R1 才支持，不误导为「格式错误」）。"""
    js = 'function search(key) { return "x"; }\nconst base = "https://a.com";'
    with pytest.raises(importer.BookSourceImportError, match="纯 JS 单文件书源"):
        importer.import_source(js)


def test_branch4_unrecognized_gives_readable_error() -> None:
    with pytest.raises(importer.BookSourceImportError, match="无法识别的格式"):
        importer.import_source("随便一段既不是 JSON 也不是 URL 的文本")


def test_empty_input_rejected() -> None:
    for bad in ("", "   ", None):
        with pytest.raises(importer.BookSourceImportError, match="不能为空"):
            importer.import_source(bad)  # type: ignore[arg-type]


def test_parse_skips_missing_url_per_entry() -> None:
    """F-X1 ② 逐条跳过：缺 bookSourceUrl 的条目进 skipped，不再整批拒绝。"""
    out = importer.parse_booksource_json(
        json.dumps([_src("https://a.com"), {"bookSourceName": "无地址"}])
    )
    assert [s["bookSourceUrl"] for s in out["valid"]] == ["https://a.com"]
    assert out["skipped"] == [{"index": 1, "reason": "缺 bookSourceUrl"}]


def test_parse_rejects_when_all_entries_invalid() -> None:
    """F-X1 ③ 仅当全部条目无效才整体拒绝（键在值空——classify 认得书源包）。"""
    with pytest.raises(importer.BookSourceImportError, match="全部无效"):
        importer.parse_booksource_json(json.dumps([{"bookSourceUrl": " "}, {"bookSourceUrl": ""}]))
    with pytest.raises(importer.BookSourceImportError, match="全部无效"):
        importer.parse_booksource_json(json.dumps({"bookSourceUrl": "  "}))


def test_parse_rejects_non_json_and_scalar() -> None:
    with pytest.raises(importer.BookSourceImportError, match="不是合法 JSON"):
        importer.parse_booksource_json("not json")
    with pytest.raises(importer.BookSourceImportError, match="顶层必须是"):
        importer.parse_booksource_json("42")


def test_parse_rejects_source_urls_outside_import_source() -> None:
    """parse_booksource_json 不做网络拉取——sourceUrls 形态交给报错。"""
    with pytest.raises(importer.BookSourceImportError, match="import_source"):
        importer.parse_booksource_json(json.dumps({"sourceUrls": ["https://a.com/x"]}))


def test_parse_normalizes_legacy_and_fills_defaults() -> None:
    """解析即归一（旧格式解包 + 缺省补全）。"""
    legacy = {"bookSourceUrl": "https://old.com", "ruleSearch": json.dumps({"bookList": ".li"})}
    out = importer.parse_booksource_json(json.dumps([legacy]))["valid"][0]
    assert out["ruleSearch"] == {"bookList": ".li"}
    assert out["respondTime"] == 180000


def test_parse_non_dict_element_skips_with_reason() -> None:
    """非对象元素跳过（reason=元素不是 JSON 对象），不整批拒绝。"""
    out = importer.parse_booksource_json(json.dumps([_src("https://a.com"), 42]))
    assert out["skipped"] == [{"index": 1, "reason": "元素不是 JSON 对象"}]
