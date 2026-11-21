"""R6 core/sources 单测 — markdown_dir/sqlite 迁移兼容 + url_fetch 离线/在线/恶意输入。

在线路径一律 monkeypatch requests.get（本仓纪律：网络与 AI 全 mock，不真实
联网）。离线 fixture：tests/fixtures/wx_article_offline.html（og:title +
js_content + 两段正文 + h2 小标题 + strong 强调 + 一图 data-src + 一引用）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import frontmatter
import pytest
import requests
from click.testing import CliRunner

from musespiral.infra.sources import (
    FetchError,
    Note,
    fetch_article,
    get_by_id,
    list_from_db,
    scaffold_article,
    scan,
    url_fetch,
)
from musespiral.commands.fetch_cmds import fetch as fetch_cmd

FIXTURES = Path(__file__).parent / "fixtures"
WX_OFFLINE = FIXTURES / "wx_article_offline.html"

_WX_URL = "https://mp.weixin.qq.com/s/offlineFixture"


def _wx_html() -> str:
    return WX_OFFLINE.read_text(encoding="utf-8")


# ── markdown_dir：目录扫描与草稿（原 notes.scan/scaffold_article 行为）────


def test_markdown_dir_scan_and_scaffold(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "note.md").write_text(
        "---\ntitle: 笔记甲\ntags: [缓存, 架构]\n---\n\n" + "正" * 220,
        encoding="utf-8",
    )
    (lib / "short.md").write_text("太短", encoding="utf-8")
    (lib / ".hidden.md").write_text("隐藏" * 100, encoding="utf-8")

    items = scan(lib)
    assert [n.title for n in items] == ["笔记甲"]
    note = items[0]
    assert isinstance(note, Note) and note.chars == 220 and note.tags == ["缓存", "架构"]
    assert not note.is_article

    out = tmp_path / "draft.md"
    dst = scaffold_article(note.path, out)
    assert dst == out and out.is_file()
    post = frontmatter.load(out)
    assert post.metadata["status"] == "draft" and post.metadata["title"] == "笔记甲"
    with pytest.raises(FileExistsError):
        scaffold_article(note.path, out)


# ── 兼容：旧 import 路径 re-export（ai.py/notes_cmds/既有测试依赖面）──────


# ── sqlite：笔记库直连（原 notessql 行为，经新路径）──────────────────────


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "vault.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE notes (title TEXT, content TEXT, path TEXT, mtime REAL)")
    con.executemany(
        "INSERT INTO notes (title, content, path, mtime) VALUES (?, ?, ?, 0)",
        [
            ("短", "短" * 50, "a.md"),
            ("长笔记", "甲" * 250, "b.md"),
            (None, "# 顶替标题\n" + "乙" * 220, "c.md"),
        ],
    )
    con.commit()
    con.close()
    return db


def test_sqlite_list_and_get_by_id(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    items = list_from_db(db)
    assert [n.title for n in items] == ["长笔记", "顶替标题"]  # 短文被过滤、日期同则按字数
    assert all(isinstance(n, Note) for n in items)

    got = get_by_id(db, 2)
    assert got is not None and got.title == "长笔记" and got.content == "甲" * 250
    assert get_by_id(db, 99999) is None
    with pytest.raises(ValueError):
        get_by_id(db, "abc")
    with pytest.raises(FileNotFoundError):
        list_from_db(tmp_path / "nope.db")


# ── url_fetch：离线 fixture → Markdown 转换保真 ──────────────────────────


def test_fetch_offline_fixture_markdown_fidelity() -> None:
    post = fetch_article(_wx_html(), is_html=True)
    # frontmatter：title 来自 og:title；status draft；cover 留空；离线无来源
    assert post.metadata["title"] == "缓存一致性实战笔记"
    assert post.metadata["status"] == "draft"
    assert post.metadata["cover"] == ""
    assert post.metadata["source_url"] == ""

    c = post.content
    # 段落：两段正文均保真（去内联样式、保留文本与标点）
    assert "先说结论：缓存一致性不追求强一致，追求**失效口径唯一**" in c
    assert "第一步先量再调，用命中率与慢查询清单定位" in c
    # 标题：h2 → ## 标题行
    assert "## 三步收敛方案" in c
    # 图：data-src 优先于空 src
    assert "![](https://mmbiz.qpic.cn/mmbiz_png/offline_fixture/diagram_640.png)" in c
    # 引用：blockquote → > 前缀
    assert "> 示例数据来自本地压测环境，读者可按文末清单复现。" in c
    # js_content 之外的内容不进正文：h1 标题与 script 占位（verify 检测）
    assert "缓存一致性实战笔记" not in c
    assert "verify" not in c
    # 块间以空行分隔（Markdown 段落语义）
    assert "\n\n" in c


def test_fetch_offline_img_falls_back_to_src() -> None:
    html = (
        '<meta property="og:title" content="图源回退">'
        '<div id="js_content"><p><img src="https://example.com/a.png"></p></div>'
    )
    post = fetch_article(html, is_html=True)
    assert "![](https://example.com/a.png)" in post.content


# ── url_fetch：失效链接（无 js_content）可读 FetchError ──────────────────


def test_fetch_dead_link_placeholder_page_raises_readable_error() -> None:
    # V2 边界：失效文章返回占位页（无 js_content）
    placeholder = (
        "<html><head><title>抱歉</title></head><body>"
        "<div>该内容已被发布者删除或无法访问。</div></body></html>"
    )
    with pytest.raises(FetchError) as ei:
        fetch_article(placeholder, is_html=True)
    msg = str(ei.value)
    assert "文章失效或需在微信内打开" in msg and "js_content" in msg


def test_fetch_missing_og_title_raises() -> None:
    html = '<div id="js_content"><p>有正文无标题。</p></div>'
    with pytest.raises(FetchError) as ei:
        fetch_article(html, is_html=True)
    assert "og:title" in str(ei.value)


# ── url_fetch：恶意/畸形 HTML 容错（不崩溃）─────────────────────────────


def test_fetch_malformed_deep_nesting_1000_layers() -> None:
    depth = 1000
    html = (
        '<meta property="og:title" content="深嵌套">'
        "<div id=\"js_content\">"
        + "<section>" * depth
        + "核心正文一句话。"
        + "</section>" * depth
        + "</div>"
    )
    post = fetch_article(html, is_html=True)  # 不崩溃即过（无递归建树）
    assert "核心正文一句话。" in post.content


def test_fetch_malformed_huge_text_10mb() -> None:
    huge = "长" * (10 * 1024 * 1024)
    html = (
        '<meta property="og:title" content="超长">'
        f'<div id="js_content"><p>{huge}</p></div>'
    )
    post = fetch_article(html, is_html=True)
    assert len(post.content) >= 10 * 1024 * 1024


def test_fetch_malformed_unclosed_and_stray_tags() -> None:
    html = (
        '<meta property="og:title" content="坏页">'
        '<div id="js_content"><p>未闭合段落'
        "</blockquote></span></p>"  # 游离结束标签
        "<span>行内<b>粗体</b>继续</span>"
    )
    post = fetch_article(html, is_html=True)
    assert "未闭合段落" in post.content
    assert "**粗体**" in post.content
    assert "行内" in post.content and "继续" in post.content


# ── url_fetch：在线路径（requests 全 mock，不真实联网）──────────────────


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}


def test_fetch_online_mocked_success(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(url, headers=None, timeout=None, allow_redirects=None):
        captured.update(
            url=url, headers=headers, timeout=timeout, allow_redirects=allow_redirects
        )
        return _FakeResp(_wx_html())

    monkeypatch.setattr(url_fetch.requests, "get", fake_get)
    post = fetch_article(_WX_URL)
    # 请求参数：普通 Chrome UA + 15s 超时（V2 结论语义）+ 不自动跟随重定向
    # （SSRF 防线锚点：allow_redirects=False，重定向由白名单逐跳手动跟随）
    assert captured["url"] == _WX_URL
    assert "Chrome" in captured["headers"]["User-Agent"]
    assert captured["timeout"] == 15
    assert captured["allow_redirects"] is False
    # 产物：source_url 记入 frontmatter，正文与离线路径一致
    assert post.metadata["source_url"] == _WX_URL
    assert post.metadata["title"] == "缓存一致性实战笔记"
    assert "## 三步收敛方案" in post.content


def test_fetch_online_dead_link_mocked(monkeypatch) -> None:
    monkeypatch.setattr(
        url_fetch.requests, "get", lambda *a, **k: _FakeResp("<html><body>占位页</body></html>")
    )
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "文章失效或需在微信内打开" in str(ei.value)


def test_fetch_online_network_and_http_errors_mocked(monkeypatch) -> None:
    def boom(*a, **k):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr(url_fetch.requests, "get", boom)
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "网络错误" in str(ei.value)

    monkeypatch.setattr(
        url_fetch.requests, "get", lambda *a, **k: _FakeResp("禁止访问", status_code=403)
    )
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "HTTP 403" in str(ei.value)


# ── url_fetch：重定向 SSRF 防线（requests 层 mock 30x，逐跳白名单）──────


def test_fetch_redirect_to_internal_rejected_no_second_request(monkeypatch) -> None:
    """30x → 内网目标：第二跳请求根本不发出（allow_redirects=False + 逐跳白名单）。

    复核 high 修复锚点：requests 默认跟随重定向且无后置校验，白名单只查
    提交期初始 URL 时，127.0.0.1/169.254.169.254 等内网目标可达且响应体
    会被解析落盘——修复后目标不过门即 FetchError，网络层零外发。
    """
    calls: list[str] = []

    def fake_get(url, *a, **k):
        calls.append(url)
        return _FakeResp("", status_code=302, headers={"Location": "http://127.0.0.1:18792/internal"})

    monkeypatch.setattr(url_fetch.requests, "get", fake_get)
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "重定向目标被拒绝" in str(ei.value)
    assert "127.0.0.1" in str(ei.value)
    # 关键断言：只发出初始一次请求，内网目标从未被接触（不可达也不可探测）
    assert calls == [_WX_URL]


def test_fetch_redirect_forms_all_rejected(monkeypatch) -> None:
    """重定向目标绕过形态全集：userinfo 混淆 / 后缀伪装 / 相对协议 //evil /
    非 http(s) / 169.254 云元数据——逐跳白名单全部拒绝。"""
    targets = [
        "https://mp.weixin.qq.com@evil.com/s",      # userinfo 混淆（真实主机 evil.com）
        "https://mp.weixin.qq.com.evil.com/s",      # 后缀伪装域
        "//169.254.169.254/latest/meta-data",       # 相对协议 → 云元数据端点
        "ftp://mp.weixin.qq.com/s",                 # 非 http(s) scheme
        "https://user:pass@mp.weixin.qq.com/s",     # 带凭证形态（主机正确也拒）
    ]
    for target in targets:
        monkeypatch.setattr(
            url_fetch.requests,
            "get",
            lambda *a, **k: _FakeResp("", status_code=302, headers={"Location": target}),
        )
        with pytest.raises(FetchError) as ei:
            fetch_article(_WX_URL)
        assert "重定向目标被拒绝" in str(ei.value), target


def test_fetch_same_host_redirect_followed(monkeypatch) -> None:
    """同域重定向（绝对/相对 Location）正常跟随：合法 http→https 与规范化
    跳转不受防线影响，第二跳命中文章页照常解析。"""
    calls: list[str] = []

    def fake_get(url, *a, **k):
        calls.append(url)
        if url == _WX_URL:
            return _FakeResp("", status_code=302, headers={"Location": "/s/normalized"})
        return _FakeResp(_wx_html())

    monkeypatch.setattr(url_fetch.requests, "get", fake_get)
    post = fetch_article(_WX_URL)
    assert calls == [_WX_URL, "https://mp.weixin.qq.com/s/normalized"]
    assert post.metadata["title"] == "缓存一致性实战笔记"
    assert post.metadata["source_url"] == _WX_URL  # frontmatter 记初始 URL（语义不变）


def test_fetch_redirect_chain_to_wx_domain_allowed(monkeypatch) -> None:
    """初始 URL 不过门（CLI 场景：用户自决）但重定向落回微信文章域 → 放行。

    短链 → mp.weixin.qq.com 的合法链路保留；CLI fetch 未加初始白名单，
    防线只约束重定向目标域。"""
    calls: list[str] = []
    final = "https://mp.weixin.qq.com/s/short-expanded"

    def fake_get(url, *a, **k):
        calls.append(url)
        if url == "https://short.example/s/abc":
            return _FakeResp("", status_code=301, headers={"Location": final})
        return _FakeResp(_wx_html())

    monkeypatch.setattr(url_fetch.requests, "get", fake_get)
    post = fetch_article("https://short.example/s/abc")
    assert calls == ["https://short.example/s/abc", final]
    assert post.metadata["title"] == "缓存一致性实战笔记"


def test_fetch_redirect_loop_capped(monkeypatch) -> None:
    """同域重定向死循环：最多 _MAX_REDIRECTS 跳，超出转可读 FetchError。"""
    monkeypatch.setattr(
        url_fetch.requests,
        "get",
        lambda url, *a, **k: _FakeResp("", status_code=302, headers={"Location": "/loop"}),
    )
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "重定向次数超过" in str(ei.value)


def test_fetch_redirect_without_location_is_http_error(monkeypatch) -> None:
    """3xx 无 Location：不视为可跟随重定向，按非 200 报错（requests 语义）。"""
    monkeypatch.setattr(
        url_fetch.requests, "get", lambda *a, **k: _FakeResp("", status_code=302)
    )
    with pytest.raises(FetchError) as ei:
        fetch_article(_WX_URL)
    assert "HTTP 302" in str(ei.value)


def test_assert_article_url_gate_contract() -> None:
    """白名单门契约（Studio 提交期与重定向逐跳共用）：过门返回原 url，
    不过门 ValueError 可读信息（穿越路径形态由产物侧 slug 净化兜底，不归本门）。"""
    for ok in ("https://mp.weixin.qq.com/s/x", "http://mp.weixin.qq.com/s/x"):
        assert url_fetch.assert_article_url(ok) == ok
    for bad in (
        "https://github.com/x",                    # 非 mp 域
        "http://127.0.0.1/s",                      # 回环 IP
        "http://[::1]/s",                          # IPv6 回环
        "http://localhost/s",                      # localhost
        "https://mp.weixin.qq.com@evil.com/s",     # userinfo 混淆
        "https://user:pass@mp.weixin.qq.com/s",    # 带凭证
        "https://MP.WEIXIN.QQ.COM.evil.com/s",     # 后缀伪装
        "ftp://mp.weixin.qq.com/s",                # 非 http(s)
        "file:///etc/passwd",                      # 本地文件 scheme
    ):
        with pytest.raises(ValueError):
            url_fetch.assert_article_url(bad)


# ── CLI：musespiral fetch（命令对象直测——集成阶段才注册进 cli.py）──────────


def test_fetch_cmd_offline_writes_draft(tmp_path: Path) -> None:
    out = tmp_path / "fetched" / "draft.md"
    result = CliRunner().invoke(
        fetch_cmd, [_WX_URL, "--html", str(WX_OFFLINE), "-o", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    post = frontmatter.load(out)
    assert post.metadata["title"] == "缓存一致性实战笔记"
    assert post.metadata["status"] == "draft"
    # 成功打印输出路径 + 后续提示（cover 提示 / check 工作流 / 离线说明）
    assert str(out) in result.output
    assert "cover" in result.output and "check" in result.output
    assert "离线导入" in result.output
    assert "Traceback" not in result.output


def test_fetch_cmd_online_error_readable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        url_fetch.requests, "get", lambda *a, **k: _FakeResp("<html><body>占位页</body></html>")
    )
    out = tmp_path / "draft.md"
    result = CliRunner().invoke(fetch_cmd, [_WX_URL, "-o", str(out)])
    assert result.exit_code == 1, result.output
    assert "❌" in result.output and "文章失效或需在微信内打开" in result.output
    assert not out.exists()
    assert "Traceback" not in result.output


def test_fetch_cmd_refuses_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "draft.md"
    out.write_text("既有内容", encoding="utf-8")
    result = CliRunner().invoke(
        fetch_cmd, [_WX_URL, "--html", str(WX_OFFLINE), "-o", str(out)]
    )
    assert result.exit_code == 1, result.output
    assert "拒绝覆盖" in result.output
    assert out.read_text(encoding="utf-8") == "既有内容"


def test_fetch_offline_img_data_uri_placeholder() -> None:
    """轮三复核 low#4：无 data-src 且 src 为 data: 懒加载占位图时降级为可见占位标记。"""
    html = (
        '<html><head><meta property="og:title" content="T"></head><body>'
        '<div id="js_content"><p>前文</p>'
        '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'
        '<img data-src="https://mmbiz.qpic.cn/real.png" src="data:image/gif;base64,R0lGOD">'
        "<p>后文</p></div></body></html>"
    )
    post = fetch_article(html, is_html=True)
    assert "![](data:image" not in post.content  # 占位符不进正文
    assert "图片占位" in post.content
    assert "![](https://mmbiz.qpic.cn/real.png)" in post.content  # data-src 正常取图
