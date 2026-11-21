"""scorer.py 单元测试（F27）——纯函数参数化：行数档位/tags 门槛/章节有无等边界。"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from musespiral.core.discovery import scorer

TODAY = date(2026, 11, 14)


def make_meta(
    *,
    lines: int = 200,
    tags: list[str] | None = None,
    sections: list[str] | None = None,
    created: str = "2026-11-01",
    category: str = "13-source-analysis",
    pattern_titles: list[str] | None = None,
) -> dict:
    return {
        "title": "源码分析-Demo",
        "tags": ["源码分析", "Python", "类型安全", "demo/demo"] if tags is None else tags,
        "created": created,
        "category": category,
        "line_count": lines,
        "sections": ["四、端到端数据流", "五、设计思维提炼"] if sections is None else sections,
        "pattern_titles": pattern_titles or [],
    }


def depth_of(result: dict) -> int:
    return result["score_breakdown"]["depth"]


# ── 内容深度：行数档位边界（99/100/150/200）───────────────────────────


@pytest.mark.parametrize(
    "lines,expect",
    [
        (0, 0), (99, 0),
        (100, 10), (149, 10),
        (150, 15), (199, 15),
        (200, 20), (1000, 20),
    ],
)
def test_depth_line_buckets(lines, expect):
    result = scorer.score_doc(make_meta(lines=lines, sections=[]), today=TODAY)
    assert depth_of(result) == expect


def test_depth_sections_add_10_each():
    base = scorer.score_doc(make_meta(sections=[]), today=TODAY)
    with_thinking = scorer.score_doc(make_meta(sections=["五、设计思维提炼"]), today=TODAY)
    with_flow = scorer.score_doc(make_meta(sections=["四、端到端数据流"]), today=TODAY)
    with_both = scorer.score_doc(
        make_meta(sections=["四、端到端数据流", "五、设计思维提炼"]), today=TODAY
    )
    assert depth_of(with_thinking) - depth_of(base) == 10
    assert depth_of(with_flow) - depth_of(base) == 10
    assert depth_of(with_both) - depth_of(base) == 20


def test_depth_section_match_is_substring():
    # 真实标题带序号与括注，按含关键词匹配
    result = scorer.score_doc(
        make_meta(sections=["四、端到端数据流（写入路径）", "五、设计思维提炼"]), today=TODAY
    )
    assert depth_of(result) == 20 + 10 + 10


def test_depth_capped_at_40():
    result = scorer.score_doc(make_meta(lines=500), today=TODAY)
    assert depth_of(result) == 40


# ── 知识独特性：owner/repo tag + 模式家族成员 ──────────────────────────


def test_uniqueness_owner_repo_tag():
    with_tag = scorer.score_doc(make_meta(tags=["源码分析", "a/b"]), today=TODAY)
    without = scorer.score_doc(make_meta(tags=["源码分析", "Python"]), today=TODAY)
    u = "uniqueness"
    assert with_tag["score_breakdown"][u] - without["score_breakdown"][u] == 15
    assert with_tag["matched"]["owner_repo"] == "a/b"
    assert without["matched"]["owner_repo"] is None


def test_uniqueness_family_membership():
    families = {"固定四节点小图 vs 手写循环", "别的族"}
    hit = scorer.score_doc(
        make_meta(pattern_titles=["无关条目", "固定四节点小图 vs 手写循环"]),
        families=families,
        today=TODAY,
    )
    miss = scorer.score_doc(make_meta(pattern_titles=["未收录条目"]), families=families, today=TODAY)
    u = "uniqueness"
    assert hit["score_breakdown"][u] == 30  # owner/repo 15 + 家族 15
    assert miss["score_breakdown"][u] == 15
    assert hit["matched"]["families"] == ["固定四节点小图 vs 手写循环"]


@pytest.mark.parametrize("families", [None, set()])
def test_uniqueness_families_none_or_empty_no_crash(families):
    result = scorer.score_doc(make_meta(pattern_titles=["任何条目"]), families=families, today=TODAY)
    assert result["score_breakdown"]["uniqueness"] == 15  # 仅 owner/repo 分
    assert result["matched"]["families"] == []


# ── 可读性：tags 数量门槛 + 新鲜度 ────────────────────────────────────


@pytest.mark.parametrize(
    "tags,expect",
    [
        (["源码分析"], 0),
        (["源码分析", "Python", "类型安全"], 0),  # 3 项不达标
        (["源码分析", "Python", "类型安全", "demo/demo"], 10),  # 4 项达标（owner/repo 也计 tags 数）
    ],
)
def test_readability_tags_floor(tags, expect):
    result = scorer.score_doc(make_meta(tags=tags, created=""), today=TODAY)
    assert result["score_breakdown"]["readability"] == expect


def test_readability_freshness_boundaries():
    fresh = scorer.score_doc(make_meta(created=(TODAY - timedelta(days=30)).isoformat()), today=TODAY)
    edge_in = scorer.score_doc(make_meta(created=(TODAY - timedelta(days=0)).isoformat()), today=TODAY)
    stale = scorer.score_doc(make_meta(created=(TODAY - timedelta(days=31)).isoformat()), today=TODAY)
    r = "readability"
    # tags 4 项的 10 分打底，新鲜度再 +10
    assert fresh["score_breakdown"][r] == 20
    assert edge_in["score_breakdown"][r] == 20
    assert stale["score_breakdown"][r] == 10


@pytest.mark.parametrize("created", ["", "2026-13-99", "not-a-date", "2026/11/01"])
def test_readability_bad_created_scores_zero_from_freshness(created):
    result = scorer.score_doc(make_meta(created=created), today=TODAY)
    assert result["score_breakdown"]["readability"] == 10  # 仅 tags 分


def test_readability_future_created_not_fresh():
    # 未来日期（created 晚于基准日）视为数据错误：不给新鲜度分
    result = scorer.score_doc(make_meta(created=(TODAY + timedelta(days=5)).isoformat()), today=TODAY)
    assert result["score_breakdown"]["readability"] == 10  # 仅 tags 分


# ── 适配潜力：类别分 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "category,expect",
    [("13-source-analysis", 10), ("11-learning-path", 5), ("09-cross-cutting", 0), ("", 0)],
)
def test_adaptability_by_category(category, expect):
    result = scorer.score_doc(make_meta(category=category), today=TODAY)
    assert result["score_breakdown"]["adaptability"] == expect


# ── 总分与四维同源 ────────────────────────────────────────────────────


def test_score_equals_sum_of_breakdown():
    result = scorer.score_doc(make_meta(), families={"源码分析-Demo::不存在"}, today=TODAY)
    bd = result["score_breakdown"]
    assert set(bd) == {"depth", "uniqueness", "readability", "adaptability"}
    assert result["score"] == sum(bd.values())


def test_full_marks_doc():
    result = scorer.score_doc(
        make_meta(lines=400, pattern_titles=["固定四节点小图 vs 手写循环"]),
        families={"固定四节点小图 vs 手写循环"},
        today=TODAY,
    )
    assert result["score"] == 100
    assert result["score_breakdown"] == {
        "depth": 40, "uniqueness": 30, "readability": 20, "adaptability": 10,
    }
