"""静态站导出全链测试（G-T6，正典=《musespiral-调研吸收功能设计-2026-11-18》§七）。

覆盖四块硬指标：
  1. 三篇已发布产物结构（每篇 <stem>.html + index.html + feed.xml 齐全）；
  2. index + feed 内容（标题/日期/标签/RSS 条目/pubDate）；
  3. 草稿不导出（action=draft 与 result=uploaded 均不入站）；
  4. HTML 无外链 JS（manifest script/link/@import/url(http) 探针全空）。
另测：frontmatter meta 进 HTML/RSS、零外联 fail-closed、CLI site build 入口、
RSS 文章 link 是内容不被零外联探针误判。全走 tmp + MUSESPIRAL_HOME 隔离，不触真实 ~/.musespiral。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.cli import cli
from musespiral.core.site import (
    SiteEntry,
    external_resource_refs,
    render_article_html,
    render_index,
    render_rss,
)
from musespiral.core.content.parser import parse_article
from musespiral.services.publish import site_service  # W2-2：真身（monkeypatch 需打在源模块）


# ── fixtures ────────────────────────────────────────────────


@pytest.fixture
def articles_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """隔离 MUSESPIRAL_HOME + 造 3 篇已发布 + 1 篇草稿的文章库与日志。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    root = tmp_path / "articles"
    root.mkdir()
    for name, title, tags, date in [
        ("01_alpha.md", "甲篇", "AI,写作", "2026-11-10"),
        ("02_beta.md", "乙篇", "架构", "2026-11-12"),
        ("03_sub/gamma.md", "丙篇", "工程,复盘", "2026-11-15"),  # 子目录
    ]:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f"---\ntitle: {title}\nauthor: 作者甲\ntags: [{tags}]\ndate: {date}\n"
            f"digest: 摘要{title}\n---\n\n# {title}\n\n这是{title}的正文段落。\n",
            encoding="utf-8",
        )
    # 草稿（仅 action=draft）与仅交付回执（result=uploaded）也应被排除
    (root / "draft_only.md").write_text(
        "---\ntitle: 草稿篇\n---\n# 草稿\n不该导出。\n", encoding="utf-8"
    )
    rows = [
        {"ts": "2026-11-10T09:00:00", "action": "publish", "article": "01_alpha.md",
         "title": "甲篇", "platform": "mp"},
        {"ts": "2026-11-12T09:00:00", "action": "publish", "article": "02_beta.md",
         "title": "乙篇", "platform": "mp"},
        {"ts": "2026-11-15T09:00:00", "action": "publish", "article": "03_sub/gamma.md",
         "title": "丙篇", "platform": "mp"},
        {"ts": "2026-11-16T09:00:00", "action": "draft", "article": "draft_only.md",
         "title": "草稿篇", "platform": "mp"},
        {"ts": "2026-11-17T09:00:00", "platform": "mp", "article": "uploaded_only.md",
         "result": "uploaded"},
    ]
    (home / "publish_log.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    return root


# ── 1. 三篇已发布产物结构 ──────────────────────────────────


def test_build_writes_three_articles_plus_index_feed(
    articles_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    result = site_service.build(out, root=articles_root, site_link="https://blog.example")

    assert result.published_total == 3
    assert set(result.written) == {"01_alpha.html", "02_beta.html", "03_sub/gamma.html"}
    assert result.skipped == []
    # 产物结构：三篇 HTML（含子目录）+ 索引 + RSS
    for rel in result.written:
        assert (out / rel).is_file(), rel
    assert (out / "index.html").is_file()
    assert (out / "feed.xml").is_file()
    # 每篇都是完整 HTML 文档 + 纸墨 CSS + 正文
    for rel in result.written:
        html = (out / rel).read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "<style>" in html and "site-article" in html
        assert "正文段落" in html  # 发布链 md→html 管线确实跑了


# ── 2. index + feed 内容 ───────────────────────────────────


def test_index_lists_date_and_tags(articles_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    site_service.build(out, root=articles_root)
    idx = (out / "index.html").read_text(encoding="utf-8")
    for title in ("甲篇", "乙篇", "丙篇"):
        assert title in idx
    assert "2026-11-10" in idx  # 日期进列表
    assert "AI" in idx and "写作" in idx and "复盘" in idx  # 标签进列表
    assert "03_sub/gamma.html" in idx  # 子目录文章链接
    assert "共 3 篇" in idx


def test_feed_has_items_pubdate_and_links(articles_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    site_service.build(out, root=articles_root, site_link="https://blog.example")
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert feed.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<rss version=\"2.0\">" in feed
    assert feed.count("<item>") == 3
    assert "<title>甲篇</title>" in feed
    assert "<pubDate>" in feed and "2026" in feed  # RFC822 日期
    # RSS 文章链接是内容，带站点绝对前缀（RSS <link> 是内容非资源，零外联断言只管 HTML）
    assert "<link>https://blog.example/01_alpha.html</link>" in feed


def test_frontmatter_meta_into_html_head(articles_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    site_service.build(out, root=articles_root)
    html = (out / "01_alpha.html").read_text(encoding="utf-8")
    assert '<meta name="author" content="作者甲">' in html
    assert '<meta name="description" content="摘要甲篇">' in html
    assert '<meta property="article:published_time" content="2026-11-10">' in html
    assert '<meta name="keywords" content="AI, 写作">' in html


# ── 3. 草稿不导出 ──────────────────────────────────────────


def test_drafts_and_uploaded_only_are_not_exported(
    articles_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "site"
    result = site_service.build(out, root=articles_root)
    all_files = {p.name for p in out.rglob("*") if p.is_file()}
    assert "draft_only.html" not in all_files
    assert "uploaded_only.html" not in all_files
    idx = (out / "index.html").read_text(encoding="utf-8")
    feed = (out / "feed.xml").read_text(encoding="utf-8")
    assert "草稿篇" not in idx and "草稿篇" not in feed


# ── 4. HTML 无外链 JS ──────────────────────────────────────


def test_all_html_has_no_external_script_or_style(articles_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "site"
    site_service.build(out, root=articles_root)
    htmls = list(out.rglob("*.html"))
    assert len(htmls) == 4  # 三篇 + index
    for p in htmls:
        assert external_resource_refs(p.read_text(encoding="utf-8")) == [], p.name
        text = p.read_text(encoding="utf-8")
        assert "<script" not in text.lower(), p.name
        assert "<link" not in text.lower(), p.name
        assert "@import" not in text.lower(), p.name
        assert ".js" not in text, p.name


def test_external_resource_refs_detects_bad_html() -> None:
    """探针本身有效性：注入 script/link/@import/外链 url() 都能抓到。"""
    assert external_resource_refs('<script src="x.js"></script>')
    assert external_resource_refs('<link rel="stylesheet" href="https://x/a.css">')
    assert external_resource_refs("<style>@import url(x.css);</style>")
    assert external_resource_refs("<style>a{background:url(https://x/i.png)}</style>")
    assert external_resource_refs("<p>clean</p>") == []


# ── 5. fail-closed / CLI 入口 ──────────────────────────────


def test_build_fail_closed_on_external_resource(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home2"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    root = tmp_path / "arts2"
    root.mkdir()
    (root / "x.md").write_text(
        "---\ntitle: 恶意\ndate: 2026-11-19\n---\n<script>alert(1)</script>\n",
        encoding="utf-8",
    )
    (home / "publish_log.jsonl").write_text(
        json.dumps({"ts": "2026-11-19T09:00:00", "action": "publish",
                    "article": "x.md", "platform": "mp"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    original = site_service.render_article_html
    def injected(*a, **k):
        return original(*a, **k).replace("</head>", '<script>x</script></head>')

    monkeypatch.setattr(site_service, "render_article_html", injected)
    with pytest.raises(RuntimeError, match="零外联断言失败"):
        site_service.build(tmp_path / "bad-site", root=root)


def test_cli_site_build_entry(articles_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "cli-site"
    result = CliRunner().invoke(
        cli, ["site", "build", "--output", str(out), "--root", str(articles_root)],
        prog_name="musespiral",
    )
    assert result.exit_code == 0, result.output
    assert "构建完成" in result.output
    assert (out / "index.html").is_file()
    assert (out / "feed.xml").is_file()
    assert (out / "01_alpha.html").is_file()


# ── 6. 单一渲染器纯函数 ────────────────────────────────────


def test_render_article_html_escapes_frontmatter(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    p.write_text(
        '---\ntitle: "<script>alert(1)</script>"\n---\n# Hi\n', encoding="utf-8"
    )
    html = render_article_html(parse_article(p), date="2026-11-19")
    assert "<script>alert(1)</script>" not in html  # title 转义，未逃逸出 <title>
    assert "&lt;script&gt;" in html
    assert external_resource_refs(html) == []


def test_render_index_and_rss_empty() -> None:
    idx = render_index([], site_title="空站")
    assert "暂无已发布文章" in idx
    feed = render_rss([], site_link="https://x")
    assert feed.count("<item>") == 0
