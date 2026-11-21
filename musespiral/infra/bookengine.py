"""bookengine 引擎客户端（R0 立抽象 → R3 实调，L1）——设计 §3.3/§3.5 的 musespiral 侧接驳层。

四方法实调（引擎 = JVM 侧 R1-R2 建设，本客户端**先行钉死接口契约**——
引擎到位后按此实现，响应形状按前置分析 §6.1-6.4 的数据面约定）：

  POST /search        {sources:"all"|[...], key, page} → {ok, items:[SearchBook 形],
                       searched:int, total:int}（§6.1：每条含 name/author/kind/
                       wordCount/lastChapter/intro/coverUrl/bookUrl/origin/originName）
  GET  /book/info     ?sourceUrl=&bookUrl= → {ok, info:{name,author,intro,kind,
                       wordCount,lastChapter,coverUrl,tocUrl}}（§6.2）
  GET  /book/toc      ?sourceUrl=&bookUrl= → {ok, chapters:[{title,url,index?}],
                       total:int}（§6.3：翻页已在引擎侧合并完）
  GET  /book/content  ?sourceUrl=&bookUrl=&chapterUrl= → {ok, title?, content:str}
                       （§6.4：nextContentUrl 分页合并与源级 replaceRegex 已在
                       引擎侧执行；musespiral 再做 B 档清洗净化）
  GET  /health        → {ok, version, sources_loaded}

纪律：

  - **回环钉死（不可配置）**：`BASE_URL` 恒 `http://127.0.0.1:8792`——不读
    base 相关环境变量、无构造参数（radar-hub 客户端同款，见 R0 注）。
  - **缺席降级**：连接被拒/非 2xx/形状不符 → `BookEngineUnavailable` →
    service/studio 层捕获 → 灰灯（`engine_status` 的 absent 语义）。
  - **mock 开关（R3 接驳批）**：`MUSESPIRAL_ENGINE_MOCK=1/true` 时四方法返回
    确定性假件（零网络）——引擎 R1-R2 未就绪期间测试与前端联调的分离
    面板（设计裁决：引擎不可达不阻塞管线逻辑）。mock 健康包带
    `mock:true` 标记（`engine_status` 透出 version="mock" 可辨识）。
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 回环钉死常量（唯一事实源；不要在别处再写 8792 字面量）
BASE_URL = "http://127.0.0.1:8792"
# /health 探活超时（设计 §3.5：3s）
HEALTH_TIMEOUT = 3.0
# 数据端点超时：搜索聚合逐源 30s 上限（架构设计 §3.3），多源串行/翻页合并
# 给足余量；单章正文是单页抓取，60s 同一档（简单优先，不做每端点微调）
CALL_TIMEOUT = 60.0

PATH_HEALTH = "/health"
PATH_SEARCH = "/search"
PATH_BOOK_INFO = "/book/info"
PATH_BOOK_TOC = "/book/toc"
PATH_BOOK_CONTENT = "/book/content"

# mock 开关环境变量（"1"/"true" 开启；大小写不敏感）
_MOCK_ENV = "MUSESPIRAL_ENGINE_MOCK"


class BookEngineUnavailable(RuntimeError):
    """引擎缺席（未运行 / 连接被拒 / 非 2xx / 响应形状不符）——调用方降级不崩。"""


def mock_enabled() -> bool:
    """MUSESPIRAL_ENGINE_MOCK 是否开启（每次现读——测试 monkeypatch 即时生效）。"""
    return os.environ.get(_MOCK_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _request_json(path: str, *, timeout: float, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    """回环 BASE_URL 上的 JSON 请求（失败 → BookEngineUnavailable）。"""
    data = None
    url = f"{BASE_URL}{path}"
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except (URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise BookEngineUnavailable(f"bookengine 未就绪（{BASE_URL} 不可达: {reason}）") from None
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BookEngineUnavailable("bookengine 响应不是合法 JSON") from None
    if not isinstance(parsed, dict):
        raise BookEngineUnavailable("bookengine 响应不是 JSON 对象")
    return parsed


def _get(path: str, *, timeout: float) -> dict[str, Any]:
    """GET 兼容别名（R0 既有测试锚 `bookengine._get` 的原通道）。"""
    return _request_json(path, timeout=timeout)


def _require_shape(payload: dict[str, Any], key: str, kind: type, method: str) -> Any:
    """响应契约校验：`payload[key]` 必须是 kind（形状不符=集成故障→降级异常）。"""
    if not isinstance(payload.get(key), kind):
        raise BookEngineUnavailable(
            f"bookengine {method} 响应形状不符：缺少合法 {key!r} 字段"
        )
    return payload[key]


def _query(**params: str | int) -> str:
    """query 串组装（urlencode 转义——URL 含中文/特殊字符时的正确性面）。"""
    return "?" + urlencode({k: str(v) for k, v in params.items()})


class BookEngineClient:
    """引擎客户端（实例无状态：纯 BASE_URL + 超时）。

    base_url 是类属性而非构造参数——「配置不可覆盖」的结构保证（同 R0）。
    """

    base_url = BASE_URL  # 钉死：不可配置（无 __init__ 参数、不读 base env）

    def __init__(self, timeout: float = CALL_TIMEOUT) -> None:
        self._timeout = timeout

    # ── 探活 ──────────────────────────────────────────────────────

    def available(self) -> bool:
        """GET /health 探活（3s 超时）；任何失败 → False（灰灯语义）。mock 开 → True。"""
        if mock_enabled():
            return True
        try:
            payload = _get(PATH_HEALTH, timeout=HEALTH_TIMEOUT)
        except BookEngineUnavailable:
            return False
        return payload.get("ok") is True

    def health(self) -> dict[str, Any]:
        """探活详情（{ok, version, sources_loaded}）；缺席 → {ok:false, absent:true,...}。"""
        if mock_enabled():
            return dict(MOCK_HEALTH)
        try:
            return _get(PATH_HEALTH, timeout=HEALTH_TIMEOUT)
        except BookEngineUnavailable as exc:
            return {"ok": False, "absent": True, "error": str(exc)}

    # ── 四方法（R3 实调；响应形状契约见模块 docstring）──────────────

    def search(self, key: str, *, sources: Any = "all", page: int = 1) -> dict[str, Any]:
        """POST /search：聚合搜索（引擎代理；四桶合并在引擎侧，§7.2）。"""
        if mock_enabled():
            return _mock_search(key, page)
        payload = _request_json(
            PATH_SEARCH,
            timeout=self._timeout,
            method="POST",
            body={"sources": sources, "key": key, "page": int(page)},
        )
        _require_shape(payload, "items", list, "search")
        return payload

    def book_info(self, source_url: str, book_url: str) -> dict[str, Any]:
        """GET /book/info：书籍详情（封面/简介/字数/tocUrl——§6.2）。"""
        if mock_enabled():
            return dict(MOCK_BOOK_INFO)
        payload = _request_json(
            PATH_BOOK_INFO + _query(sourceUrl=source_url, bookUrl=book_url),
            timeout=self._timeout,
        )
        _require_shape(payload, "info", dict, "book/info")
        return payload

    def toc(self, source_url: str, book_url: str) -> dict[str, Any]:
        """GET /book/toc：目录（chapters 已含引擎侧翻页合并——§6.3）。"""
        if mock_enabled():
            return {"ok": True, "chapters": _mock_chapters(), "total": len(_mock_chapters())}
        payload = _request_json(
            PATH_BOOK_TOC + _query(sourceUrl=source_url, bookUrl=book_url),
            timeout=self._timeout,
        )
        _require_shape(payload, "chapters", list, "book/toc")
        return payload

    def content(self, source_url: str, book_url: str, chapter_url: str) -> dict[str, Any]:
        """GET /book/content：单章正文（分页合并/源级 replaceRegex 已在引擎侧——§6.4）。"""
        if mock_enabled():
            return _mock_content(chapter_url)
        payload = _request_json(
            PATH_BOOK_CONTENT + _query(
                sourceUrl=source_url, bookUrl=book_url, chapterUrl=chapter_url
            ),
            timeout=self._timeout,
        )
        _require_shape(payload, "content", str, "book/content")
        return payload


# ── mock 假件（确定性；带广告/乱码行供清洗链自证）───────────────────

MOCK_HEALTH: dict[str, Any] = {"ok": True, "version": "mock", "sources_loaded": 1, "mock": True}
MOCK_BOOK_INFO: dict[str, Any] = {
    "ok": True,
    "info": {
        "name": "mock 书",
        "author": "mock 作者",
        "intro": "用于引擎缺席期联调的确定性假书。",
        "kind": "测试",
        "wordCount": "3.6万字",
        "lastChapter": "第 12 章",
        "coverUrl": "",
        "tocUrl": "https://mock.example/book/1/toc",
    },
}


def _mock_chapters() -> list[dict[str, Any]]:
    return [
        {"title": f"第 {i} 章 · mock 主题 {i}", "url": f"https://mock.example/book/1/c{i}", "index": i}
        for i in range(1, 13)
    ]


def _mock_search(key: str, page: int) -> dict[str, Any]:
    name = f"mock 书·{key}" if key else "mock 书"
    items = [
        {
            "name": name,
            "author": "mock 作者",
            "kind": "测试",
            "wordCount": "3.6万字",
            "lastChapter": "第 12 章",
            "intro": f"关于「{key}」的确定性假书（第 {page} 页）。",
            "coverUrl": "",
            "bookUrl": "https://mock.example/book/1",
            "origin": "https://mock.example/source",
            "originName": "Mock 源",
        }
    ]
    return {"ok": True, "items": items, "searched": 1, "total": len(items)}


def _mock_content(chapter_url: str) -> dict[str, Any]:
    idx = 1
    tail = chapter_url.rsplit("c", 1)[-1]
    if tail.isdigit():
        idx = int(tail)
    title = f"第 {idx} 章 · mock 主题 {idx}"
    # 每章含：正文段 ×6 + 一条广告行 + 一条乱码行（清洗函数的确定性夹具）
    para = f"{title}的正文段落 {idx}：本章围绕 mock 主题 {idx} 展开论证，先立论再展开，"
    para += "随后给出论据与反例，最后回到主线收束。"
    lines = [title, ""]
    for j in range(1, 7):
        lines.append(f"{para}（第 {j} 段）")
        lines.append("")
    lines.append("天才一秒记住本书首发地址：mock.example")
    lines.append("")
    lines.append("��\x1b��")
    lines.append("")
    return {"ok": True, "title": title, "content": "\n".join(lines)}
