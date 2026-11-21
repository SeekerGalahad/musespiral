"""review（F8 对照审阅）的单元测试 — 四类段落分类/着色、统计一致、空端不崩。"""
from __future__ import annotations

import re

import pytest

from musespiral.core.quality.review import build_review_html

# 一组「保留×2 / 修改×1 / 删除×1 / 新增×1」的构造用段落
KEPT1 = "缓存是提升系统性能的第一道防线。"
MOD_OLD = "过期时间建议设置为三十分钟。"
MOD_NEW = "过期时间建议设置为一小时。"
DELETED = "大家好，欢迎关注我的频道。"
KEPT2 = "合理设置过期时间同样关键。"
ADDED = "最后记得点赞在看，我们下期见。"

_STATS_RE = re.compile(
    r"段落统计：保留 (?P<keep>\d+) · 修改 (?P<modify>\d+) · 新增 (?P<add>\d+) "
    r"· 删除 (?P<delete>\d+) · 字符保留率 (?P<retention>[\d.]+%|—)"
)


@pytest.fixture(autouse=True)
def _mdpub_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))


def _pair(tmp_path, note_text: str, article_text: str):
    note = tmp_path / "note.md"
    article = tmp_path / "article.md"
    note.write_text(note_text, encoding="utf-8")
    article.write_text(article_text, encoding="utf-8")
    return note, article


def _stats(page: str) -> dict[str, str]:
    m = _STATS_RE.search(page)
    assert m, "统计条缺失或格式不符"
    return m.groupdict()


def test_four_kinds_classified_and_colored(tmp_path) -> None:
    note, article = _pair(
        tmp_path,
        "\n\n".join([KEPT1, MOD_OLD, DELETED, KEPT2]),
        "\n\n".join([KEPT1, MOD_NEW, KEPT2, ADDED]),
    )
    page = build_review_html(note, article)

    # 保留：两侧同文、keep 类、无高亮标签
    assert f'<td class="keep">{KEPT1}</td><td class="keep">{KEPT1}</td>' in page
    assert f'<td class="keep">{KEPT2}</td><td class="keep">{KEPT2}</td>' in page
    # 删除：仅左格有文且红底类，右格为空
    assert f'<td class="delete">{DELETED}</td><td></td>' in page
    # 新增：仅右格有文且绿底类，左格为空
    assert f'<td></td><td class="add">{ADDED}</td>' in page
    # 修改：两侧黄色描边类，且段内词级差异被 <del>/<ins> 高亮
    assert page.count('<td class="modify">') == 2
    assert "<del>三十分钟</del>" in page
    assert "<ins>一小时</ins>" in page
    assert "过期时间建议设置为" in page  # 共同词两侧原样可见


def test_stats_match_construction(tmp_path) -> None:
    note, article = _pair(
        tmp_path,
        "\n\n".join([KEPT1, MOD_OLD, DELETED, KEPT2]),
        "\n\n".join([KEPT1, MOD_NEW, KEPT2, ADDED]),
    )
    s = _stats(build_review_html(note, article))
    assert (s["keep"], s["modify"], s["add"], s["delete"]) == ("2", "1", "1", "1")
    # 字符保留率 = 未变段字符和（KEPT1+KEPT2）/ 原文四段总字符
    total = len(KEPT1) + len(MOD_OLD) + len(DELETED) + len(KEPT2)
    expected = f"{(len(KEPT1) + len(KEPT2)) / total * 100:.1f}%"
    assert s["retention"] == expected


def test_identical_texts_all_kept(tmp_path) -> None:
    text = "\n\n".join([KEPT1, KEPT2])
    note, article = _pair(tmp_path, text, text)
    page = build_review_html(note, article)
    s = _stats(page)
    assert (s["keep"], s["modify"], s["add"], s["delete"]) == ("2", "0", "0", "0")
    assert s["retention"] == "100.0%"
    assert '<td class="delete">' not in page
    assert '<td class="add">' not in page
    assert '<td class="modify">' not in page
    assert "<del>" not in page and "<ins>" not in page


def test_empty_note_all_added(tmp_path) -> None:
    note, article = _pair(tmp_path, "", "\n\n".join([KEPT1, ADDED]))
    page = build_review_html(note, article)
    s = _stats(page)
    assert (s["keep"], s["modify"], s["add"], s["delete"]) == ("0", "0", "2", "0")
    assert s["retention"] == "—"  # 原文无字符，保留率无定义而非除零
    assert page.count('<td></td><td class="add">') == 2
    assert '<td class="delete">' not in page


def test_empty_article_all_deleted(tmp_path) -> None:
    note, article = _pair(tmp_path, "\n\n".join([KEPT1, KEPT2]), "")
    page = build_review_html(note, article)
    s = _stats(page)
    assert (s["keep"], s["modify"], s["add"], s["delete"]) == ("0", "0", "0", "2")
    assert s["retention"] == "0.0%"
    assert page.count(f'<td class="delete">{KEPT1}</td><td></td>') == 1
    assert '<td class="add">' not in page


def test_frontmatter_excluded_from_diff(tmp_path) -> None:
    note, article = _pair(
        tmp_path,
        "---\ntitle: 原笔记\ndigest: 笔记摘要\n---\n\n" + KEPT1,
        "---\ntitle: 文章\ndigest: 文章摘要\n---\n\n" + KEPT1,
    )
    page = build_review_html(note, article)
    assert "digest:" not in page and "title:" not in page
    assert "---" not in page
    s = _stats(page)
    assert (s["keep"], s["modify"], s["add"], s["delete"]) == ("1", "0", "0", "0")


def test_html_escaped(tmp_path) -> None:
    para = "段落含 <b>加粗</b> 与 <script>alert(1)</script>。"
    note, article = _pair(tmp_path, para, para)
    page = build_review_html(note, article)
    assert "&lt;b&gt;加粗&lt;/b&gt;" in page
    assert "&lt;script&gt;" in page
    assert "<b>加粗" not in page
    assert "<script>alert" not in page
