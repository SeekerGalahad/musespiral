"""W18 Playwright 书源引擎测试（本地假站点，零外部网络）。

用 stdlib HTTPServer 起一个含搜索/详情/目录/正文四页的假书站，配合真实
Chromium 跑 BrowserEngineClient 四方法全链（search→info→toc→content），
并对规则解析的简写/完整形态、缺席降级做机器锁。
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from musespiral.core.booksource import model as source_model
from musespiral.infra import bookengine
from musespiral.infra import booksource_store as store
from musespiral.infra import browser_engine as be

SITE_HTML = {
    "/search": """<html><body><ul class="result">
      <li><h3><a href="/book/1">三体</a></h3><span class="author">刘慈欣</span>
          <img src="/cover/1.jpg"></li>
      <li><h3><a href="/book/2">球状闪电</a></h3><span class="author">刘慈欣</span></li>
    </ul></body></html>""",
    "/book/1": """<html><body><h1>三体</h1><div class="desc">宇宙社会学小说</div>
      <a class="toc-link" href="/book/1/toc">目录</a>
      <ul class="chapter-list">
        <li><a href="/book/1/c1">第一章 疯狂年代</a></li>
        <li><a href="/book/1/c2">第二章 寂静的春天</a></li>
      </ul></body></html>""",
    "/book/1/toc": """<html><body><ul class="chapter-list">
      <li><a href="/book/1/c1">第一章 疯狂年代</a></li>
      <li><a href="/book/1/c2">第二章 寂静的春天</a></li>
    </ul></body></html>""",
    "/book/1/c1": """<html><body><h1>第一章</h1><div id="content">
      <p>汪淼眼前的数据突然消失了。</p><p>这是正文第二段。</p>
    </div></body></html>""",
}

SOURCE = {
    "bookSourceUrl": "http://book.test",
    "bookSourceName": "测试书站",
    "searchUrl": "http://book.test/search?q={{key}}",
    "browserRule": {
        "search": {
            "list": ".result li",
            "fields": {
                "name": "h3 a",
                "author": ".author",
                "bookUrl": {"sel": "h3 a", "attr": "href"},
                "coverUrl": {"sel": "img", "attr": "src"},
            },
        },
        "info": {"fields": {"name": "h1", "intro": ".desc"}},
        "toc": {
            "list": ".chapter-list a",
            "fields": {"title": ".", "url": {"sel": ".", "attr": "href"}},
        },
        "content": {"sel": "#content", "title": {"sel": "h1", "attr": "text"}},
    },
}

class _SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        # 搜索页带 query；其余按 path 精确匹配
        key = parsed.path
        if key == "/search" and parse_qs(parsed.query).get("q"):
            key = "/search"
        body = SITE_HTML.get(key)
        if body is None:
            self.send_error(404)
            return
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass

@pytest.fixture(scope="module")
def fake_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], dict(SOURCE)
    server.shutdown()
    server.server_close()

@pytest.fixture
def client(fake_site, monkeypatch, tmp_path):
    port, source = fake_site
    base = f"http://127.0.0.1:{port}"
    source["bookSourceUrl"] = base
    source["searchUrl"] = f"{base}/search?q={{{{key}}}}"
    # 隔离书源库到 tmp（不动真实 ~/.musespiral）
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    source_model.normalize(source)  # 校验归一不破坏 browserRule
    store.save_source(source)
    store.save_index([source_model.index_row(source)])
    return be.BrowserEngineClient()

def test_parse_browser_rule_shorthand_and_full():
    rule = be.parse_browser_rule(SOURCE)
    assert rule is not None
    assert rule["search"]["fields"]["author"] == (".author", "text")
    assert rule["search"]["fields"]["bookUrl"] == ("h3 a", "href")
    assert rule["search"]["fields"]["coverUrl"] == ("img", "src")
    assert rule["content"]["title"] == ("h1", "text")

def test_default_attr_inference():
    assert be._default_attr("name") == "text"
    assert be._default_attr("bookUrl") == "href"
    assert be._default_attr("coverUrl") == "src"

def test_search_url_template_both_syntaxes():
    src = {"searchUrl": "https://x/search?q={{key}}&k2={key}"}
    assert be.search_url(src, "三 体") == "https://x/search?q=%E4%B8%89%20%E4%BD%93&k2=%E4%B8%89%20%E4%BD%93"

@pytest.mark.browser
def test_search_end_to_end(client, fake_site):
    result = client.search("三体")
    assert result["ok"] and result["searched"] == 1
    names = {it["name"] for it in result["items"]}
    assert names == {"三体", "球状闪电"}
    first = next(it for it in result["items"] if it["name"] == "三体")
    assert first["author"] == "刘慈欣"
    assert first["bookUrl"].endswith("/book/1")
    assert first["originName"] == "测试书站"

@pytest.mark.browser
def test_info_toc_content_end_to_end(client, fake_site):
    port, _ = fake_site
    base = f"http://127.0.0.1:{port}"
    info = client.book_info(base, f"{base}/book/1")
    assert info["ok"] and info["info"]["name"] == "三体"
    assert info["info"]["intro"] == "宇宙社会学小说"
    toc = client.toc(base, f"{base}/book/1")
    assert toc["ok"] and toc["total"] == 2
    assert toc["chapters"][0]["title"] == "第一章 疯狂年代"
    content = client.content(base, f"{base}/book/1", f"{base}/book/1/c1")
    assert content["ok"] and "汪淼" in content["content"]
    assert content["title"] == "第一章"

def test_unavailable_when_no_rule(monkeypatch, tmp_path):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    client = be.BrowserEngineClient()
    with pytest.raises(be.BrowserEngineUnavailable):
        client.book_info("http://none", "http://none/book")

def test_no_external_import_at_module_load():
    # 规则解析是纯函数，无 Playwright 依赖（缺席环境仍可用）
    assert be.parse_browser_rule({"browserRule": {"search": {"list": ".x"}}}) == {
        "search": {"list": ".x"}
    }
