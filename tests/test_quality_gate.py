"""质量门 v2 单测（设计 §十三 F7 / F11a / F11c）。

覆盖五个点:
  7a  run_checks 正文图片存在性 —— 本地路径缺失 FAIL（列缺失清单）、存在 PASS、网络图不在此列
  7c  run_checks LaTeX/Mermaid —— mermaid 代码块 WARN、$$...$$ 与成对 $ 数学式 WARN、货币配对不误报
  11a render_checks linkfoot:false 时外链残留 FAIL → WARN 降级
  11c render_checks data:/file: 协议图片 → WARN（微信转存必失败）
  7b  preview 静态服务 —— / 与 /preview 包装页、白名单图片 200 + Content-Type、
      路径穿越/未知扩展名/缺失 404

所有文件写 tmp_path（MUSESPIRAL_HOME 一并隔离），不触碰 ~/.musespiral、不发真实网络请求
（静态服务仅绑定 127.0.0.1 回环地址）。
"""
from __future__ import annotations

import contextlib
import http.client
import socketserver
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from musespiral.core.content.parser import ParsedArticle, parse_article
from musespiral.core.quality.checks import run_checks
from musespiral.core.quality.render_checks import render_checks
from musespiral.preview import _make_handler


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _make_image(path: Path, width: int = 4, height: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), "#336699").save(path)


def _write_article(tmp_path: Path, body: str, name: str = "article.md") -> Path:
    """写一篇带合法封面/frontmatter 的临时文章，返回路径。"""
    _make_image(tmp_path / "cover.png", 900, 383)
    path = tmp_path / name
    path.write_text(
        f"---\ntitle: 质量门测试\ncover: cover.png\ndigest: 摘要\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


def _levels(results) -> list[str]:
    return [lvl for lvl, _ in results]


def _messages(results, level: str) -> list[str]:
    return [msg for lvl, msg in results if lvl == level]


# ---- 7a 正文图片存在性 ----


def test_missing_body_image_fails_with_list(tmp_path: Path) -> None:
    """本地图片文件缺失 → FAIL 并列出全部缺失路径（裂图必现）。"""
    article_path = _write_article(tmp_path, "![配图一](img/missing.png)\n\n![配图二](gone.jpg)\n")
    results = run_checks(parse_article(article_path), article_path)
    fails = _messages(results, "FAIL")
    assert len(fails) == 1
    assert "2 张正文图片文件缺失" in fails[0]
    assert "img/missing.png" in fails[0] and "gone.jpg" in fails[0]


def test_existing_body_image_passes(tmp_path: Path) -> None:
    """本地图片存在 → PASS（不再误报「正文无图片」）。"""
    _make_image(tmp_path / "img" / "pic.png")
    article_path = _write_article(tmp_path, "![配图](img/pic.png)\n")
    results = run_checks(parse_article(article_path), article_path)
    assert "FAIL" not in _levels(results)
    assert any("正文 1 张本地图片齐全" in msg and "≤ 2MB" in msg for _, msg in results)


def test_web_image_skips_existence_check(tmp_path: Path) -> None:
    """网络图不参与本地存在性检查（走 F3 渲染级外域路径），混合时注明网络图数量。"""
    _make_image(tmp_path / "img" / "pic.png")
    article_path = _write_article(
        tmp_path, "![本地图](img/pic.png)\n\n![网络图](https://cdn.example.com/a.png)\n"
    )
    results = run_checks(parse_article(article_path), article_path)
    assert "FAIL" not in _levels(results)
    assert any("另有 1 张网络图" in msg for _, msg in results)


def test_image_typed_block_missing_fails(tmp_path: Path) -> None:
    """直接以 {type: image} block 提供时同样走存在性检查（收集器两个来源都覆盖）。"""
    _make_image(tmp_path / "cover.png", 900, 383)
    article = ParsedArticle(
        metadata={"title": "t", "cover": "cover.png"},
        blocks=[{"type": "image", "src": "ghost.png", "alt": ""}],
    )
    results = run_checks(article, tmp_path / "article.md")
    assert "ghost.png" in _messages(results, "FAIL")[0]


# ---- 7c LaTeX / Mermaid 检测 ----


def test_mermaid_code_block_warns(tmp_path: Path) -> None:
    """```mermaid 代码块 → WARN「公众号不渲染，请转图片」。"""
    article_path = _write_article(tmp_path, "```mermaid\ngraph TD;\nA-->B;\n```\n")
    results = run_checks(parse_article(article_path), article_path)
    warns = _messages(results, "WARN")
    assert any("mermaid" in w and "转图片" in w for w in warns)
    assert "FAIL" not in _levels(results)


def test_block_latex_warns(tmp_path: Path) -> None:
    """块级 $$E=mc^2$$ → LaTeX WARN，且不重复计数。"""
    article_path = _write_article(tmp_path, "质能方程 $$E=mc^2$$ 见上文。\n")
    results = run_checks(parse_article(article_path), article_path)
    warns = [w for w in _messages(results, "WARN") if "LaTeX" in w]
    assert len(warns) == 1
    assert "1 处" in warns[0] and "转图片" in warns[0]


def test_inline_latex_warns(tmp_path: Path) -> None:
    """行内成对 $a^2+b^2=c^2$ → LaTeX WARN。"""
    article_path = _write_article(tmp_path, "勾股定理 $a^2+b^2=c^2$ 成立。\n")
    results = run_checks(parse_article(article_path), article_path)
    assert any("LaTeX" in w and "转图片" in w for w in _messages(results, "WARN"))


def test_currency_pairs_not_flagged(tmp_path: Path) -> None:
    """货币配对 $ 不误报：中文语境配对（含中文标点）与纯数字单词配对都不算数学式。"""
    article_path = _write_article(
        tmp_path,
        "价格是 $100，再加 $50 运费。\n\ncosts $5 and $10 total, no math here.\n",
    )
    results = run_checks(parse_article(article_path), article_path)
    assert not any("LaTeX" in w for w in _messages(results, "WARN"))


def test_dollar_in_code_block_not_flagged(tmp_path: Path) -> None:
    """代码块里的 $（shell 变量等）不是数学式。"""
    article_path = _write_article(tmp_path, "```bash\necho $HOME $USER\n```\n")
    results = run_checks(parse_article(article_path), article_path)
    assert not any("LaTeX" in w for w in _messages(results, "WARN"))


# ---- 11a linkfoot:false 外链降级 ----


def _meta_article(**metadata) -> ParsedArticle:
    base: dict = {"title": "t"}
    base.update(metadata)
    return ParsedArticle(metadata=base, blocks=[], raw_body="")


FOREIGN_HTML = '<p>参考 <a href="https://github.com/foo/bar">项目</a></p>'


def test_linkfoot_false_downgrades_foreign_link_to_warn(tmp_path: Path) -> None:
    """linkfoot:false 时外链残留 FAIL → WARN（开关即用户显式选择，机器不再否决）。"""
    article = _meta_article(linkfoot=False)
    results = render_checks(FOREIGN_HTML, article, tmp_path / "a.md")
    assert "FAIL" not in _levels(results)
    warns = _messages(results, "WARN")
    assert len(warns) == 1
    assert "外链残留" in warns[0] and "github.com/foo/bar" in warns[0]


def test_linkfoot_default_foreign_link_still_fails(tmp_path: Path) -> None:
    """未关闭 linkfoot（默认）时外链残留仍 FAIL —— 与降级行为形成对照。"""
    results = render_checks(FOREIGN_HTML, _meta_article(), tmp_path / "a.md")
    assert "外链残留" in _messages(results, "FAIL")[0]


# ---- 11c data:/file: 协议图片 ----


def test_data_scheme_image_warns(tmp_path: Path) -> None:
    """data: 协议图片 → WARN（微信转存必失败）。"""
    html = '<img src="data:image/png;base64,iVBORw0KGgo=" alt="" />'
    results = render_checks(html, _meta_article(), tmp_path / "a.md")
    warns = _messages(results, "WARN")
    assert len(warns) == 1
    assert "data:" in warns[0] and "转存必失败" in warns[0]


def test_file_scheme_image_warns(tmp_path: Path) -> None:
    """file: 协议图片 → 同样 WARN。"""
    html = '<img src="file:///home/user/pic.png" alt="" />'
    results = render_checks(html, _meta_article(), tmp_path / "a.md")
    warns = _messages(results, "WARN")
    assert len(warns) == 1
    assert "file:" in warns[0] and "转存必失败" in warns[0]


def test_scheme_image_not_reported_as_compliant(tmp_path: Path) -> None:
    """data: 图片不得并入「来源合规」PASS。"""
    html = '<img src="images/local.png" alt="" /><img src="data:image/gif;base64,R0=" alt="" />'
    results = render_checks(html, _meta_article(), tmp_path / "a.md")
    assert not any("来源合规" in msg for _, msg in results)
    assert any("转存必失败" in msg for msg in _messages(results, "WARN"))


# ---- 7b preview 静态服务 ----

WRAPPED = "<html>WRAPPED-PAGE</html>"


@contextlib.contextmanager
def _live_server(article_dir: Path, wrapped: str = WRAPPED):
    """在 127.0.0.1 随机端口起真实 HTTP 服务，yield base URL。"""
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _make_handler(article_dir, wrapped))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _fetch(base: str, path: str) -> tuple[int, str, bytes]:
    """GET 并返回 (状态码, Content-Type, body)；4xx/5xx 不抛异常。"""
    try:
        with urllib.request.urlopen(base + path) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_root_and_preview_serve_wrapped_page(tmp_path: Path) -> None:
    """/ 与 /preview 返回包装页（200, text/html）。"""
    with _live_server(tmp_path) as base:
        for path in ("/", "/preview"):
            code, ctype, body = _fetch(base, path)
            assert code == 200
            assert ctype.startswith("text/html")
            assert b"WRAPPED-PAGE" in body


@pytest.mark.parametrize(
    "filename,ctype",
    [
        ("pic.png", "image/png"),
        ("pic.jpg", "image/jpeg"),
        ("pic.jpeg", "image/jpeg"),
        ("pic.gif", "image/gif"),
        ("pic.webp", "image/webp"),
        ("pic.svg", "image/svg+xml"),
    ],
)
def test_static_image_200_with_content_type(tmp_path: Path, filename: str, ctype: str) -> None:
    """白名单扩展名图片 200 且 Content-Type 按扩展名正确。"""
    payload = f"IMGDATA-{filename}".encode()
    (tmp_path / filename).write_bytes(payload)
    with _live_server(tmp_path) as base:
        code, got_ctype, body = _fetch(base, f"/{filename}")
    assert code == 200
    assert got_ctype == ctype
    assert body == payload


def test_static_nested_path_200(tmp_path: Path) -> None:
    """子目录相对路径图片可命中（对应正文 img/pic.png 引用形态）。"""
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "pic.png").write_bytes(b"nested")
    with _live_server(tmp_path) as base:
        code, ctype, body = _fetch(base, "/img/pic.png")
    assert code == 200
    assert ctype == "image/png"
    assert body == b"nested"


def test_path_traversal_blocked_404(tmp_path: Path) -> None:
    """../ 越出文章目录 → 404（文件真实存在也不给）。"""
    secret = tmp_path.parent / "secret-8765.png"
    secret.write_bytes(b"secret")  # article_dir=tmp_path 之外的同级文件
    try:
        with _live_server(tmp_path) as base:
            port = int(base.rsplit(":", 1)[1])
            # http.client 不做路径归一化，../ 原样发出
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/../secret-8765.png")
            resp = conn.getresponse()
            assert resp.status == 404
            conn.close()
            # 百分号编码形态同样拦下
            code, _, _ = _fetch(base, "/%2e%2e/secret-8765.png")
            assert code == 404
    finally:
        secret.unlink(missing_ok=True)


def test_unknown_extension_404(tmp_path: Path) -> None:
    """白名单之外的文件（含文章自身 .md）一律 404。"""
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "article.md").write_text("---\ntitle: t\n---\n")
    with _live_server(tmp_path) as base:
        assert _fetch(base, "/notes.txt")[0] == 404
        assert _fetch(base, "/article.md")[0] == 404


def test_missing_static_file_404(tmp_path: Path) -> None:
    """扩展名合法但文件不存在 → 404。"""
    with _live_server(tmp_path) as base:
        assert _fetch(base, "/nope.png")[0] == 404


def test_wrap_html_escapes_srcdoc_attribute() -> None:
    # 第四期复核 finding#5：srcdoc 用 repr 会在含撇号/双引号的 HTML 上产生
    # 反斜杠转义截断属性（HTML 不认 \ 转义）——改用 HTML 实体转义后必须无反斜杠
    from musespiral.preview import _wrap_html

    html = '<p>It\'s a "quoted" title</p>'
    wrapped = _wrap_html(html, "a.md")

    assert 'srcdoc="' in wrapped
    assert "&quot;" in wrapped  # 双引号被实体转义，属性不被截断
    assert "\\'" not in wrapped  # repr 反斜杠转义不再出现


def test_currency_and_inline_code_not_flagged_as_latex(tmp_path) -> None:
    """第四期遗留 low#1：汇率句与行内 code span 里的 $ 不再误报 LaTeX。"""
    from musespiral.core.content.parser import parse_article
    from musespiral.core.quality.checks import run_checks

    article = tmp_path / "fx.md"
    article.write_text(
        "---\ntitle: 汇率与代码\ncover: c.png\n---\n\n"
        "Rate: now $1 = 7.2 CNY, last year $1 = 6.9.\n\n"
        "Run `calc $a^2$` to verify.\n",
        encoding="utf-8",
    )
    (tmp_path / "c.png").write_bytes(b"x")
    results = run_checks(parse_article(article), article)
    latex = [msg for _, msg in results if "LaTeX" in msg]
    assert latex == [], f"误报: {latex}"


def test_resolve_static_null_byte_returns_none(tmp_path) -> None:
    """第四期遗留 low#6：%00 编码路径统一 404 而非 ValueError。"""
    from musespiral.preview import _resolve_static

    assert _resolve_static(tmp_path, "/%00.png") is None
