"""引擎选择器（W18b）：JVM → Playwright → 缺席，单一持有者。

所有取文/搜索调用点统一走 ``get_engine_client()``，不各自判断引擎形态：

  - 体验模式（开关语义只在 bookengine.py 内，见其 health() 的 mock 标记）→
    仍走 BookEngineClient（它内部出 mock 假件）；
  - JVM 在线（GET /health ok）→ BookEngineClient；
  - JVM 缺席 + Playwright 可用 + 存在带 browserRule 的启用书源 →
    BrowserEngineClient（本地浏览器抓取真页面）；
  - 都不行 → BookEngineClient（其调用抛 BookEngineUnavailable，UI 灰灯）。

探活每次现做（不缓存）：JVM 可能后启动、书源可能新增；探测是单次
GET /health（3s），开销可接受。调用方需要「不可用」语义时照旧捕获
BookEngineUnavailable（浏览器引擎失败抛 BrowserEngineUnavailable——本模块
把两者统一成 BookEngineUnavailable 的子类语义，见下）。
"""
from __future__ import annotations

from typing import Any

from . import bookengine
from . import browser_engine

class EngineUnavailable(bookengine.BookEngineUnavailable):
    """统一缺席异常（浏览器引擎缺席也归此，调用方只捕一种）。"""

def engine_kind() -> str:
    """当前引擎形态：mock / jvm / playwright / absent（供状态面展示）。"""
    # 体验模式语义只在 bookengine.py 内裁决（架构门：开关名不得泄漏出该文件）；
    # 这里只看它 health() 的 mock 标记，不判定开关本身。
    if bookengine.BookEngineClient().health().get("mock"):
        return "mock"
    if bookengine.BookEngineClient().available():
        return "jvm"
    if _browser_ready():
        return "playwright"
    return "absent"

def _browser_ready() -> bool:
    if not browser_engine.BrowserEngineClient().available():
        return False
    try:
        from . import booksource_store as store

        for row in store.load_index():
            if not row.get("enabled"):
                continue
            src = store.load_source(row.get("bookSourceUrl", ""))
            if src and browser_engine.parse_browser_rule(src):
                return True
    except Exception:
        return False
    return False

def get_engine_client() -> Any:
    """返回当前可用引擎客户端（四方法同契约）。"""
    kind = engine_kind()
    if kind == "playwright":
        return browser_engine.BrowserEngineClient()
    return bookengine.BookEngineClient()

def health() -> dict[str, Any]:
    """统一健康包（{ok, version, ...}）——状态面直接用。"""
    kind = engine_kind()
    if kind == "playwright":
        h = browser_engine.BrowserEngineClient().health()
        return {**h, "kind": "playwright"}
    return {**bookengine.BookEngineClient().health(), "kind": kind}
