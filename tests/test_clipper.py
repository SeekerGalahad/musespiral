"""B 采集扩展单元测试（§五 5.2）：infra/sources/clipper 正文提取
（T66-1/T66-2 降级链 + T67-4 pre/code 保留）——readability-lxml 离线 fixtures。
"""
from __future__ import annotations

from musespiral.infra.sources import clipper

BLOG_HTML = """<html><head><title>My Tech Blog - SiteName</title></head><body>
<nav><a href="/">home</a> <a href="/a">link1</a> <a href="/b">link2</a> <a href="/c">link3</a></nav>
<article>
<h1>Deep dive into parsers</h1>
<p>This is the main content paragraph about parsing technology. It has enough
substantial words to be detected as the readable main container by the scoring
heuristics of the extraction library used here today.</p>
<p>Second paragraph continues the discussion with more substantial text about
how content extraction works and which tradeoffs exist between the different
approaches to readability scoring and density based extraction.</p>
<pre><code>def parse(html):
    return html.strip()  # 保留缩进
</code></pre>
<p>Closing thoughts wrap up the article nicely with a summary of what we
learned today about readable extraction and confidence marking.</p>
</article>
<footer>copyright site nav links terms privacy about contact more links</footer>
</body></html>
"""

LIST_HTML = (
    """<html><head><title>Posts</title></head><body>
<nav>home about archive tags categories rss twitter github newsletter subscribe search</nav>
<div class="listing">"""
    + "".join(
        f'<div class="item"><a href="/p{i}">Post number {i} about topic {i}</a>'
        f"<span>2026-01-0{i % 9 + 1}</span></div>"
        for i in range(40)
    )
    + """</div>
<footer>copyright nav links terms privacy about contact</footer></body></html>"""
)


def test_t66_1_blog_fixture_full_confidence() -> None:
    result = clipper.extract(BLOG_HTML)
    assert result["confidence"] >= 0.5
    assert result["text"].strip()
    assert result["word_count"] > 0
    assert result["title"]  # short_title 非空
    assert "Deep dive" in result["text"]


def test_t66_2_list_fixture_degrades_low_confidence() -> None:
    result = clipper.extract(LIST_HTML)
    assert result["confidence"] < 0.5  # 降级链输入（mode 裁决归服务层）
    assert result["text"] == ""  # 列表页无可读正文


def test_t67_4_pre_code_preserved_verbatim() -> None:
    result = clipper.extract(BLOG_HTML)
    text = result["text"]
    assert "```" in text  # Markdown 围栏块
    assert "def parse(html):" in text
    assert "return html.strip()  # 保留缩进" in text  # 代码文本逐字保留


def test_license_note_detection_rel_license() -> None:
    html = BLOG_HTML.replace(
        "<footer>",
        '<a rel="license" href="https://creativecommons.org/licenses/by-nc/4.0/">CC</a>\n<footer>',
    )
    result = clipper.extract(html)
    assert "creativecommons.org/licenses/by-nc/4.0/" in result["license_note"]
    # 无 CC 标记页面 → 空串（枚举/缺省推断归服务层）
    assert clipper.extract(BLOG_HTML)["license_note"] == ""
