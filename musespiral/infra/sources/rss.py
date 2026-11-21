"""RSS/Atom/JSON Feed 解析（B 采集扩展 §二 F65）——feedparser 裁决实现。

选型（B 正典 §十 R-B2 实施修订裁决引入 feedparser，替代原 stdlib ET 窄域
方案；即 B 正典 §八-3 预留的「协议接口可替换实现」落点）：RSS 2.0（Hugo/Hexo/
Jekyll 生成面）+ Atom 0.3/1.0（GitHub releases atom 同构）+ JSON Feed
（任务书容错要求，feedparser 原生不覆盖——json 路径自实现映射）。

纪律（B 正典 §〇 容错树沿承）：
  - DTD/ENTITY 一律拒绝门：合法 RSS/Atom/JSON Feed 无需 DTD，billion-laughs
    类实体注入在进 feedparser 之前即拒（feedparser 实体行为不押注）；
  - 格式白名单：CDF/RSS 0.9x/RDF 不支持——可读错误不静默降级；
  - 单条目缺 guid/link 只拒该条（错误含「第 k 条」），不整源失败；
  - 截首不尾：max_items 取 feed 首 N 条（feed 时序最新优先）。

纯解析零 I/O（body 由 infra/fetcher 咽喉取得）；guid 归一/license 枚举
推断（cc:license → CC-BY 前缀）在本层，URL 规范化与 ALL-RIGHTS 缺省
基线归服务层（T65-1 口径）。
"""
from __future__ import annotations

import calendar
import json
import re
import time
from typing import Any

import feedparser

# feedparser version 白名单（rss20/rss20ns…/atom03/atom10）
_ALLOWED_VERSION_PREFIXES = ("rss2", "atom")

# DTD/ENTITY 拒绝门（大小写不敏感；合法三格式无需 DTD/内部实体）
_DTD_RE = re.compile(r"<!\s*(doctype|entity)", re.IGNORECASE)

# JSON Feed 版本标识（https://jsonfeed.org/version/1 与 1.1 同构）
_JSONFEED_VERSION_PREFIX = "https://jsonfeed.org/version/"

_FORMAT_ERROR = "非 RSS 2.0/Atom 格式（CDF/RSS 0.9x/RDF 不支持，可换 Atom 链接）"


class FeedParseError(ValueError):
    """feed 解析失败（格式不支持/安全门拒绝/良构失败）——可读错误。"""


def _as_text(value: Any) -> str:
    """宽容字符串化（None/非 str → ""，str strip）。"""
    return value.strip() if isinstance(value, str) else ""


def _iso_from_parsed(st: Any) -> str:
    """feedparser 的 struct_time（UTC 语义）→ ISO 秒级（timegm 归一，缺省空串）。"""
    if st is None:
        return ""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(calendar.timegm(st)))
    except (TypeError, ValueError, OverflowError):
        return ""


def _license_fields(raw_license: Any) -> tuple[str, str]:
    """license 声明 → (枚举推断, 声明原文留档)。无标记 → ("", "")。

    CC-BY 前缀规则：creativecommons.org/licenses/by… → "CC-BY"（正典
    §1.2 license 推断判例）；其余声明只留原文档（枚举外不入 LICENSES）。
    """
    raw = _as_text(raw_license)
    if not raw:
        return "", ""
    if "creativecommons.org/licenses/by" in raw.lower():
        return "CC-BY", raw
    return "", raw


def _reject_entry(index: int) -> str:
    return f"第 {index} 条缺 guid/link（无法构造稳定 id，该条目拒）"


def _jsonfeed_author(entry: dict[str, Any]) -> str:
    """JSON Feed 作者归一：authors[0].name > author.name > author 字符串。"""
    authors = entry.get("authors")
    if isinstance(authors, list) and authors and isinstance(authors[0], dict):
        return _as_text(authors[0].get("name"))
    author = entry.get("author")
    if isinstance(author, dict):
        return _as_text(author.get("name"))
    return _as_text(author)


def _parse_json_feed(data: dict[str, Any], max_items: int | None) -> dict[str, Any]:
    """JSON Feed（jsonfeed.org 1.x）→ 同构条目面（截首不尾同纪律）。"""
    items: list[dict[str, Any]] = []
    rejected: list[str] = []
    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    for i, entry in enumerate(raw_items[: max_items or len(raw_items)], start=1):
        if not isinstance(entry, dict):
            rejected.append(f"第 {i} 条不是对象（JSON Feed 条目拒）")
            continue
        guid = _as_text(entry.get("id"))
        url = _as_text(entry.get("url")) or _as_text(entry.get("external_url"))
        if not guid and not url:
            rejected.append(_reject_entry(i))
            continue
        enum_license, license_note = _license_fields(entry.get("license"))
        items.append(
            {
                "guid": guid,
                "url": url,
                "title": _as_text(entry.get("title")),
                "published": _as_text(entry.get("date_published")),
                "author": _jsonfeed_author(entry),
                "license": enum_license,
                "license_note": license_note,
            }
        )
    return {"feed_title": _as_text(data.get("title")), "items": items, "rejected": rejected}


def _entry_item(index: int, entry: Any) -> dict[str, Any] | str:
    """feedparser 条目 → 条目 dict；缺 guid/link 返回拒绝理由 str。"""
    guid = _as_text(entry.get("id")) or _as_text(entry.get("guid"))
    url = _as_text(entry.get("link"))
    if not guid and not url:
        return _reject_entry(index)
    enum_license, license_note = _license_fields(entry.get("license"))
    return {
        "guid": guid,
        "url": url,
        "title": _as_text(entry.get("title")),
        "published": _iso_from_parsed(entry.get("published_parsed"))
        or _iso_from_parsed(entry.get("updated_parsed")),
        "author": _as_text(entry.get("author")),
        "license": enum_license,
        "license_note": license_note,
    }


def parse_feed(body: bytes | str, *, max_items: int | None = None) -> dict[str, Any]:
    """解析 feed body → {"feed_title", items[], rejected[]}。

    - DTD/ENTITY 声明 → FeedParseError（安全门拒绝，零条目产出）；
    - JSON Feed（body 为对象且 version 指向 jsonfeed.org）→ json 路径；
    - 版本白名单（RSS 2.0/Atom）外（CDF/RSS 0.9x/RDF/HTML 页面体）→
      FeedParseError「非 RSS 2.0/Atom 格式……可换 Atom 链接」；
    - max_items 截首不尾（feed 首 N 条，时序最新优先）。
    """
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body
    if _DTD_RE.search(text):
        raise FeedParseError("feed 包含 DTD/ENTITY 声明（安全门拒绝）")
    stripped = text.lstrip("﻿ \t\r\n")
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise FeedParseError(f"JSON 解析失败: {exc}") from None
        if isinstance(data, dict) and str(data.get("version") or "").startswith(
            _JSONFEED_VERSION_PREFIX
        ):
            return _parse_json_feed(data, max_items)
        raise FeedParseError(_FORMAT_ERROR)
    parsed_feed = feedparser.parse(text)
    version = str(parsed_feed.get("version") or "")
    if not version.startswith(_ALLOWED_VERSION_PREFIXES):
        raise FeedParseError(_FORMAT_ERROR if version == "" else
                             f"feed 版本不受支持: {version}（CDF/RSS 0.9x/RDF 不支持，可换 Atom 链接）")
    entries = list(parsed_feed.get("entries") or [])
    items: list[dict[str, Any]] = []
    rejected: list[str] = []
    for i, entry in enumerate(entries[: max_items or len(entries)], start=1):
        item = _entry_item(i, entry)
        if isinstance(item, str):
            rejected.append(item)
        else:
            items.append(item)
    return {
        "feed_title": _as_text(parsed_feed.feed.get("title")),
        "items": items,
        "rejected": rejected,
    }
