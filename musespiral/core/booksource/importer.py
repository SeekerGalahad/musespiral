"""导入格式识别（R0.5 F-X1，L2 纯，网络注入）——前置分析 §3.1 四分支 + §二 F-X1 类型识别。

四分支（顺序即优先级，与 legado `importSource`/`importSourceText` 逐条同构）：

    ① 文本整体是 JSON 数组/对象
         ├─ Sources：BookSource 数组/单对象 → 源清单（脏源逐条跳过，见下）
         └─ SourceUrls：URL 字符串数组 → 逐个拉取后按 ③ 解析（二级间接导入）
    ② 文本是绝对 URL → HTTP GET（`#requestWithoutUA` 后缀=去 UA，由 infra
       处理）→ 拉回的文本走 ③
    ③ 内容是 JSON 数组/对象 → 同 ①（allowSourceUrls=False，**不再递归**）
    ④ 否则 → 纯 JS 单文件书源（mainJs 形态）→ 可读错误（引擎态 R1 才支持）

R0.5 F-X1 两级判定（§二）：

    ①包类型：JSON 先经 `classifier.classify_payload` 六类识别——非书源包
      **不再抛错**，返回 `{kind, message}` 类型化边界说明（主题/TTS/净化/
      排版/unknown 各给一句可读话）；
    ②包内逐条：缺 bookSourceUrl 的条目跳过并计入 `skipped[{index, reason}]`
      （不再整批拒绝）；仅当「bookSource 类型且全部条目无效」才整体拒绝
      （raise，CLI ❌ / Studio 400 语义不变）。

返回形状（本函数公共契约，R0.5 起从 list 变 dict）：

    {
      "kind": 六类之一（classifier.KINDS）,
      "valid":   [规范化书源...]      # kind=bookSource 时非空语义
      "skipped": [{"index": int, "reason": str}...],  # 脏源清单（仅 bookSource）
      "message": "",                  # 非 bookSource 时类型化提示
    }

网络注入：本层不 import 任何网络库（L2 纪律），URL 分支经 `fetcher`
可调用注入（生产=infra/booksource_fetch.fetch，测试=lambda）。
未注入 fetcher 而遇到 URL → BookSourceImportError（可读错误，不静默降级）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlparse

from . import classifier, model

# 源 URL 数组键名（legado `jsonObject.has("sourceUrls")`——键名不可变）
SOURCE_URLS_KEY = "sourceUrls"


class BookSourceImportError(ValueError):
    """导入失败（可读中文信息；Studio 层转 400，CLI 层直接打印）。"""


# 纯 JS 源的可读判定（R0 不解析 JS，只认形态给出准确错误——不给「格式错误」
# 这种误导性提示）；三形态覆盖 legado JsSourceConfig 夹具的常见写法。
_JS_HINT_RE = re.compile(r"\bfunction\s+\w+\s*\(|=>\s*[({]|\b(?:const|let|var)\s+\w+\s*=")


def is_absolute_url(text: str) -> bool:
    """绝对 URL 判定（legado isAbsUrl：http/https 且有 host）。"""
    try:
        parsed = urlparse(text.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _loads(text: str) -> Any:
    """JSON 解析（失败 → _MISSING 哨兵，由调用方按分支语义处理）。"""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return _MISSING


_MISSING = object()


def _is_json_text(text: str) -> bool:
    """整体是否 JSON 对象/数组（裸标量不算——书源包不会是标量）。"""
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return False
    return _loads(stripped) is not _MISSING


def parse_booksource_json(text: str) -> dict[str, Any]:
    """JSON 文本 → `{valid, skipped}`（校验 bookSourceUrl 必填非空）。

    R0.5 F-X1：数组逐元素校验——缺 bookSourceUrl 的条目**跳过**进
    `skipped:[{index, reason}]`（不再整批拒绝）；仅当全部条目无效才
    raise BookSourceImportError（整体拒绝，§二 ③）。

    `sourceUrls` 形态在本函数**不做**网络拉取——归 import_source 的 ①
    分支处理；此处遇到即报「不是书源」（与 legado allowSourceUrls=False
    同语义）。非 JSON / 顶层标量 → raise（与旧版一致）。
    """
    data = _loads(text.strip())
    if data is _MISSING:
        raise BookSourceImportError("不是书源（内容不是合法 JSON）")
    entries: list[Any]
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        if SOURCE_URLS_KEY in data:
            raise BookSourceImportError("不是书源（sourceUrls 需经 import_source 递归拉取）")
        entries = [data]
    else:
        raise BookSourceImportError("不是书源（顶层必须是 JSON 数组或对象）")
    valid: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, raw in enumerate(entries):
        normalized = model.normalize(raw)
        if normalized is not None:
            valid.append(normalized)
        else:
            reason = "元素不是 JSON 对象" if not isinstance(raw, dict) else "缺 bookSourceUrl"
            skipped.append({"index": index, "reason": reason})
    if not valid:
        raise BookSourceImportError(
            f"不是书源（{len(entries)} 条全部无效：缺 bookSourceUrl——整包拒绝）"
        )
    return {"valid": valid, "skipped": skipped}


def import_source(
    text: str, fetcher: Callable[[str], str] | None = None
) -> dict[str, Any]:
    """文本/URL → 分类+解析结果 dict（形状见模块 docstring；四分支识别）。"""
    if not isinstance(text, str) or not text.strip():
        raise BookSourceImportError("导入内容不能为空")
    return _import_text(text.strip(), fetcher, allow_source_urls=True)


def _empty_result(kind: str, message: str = "") -> dict[str, Any]:
    return {"kind": kind, "valid": [], "skipped": [], "message": message}


def _import_text(
    text: str, fetcher: Callable[[str], str] | None, *, allow_source_urls: bool
) -> dict[str, Any]:
    if _is_json_text(text):
        return _import_json(text, fetcher, allow_source_urls=allow_source_urls)
    if is_absolute_url(text):
        return _import_url(text, fetcher)
    raise _unrecognized(text, top_level=allow_source_urls)


def _import_url(text: str, fetcher: Callable[[str], str] | None) -> dict[str, Any]:
    """② 绝对 URL → 拉取 → ③（拉回内容不再递归 sourceUrls）。"""
    if fetcher is None:
        raise BookSourceImportError(
            "URL 导入需要网络拉取（未注入 fetcher；请经 booksource_service 调用）"
        )
    try:
        body = fetcher(text)
    except BookSourceImportError:
        raise
    except Exception as err:  # 网络/大小/协议门失败 → 可读错误
        raise BookSourceImportError(f"拉取失败: {err}") from None
    content = (body or "").strip()
    if not content:
        raise BookSourceImportError("拉取失败: 订阅地址返回空内容")
    if _is_json_text(content):
        return _import_json(content, fetcher, allow_source_urls=False)
    raise _unrecognized(content, top_level=False)


def _import_json(
    text: str, fetcher: Callable[[str], str] | None, *, allow_source_urls: bool
) -> dict[str, Any]:
    """① / ③ JSON → 先分类（F-X1 六类）再解析；sourceUrls 逐项递归拉取。"""
    data = _loads(text.strip())
    if isinstance(data, dict) and SOURCE_URLS_KEY in data:
        if not allow_source_urls:
            raise BookSourceImportError("不是书源（嵌套的 sourceUrls 不再递归）")
        urls = data.get(SOURCE_URLS_KEY)
        if not isinstance(urls, list) or not urls:
            raise BookSourceImportError("不是书源（sourceUrls 必须是 URL 字符串数组）")
        if any(not isinstance(url, str) or not url.strip() for url in urls):
            raise BookSourceImportError("不是书源（sourceUrls 含空项）")
        out: list[dict[str, Any]] = []
        for url in urls:
            sub = _import_url(url.strip(), fetcher)
            out.extend(sub["valid"])
        return {
            "kind": classifier.KIND_BOOKSOURCE,
            "valid": out,
            "skipped": [],
            "message": "",
        }
    verdict = classifier.classify_payload(data)
    if verdict.kind != classifier.KIND_BOOKSOURCE:
        return _empty_result(verdict.kind, classifier.typed_message(verdict.kind, verdict.features))
    return parse_booksource_json(text) | {"kind": classifier.KIND_BOOKSOURCE, "message": ""}


def _unrecognized(text: str, *, top_level: bool) -> BookSourceImportError:
    """④ 兜底：纯 JS 单文件源给准确错误；其余给「不是书源」。"""
    if top_level and _JS_HINT_RE.search(text):
        return BookSourceImportError(
            "纯 JS 单文件书源（mainJs 形态）需 bookengine 引擎解析，R1 建设中——暂不支持导入"
        )
    return BookSourceImportError("不是书源（无法识别的格式：既非书源 JSON，也不是订阅 URL）")
