"""render_checks 渲染级自动验收单测（§十二 F3）。

四类缺陷样例逐项触发对应级别；干净样例全部 PASS。
所有文件写 tmp_path，不触碰 ~/.musespiral。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from musespiral.core.content.parser import ParsedArticle
from musespiral.core.quality.render_checks import render_checks


def _article(cover: str = "") -> ParsedArticle:
    """构造只带 cover 元数据的文章（其余检查不依赖 blocks）。"""
    metadata: dict = {"title": "测试文章"}
    if cover:
        metadata["cover"] = cover
    return ParsedArticle(metadata=metadata, blocks=[], raw_body="")


def _make_image(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), "#336699").save(path)


def _levels(results) -> list[str]:
    return [lvl for lvl, _ in results]


# ---- 1. 外链残留 ----


def test_foreign_link_fails(tmp_path) -> None:
    html = '<p>参考 <a href="https://github.com/foo/bar">项目主页</a></p>'
    results = render_checks(html, _article(), tmp_path / "article.md")
    fails = [msg for lvl, msg in results if lvl == "FAIL"]
    assert len(fails) == 1
    assert "外链残留" in fails[0]
    assert "https://github.com/foo/bar" in fails[0]


def test_wechat_internal_link_exempt(tmp_path) -> None:
    html = '<p><a href="https://mp.weixin.qq.com/s/abc123">历史文章</a></p>'
    results = render_checks(html, _article(), tmp_path / "article.md")
    assert _levels(results) == ["PASS", "PASS", "PASS"]


# ---- 2. 正文图片外域 ----


def test_foreign_image_warns(tmp_path) -> None:
    html = '<img src="https://cdn.example.com/pic.png" alt="" />'
    results = render_checks(html, _article(), tmp_path / "article.md")
    warns = [msg for lvl, msg in results if lvl == "WARN"]
    assert len(warns) == 1
    assert "外域" in warns[0]
    assert "https://cdn.example.com/pic.png" in warns[0]


def test_mmbiz_and_local_images_pass(tmp_path) -> None:
    html = (
        '<img src="https://mmbiz.qpic.cn/mmbiz_png/abc/640?wx_fmt=png" alt="" />'
        '<img src="images/local.png" alt="" />'
    )
    results = render_checks(html, _article(), tmp_path / "article.md")
    assert "WARN" not in _levels(results)
    assert any("图片来源合规" in msg for _, msg in results)


# ---- 3. 封面比例 ----


def test_cover_ratio_in_tolerance_passes(tmp_path) -> None:
    _make_image(tmp_path / "assets" / "cover.png", 900, 383)  # 2.35:1 官方推荐
    article = _article(cover="assets/cover.png")
    results = render_checks("<p>正文</p>", article, tmp_path / "article.md")
    assert any(lvl == "PASS" and "2.35:1" in msg for lvl, msg in results)


def test_cover_ratio_inside_band_passes(tmp_path) -> None:
    _make_image(tmp_path / "cover.jpg", 800, 300)  # 2.67:1，仍在 ±25% 内
    article = _article(cover="cover.jpg")
    results = render_checks("<p>正文</p>", article, tmp_path / "article.md")
    assert "WARN" not in _levels(results)


def test_cover_ratio_square_warns(tmp_path) -> None:
    _make_image(tmp_path / "cover.jpg", 600, 600)  # 1.0:1，低于下界 1.76
    article = _article(cover="cover.jpg")
    results = render_checks("<p>正文</p>", article, tmp_path / "article.md")
    warns = [msg for lvl, msg in results if lvl == "WARN"]
    assert len(warns) == 1
    assert "封面宽高比" in warns[0]


def test_cover_ratio_too_wide_warns(tmp_path) -> None:
    _make_image(tmp_path / "cover.jpg", 700, 200)  # 3.5:1，高于上界 2.94
    article = _article(cover="cover.jpg")
    results = render_checks("<p>正文</p>", article, tmp_path / "article.md")
    warns = [msg for lvl, msg in results if lvl == "WARN"]
    assert len(warns) == 1
    assert "偏离" in warns[0]


def test_cover_missing_skips_ratio_check(tmp_path) -> None:
    # 封面缺失由 run_checks FAIL 覆盖，比例项静默跳过（无崩溃、无新增级别）
    results = render_checks("<p>正文</p>", _article(), tmp_path / "article.md")
    assert all(lvl == "PASS" for lvl in _levels(results))
    assert len(results) == 3  # 外链/图片/标签三项


# ---- 4. 标签白名单 ----


@pytest.mark.parametrize(
    "snippet",
    [
        "<script>alert(1)</script>",
        '<iframe src="https://evil.com"></iframe>',
        '<link rel="stylesheet" href="a.css">',
    ],
)
def test_forbidden_tags_fail(tmp_path, snippet: str) -> None:
    results = render_checks(f"<p>正文</p>{snippet}", _article(), tmp_path / "article.md")
    fails = [msg for lvl, msg in results if lvl == "FAIL"]
    assert len(fails) == 1
    assert "禁止标签" in fails[0]


def test_code_sample_not_flagged_as_script(tmp_path) -> None:
    # 代码块经转义后为 &lt;script&gt;，不应命中白名单检查
    html = "<pre>&lt;script&gt;alert(1)&lt;/script&gt;</pre>"
    results = render_checks(html, _article(), tmp_path / "article.md")
    assert "FAIL" not in _levels(results)


# ---- 干净样例：全部 PASS ----


def test_clean_article_all_pass(tmp_path) -> None:
    _make_image(tmp_path / "cover.png", 900, 383)
    html = (
        "<section>开头</section>"
        '<p><a href="https://mp.weixin.qq.com/s/abc">内链</a></p>'
        '<img src="https://mmbiz.qpic.cn/mmbiz_jpg/xyz/640" alt="" />'
        "<p>结尾</p>"
    )
    article = _article(cover="cover.png")
    results = render_checks(html, article, tmp_path / "article.md")
    assert _levels(results) == ["PASS", "PASS", "PASS", "PASS"]
