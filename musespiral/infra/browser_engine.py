"""Playwright 书源引擎（W18，2026-11-24）——零新依赖的 bookengine 替代实现。

四方法签名与响应形状与 ``infra/bookengine.py`` 完全一致（{ok, items/info/
chapters/content}），调用方（book_fetch_service / studio）无需改一行。

规则来源：书源 JSON 的可选 ``browserRule`` 子对象（纯 CSS，不动 legado 原
字段——键名不可变铁律，见 core/booksource/model.py）：

    {
      "bookSourceUrl": "https://example.com",
      "searchUrl": "https://example.com/search?q={{key}}",
      "browserRule": {
        "search": {
          "list": ".result li",
          "fields": {
            "name": "h3 a",
            "author": ".author",
            "bookUrl": {"sel": "h3 a", "attr": "href"},
            "coverUrl": {"sel": "img", "attr": "src"}
          }
        },
        "info": {"fields": {"name": "h1", "intro": ".desc"}},
        "toc": {
          "list": ".chapter-list a",
          "fields": {"title": ".", "url": {"sel": ".", "attr": "href"}}
        },
        "content": {"sel": "#content", "title": {"sel": "h1"}}
      }
    }

字段值简写：字符串 = 选择器 + 按字段名推断属性（name/title/intro/wordCount/
lastChapter 等取 text；*Url 取 href；cover* 取 src）；完整形态
``{"sel": ..., "attr": ...}`` 显式指定。选择器 ``"."`` 表示列表项自身。

纪律：
  - Playwright 为库内可选依赖（pyproject browser 组）——导入期不 import，
    真正调用时才 ``from playwright.sync_api import sync_playwright``，缺席时
    raise ``BrowserEngineUnavailable``（调用方降级不崩）；
  - 单源失败跳过（聚合搜索多源健壮性），全部失败返回空结果不抛；
  - 相对 URL 用 urljoin 转绝对；``{{key}}``/``{key}`` 均作搜索占位符。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote, urljoin

# 字段名 → 默认属性（简写规则）
_TEXT_FIELDS = {
    "name", "author", "intro", "kind", "wordCount", "lastChapter",
    "updateTime", "title", "index",
}

class BrowserEngineUnavailable(RuntimeError):
    """Playwright 缺席 / 规则缺失 / 页面抓取失败——调用方降级不崩。"""

# ── 规则解析（纯函数，无 I/O）────────────────────────────────────────

def _default_attr(field: str) -> str:
    """按字段名推断默认属性（简写规则；完整形态可覆盖）。"""
    low = field.lower()
    if "cover" in low or low.endswith("image") or low.endswith("img"):
        return "src"
    if low.endswith("url") or low.endswith("href") or low == "link":
        return "href"
    return "text"

def _parse_field(spec: Any, field: str) -> tuple[str, str] | None:
    """字段规则 → (选择器, 属性)；不合规 → None（宽容跳过坏字段）。"""
    if isinstance(spec, str) and spec.strip():
        return spec.strip(), _default_attr(field)
    if isinstance(spec, dict):
        sel = spec.get("sel") or spec.get("selector")
        if isinstance(sel, str) and sel.strip():
            attr = spec.get("attr") or _default_attr(field)
            return sel.strip(), str(attr).strip()
    return None

def parse_browser_rule(source: dict[str, Any]) -> dict[str, Any] | None:
    """书源 → 归一化 browserRule（无规则/坏规则 → None）。

    只做「缺键补默认、类型宽容」，不改 legado 原字段；规则组内部不补字段。
    """
    raw = source.get("browserRule")
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict[str, Any] = {}
    for group in ("search", "info", "toc", "content"):
        section = raw.get(group)
        if not isinstance(section, dict) or not section:
            continue
        node: dict[str, Any] = {}
        if "list" in section or "sel" in section:
            key = "list" if "list" in section else "sel"
            sel = section.get(key)
            if isinstance(sel, str) and sel.strip():
                node[key] = sel.strip()
        if "title" in section:
            parsed = _parse_field(section["title"], "title")
            if parsed:
                node["title"] = parsed
        fields = section.get("fields")
        if isinstance(fields, dict):
            parsed_fields = {}
            for name, spec in fields.items():
                p = _parse_field(spec, str(name))
                if p:
                    parsed_fields[str(name)] = p
            if parsed_fields:
                node["fields"] = parsed_fields
        if node:
            out[group] = node
    return out or None

def search_url(source: dict[str, Any], key: str) -> str | None:
    """从 searchUrl 模板生成搜索 URL（{{key}}/{key} 占位符 URL 编码）。"""
    template = source.get("searchUrl")
    if not isinstance(template, str) or "{{" not in template and "{" not in template:
        return None
    encoded = quote(key, safe="")
    return template.replace("{{key}}", encoded).replace("{key}", encoded)

# ── 页面提取（Playwright）────────────────────────────────────────────

def _extract(locator: Any, attr: str, base_url: str) -> str:
    """元素 → 文本 / 属性；相对 URL 转绝对。"""
    try:
        if attr == "text":
            value = locator.inner_text(timeout=3000).strip()
        else:
            value = locator.get_attribute(attr, timeout=3000) or ""
    except Exception:
        return ""
    value = (value or "").strip()
    if attr in ("href", "src") and value and not value.startswith(("http://", "https://", "data:")):
        value = urljoin(base_url, value)
    return value

def _extract_fields(item: Any, fields: dict[str, tuple[str, str]], base_url: str) -> dict[str, str]:
    """从列表项提取所有字段（选择器 "." = 项自身）。"""
    out: dict[str, str] = {}
    for name, (sel, attr) in fields.items():
        target = item if sel == "." else item.locator(sel).first
        out[name] = _extract(target, attr, base_url)
    return out

class BrowserEngineClient:
    """Playwright 引擎客户端（与 BookEngineClient 同契约，实例无状态）。"""

    timeout = 15000  # 单页导航上限（毫秒）

    def available(self) -> bool:
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            return False
        return True

    def health(self) -> dict[str, Any]:
        return {
            "ok": self.available(),
            "version": "playwright",
            "sources_loaded": 0,  # 门面层用真实书源数覆盖（W18b）
            "browser": True,
        }

    def _visit(self, url: str, action: Any) -> Any:
        """打开页面 → 在页面上下文执行 action(page) → 关闭浏览器。"""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserEngineUnavailable("playwright 未安装（pip install playwright）") from exc
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
                    if response is not None and response.status >= 400:
                        raise BrowserEngineUnavailable(f"页面返回 HTTP {response.status}: {url}")
                    return action(page, page.url)
                finally:
                    browser.close()
        except BrowserEngineUnavailable:
            raise
        except Exception as exc:
            raise BrowserEngineUnavailable(f"页面抓取失败（{url}）: {exc}") from exc

    def _sources(self) -> list[dict[str, Any]]:
        """启用的、带 browserRule 的书源（store 缺席 → 空）。"""
        try:
            from . import booksource_store as store

            rows = store.load_index()
            out = []
            for row in rows:
                if not row.get("enabled"):
                    continue
                src = store.load_source(row.get("bookSourceUrl", ""))
                if not src or not parse_browser_rule(src):
                    continue
                out.append(src)
            return out
        except Exception:
            return []

    def _source_by_url(self, source_url: str) -> dict[str, Any] | None:
        try:
            from . import booksource_store as store

            src = store.load_source(source_url)
            return src if src and parse_browser_rule(src) else None
        except Exception:
            return None

    # ── 四方法（契约对齐 bookengine）──────────────────────────────────

    def search(self, key: str, *, sources: Any = "all", page: int = 1) -> dict[str, Any]:
        """POST /search 等价：逐源访问 searchUrl → CSS 提取 → 聚合。"""
        picked = self._sources()
        if sources != "all":
            wanted = set(sources) if isinstance(sources, (list, tuple, set)) else {str(sources)}
            picked = [s for s in picked if s.get("bookSourceUrl") in wanted]
        items: list[dict[str, Any]] = []
        searched = 0
        for src in picked:
            rule = parse_browser_rule(src)
            section = (rule or {}).get("search")
            url = search_url(src, key)
            if not section or not url or "list" not in section:
                continue
            def grab(pg: Any, final_url: str, _s=src, _sec=section) -> list[dict[str, Any]]:
                locators = pg.locator(_sec["list"])
                out = []
                for i in range(min(locators.count(), 50)):
                    row = _extract_fields(locators.nth(i), _sec.get("fields", {}), final_url)
                    if row:
                        row.setdefault("origin", _s.get("bookSourceUrl", ""))
                        row.setdefault("originName", _s.get("bookSourceName", ""))
                        out.append(row)
                return out
            try:
                items.extend(self._visit(url, grab))
                searched += 1
            except BrowserEngineUnavailable:
                continue
        return {"ok": True, "items": items[:50], "searched": searched, "total": len(items)}

    def book_info(self, source_url: str, book_url: str) -> dict[str, Any]:
        src = self._source_by_url(source_url)
        rule = parse_browser_rule(src) if src else None
        section = (rule or {}).get("info")
        if not section:
            raise BrowserEngineUnavailable(f"书源无 browserRule.info: {source_url}")
        def grab(pg: Any, final_url: str) -> dict[str, str]:
            return _extract_fields(pg.locator("body"), section.get("fields", {}), final_url)
        info = self._visit(book_url, grab)
        info.setdefault("tocUrl", book_url)
        return {"ok": True, "info": info}

    def toc(self, source_url: str, book_url: str) -> dict[str, Any]:
        src = self._source_by_url(source_url)
        rule = parse_browser_rule(src) if src else None
        section = (rule or {}).get("toc")
        if not section or "list" not in section:
            raise BrowserEngineUnavailable(f"书源无 browserRule.toc: {source_url}")
        def grab(pg: Any, final_url: str) -> list[dict[str, Any]]:
            locators = pg.locator(section["list"])
            out = []
            for i in range(locators.count()):
                row = _extract_fields(locators.nth(i), section.get("fields", {}), final_url)
                if row.get("title") or row.get("url"):
                    row.setdefault("index", i + 1)
                    out.append(row)
            return out
        chapters = self._visit(book_url, grab)
        return {"ok": True, "chapters": chapters, "total": len(chapters)}

    def content(self, source_url: str, book_url: str, chapter_url: str) -> dict[str, Any]:
        src = self._source_by_url(source_url)
        rule = parse_browser_rule(src) if src else None
        section = (rule or {}).get("content")
        if not section or "sel" not in section:
            raise BrowserEngineUnavailable(f"书源无 browserRule.content: {source_url}")
        def grab(pg: Any, final_url: str) -> dict[str, str]:
            node = pg.locator(section["sel"]).first
            out = {"content": _extract(node, "text", final_url)}
            if "title" in section:
                sel, attr = section["title"]
                # 标题选择器相对整个页面（章节页 h1 常在 #content 之外）
                target = node if sel == "." else pg.locator(sel).first
                out["title"] = _extract(target, attr, final_url)
            return out
        return {"ok": True, **self._visit(chapter_url, grab)}
