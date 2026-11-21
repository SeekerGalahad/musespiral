"""suggester.py 单元测试（F28）——规则路径 series/hint/reason + mock AI 增强路径。"""
from __future__ import annotations

import pytest

from musespiral.core.discovery import suggester
from musespiral.infra.ai import AIError

TODAY_META = {
    "path": "/kb/源码分析-FastDB.md",
    "title": "源码分析-FastDB",
    "tags": ["源码分析", "Python", "高性能-数据库", "demo/fastdb"],
    "created": "2026-11-01",
    "category": "13-source-analysis",
    "line_count": 260,
    "headings": [
        (1, "源码分析：FastDB"),
        (2, "一、项目概述"),
        (2, "三、核心模块分析（存储引擎）"),
        (3, "3.1 MergeTree 引擎：列式压缩"),
        (2, "四、端到端数据流"),
        (2, "五、设计思维提炼"),
        (3, "5.1 关键权衡"),
        (3, "5.2 演进推断"),
        (3, "5.3 同域对照"),
        (3, "5.4 可迁移决策"),
    ],
    "sections": [
        "一、项目概述", "三、核心模块分析（存储引擎）", "四、端到端数据流", "五、设计思维提炼",
    ],
    "pattern_titles": [],
    "family_repos": None,
}


def scored(matched=None):
    return {
        "score": 90,
        "score_breakdown": {"depth": 40, "uniqueness": 30, "readability": 10, "adaptability": 10},
        "matched": matched or {"owner_repo": "demo/fastdb", "families": []},
    }


# ── series_for ────────────────────────────────────────────────────────


def test_series_for_source_analysis():
    assert suggester.series_for(TODAY_META) == "开源项目深度解读"


def test_series_for_family_card_beats_category():
    meta = dict(TODAY_META, title="模式家族-演示族")
    assert suggester.series_for(meta) == "跨仓模式拆解"


def test_series_for_learning_path_and_default():
    assert suggester.series_for({"title": "01-指南", "category": "11-learning-path"}) == "学习路径入门"
    assert suggester.series_for({"title": "随便", "category": "09-x"}) == "知识库精选"


# ── skeleton_hint_for（F28 三形态规则）────────────────────────────────


def test_hint_source_analysis_trait_and_core():
    hint = suggester.skeleton_hint_for(TODAY_META)
    # tags 含「高性能」→ 快；核心技术取 核心模块 首个小节标题（去编号前缀）
    assert hint == "为什么 FastDB 这么快？——MergeTree 引擎：列式压缩 深度解析"


@pytest.mark.parametrize(
    "tag,trait",
    [("安全-框架", "可靠"), ("优雅-API", "优雅"), ("普通-工具", "流行")],
)
def test_hint_trait_keyword_map(tag, trait):
    meta = dict(TODAY_META, tags=["源码分析", tag])
    assert f"这么{trait}" in suggester.skeleton_hint_for(meta)


def test_hint_core_fallback_without_module_section():
    meta = {**TODAY_META, "headings": [(1, "源码分析：X"), (2, "二、架构总览")], "sections": ["二、架构总览"]}
    assert suggester.skeleton_hint_for(meta).endswith("——架构总览 深度解析")


def test_hint_learning_path():
    meta = {"title": "01-AI开发入门指南", "category": "11-learning-path"}
    assert suggester.skeleton_hint_for(meta) == "AI开发入门指南：从零到一"
    meta2 = {"title": "03-深度学习与PyTorch", "category": "11-learning-path"}
    assert suggester.skeleton_hint_for(meta2) == "深度学习与PyTorch 入门指南：从零到一"


def test_hint_family_card_with_and_without_count():
    card = {"title": "模式家族-稳定 ABI 边界墙", "category": "13-source-analysis", "family_repos": 7}
    assert suggester.skeleton_hint_for(card) == "7 个开源项目怎么解决同一个问题：稳定 ABI 边界墙"
    card_none = dict(card, family_repos=None)
    assert suggester.skeleton_hint_for(card_none) == "多个开源项目怎么解决同一个问题：稳定 ABI 边界墙"


# ── reason_for ────────────────────────────────────────────────────────


def test_reason_full_facts():
    reason = suggester.reason_for(TODAY_META, scored())
    assert "260 行深度内容" in reason
    assert "四问齐全" in reason  # 5.1-5.4 四小节齐
    assert "含端到端数据流" in reason
    assert "可追溯 demo/fastdb" in reason
    assert "tags 4 项" in reason


def test_reason_partial_facts():
    meta = {
        "title": "短文", "tags": ["a", "b"], "line_count": 50,
        "sections": ["五、设计思维提炼"],
        "headings": [(2, "五、设计思维提炼"), (3, "5.1 关键权衡")],
    }
    reason = suggester.reason_for(meta, {"matched": {}})
    assert reason == "含设计思维提炼章节"


def test_reason_fallback_when_no_facts():
    assert suggester.reason_for({"title": "x", "tags": [], "line_count": 5}, {"matched": {}}) == "知识库候选"


# ── suggest_batch：series_no 分配 ──────────────────────────────────────


def test_suggest_batch_orders_and_numbers_per_series():
    a = {"meta": {**TODAY_META, "path": "/a.md"}, "score": 70}
    b = {"meta": {**TODAY_META, "path": "/b.md"}, "score": 90}
    c = {"meta": {"title": "01-指南", "category": "11-learning-path", "path": "/c.md"}, "score": 80}
    out = suggester.suggest_batch([a, b, c])
    assert [s["series_no"] for s in out] == [2, 1, 1]  # b(90)先领 1，a 领 2；c 独立系列
    assert out[1]["series"] == "开源项目深度解读"
    assert out[2]["series"] == "学习路径入门"
    assert all({"series", "series_no", "reason", "skeleton_hint"} == set(s) for s in out)


def test_suggest_batch_existing_counts_seed_numbering():
    item = {"meta": TODAY_META, "score": 50}
    out = suggester.suggest_batch([item], existing_counts={"开源项目深度解读": 11})
    assert out[0]["series_no"] == 12


def test_suggest_batch_tie_breaks_by_path():
    a = {"meta": {**TODAY_META, "path": "/b.md"}, "score": 90}
    b = {"meta": {**TODAY_META, "path": "/a.md"}, "score": 90}
    out = suggester.suggest_batch([a, b])
    assert out[1]["series_no"] == 1 and out[0]["series_no"] == 2  # a.md 先领 1


# ── ai_enhance：mock AI 路径 ──────────────────────────────────────────


def _items(n):
    return [
        {"title": f"文档{i}", "path": f"/d{i}.md", "score": 100 - i, "skeleton_hint": f"角度{i}"}
        for i in range(n)
    ]


def test_ai_enhance_mock_chat_sets_fields():
    calls = []

    def fake_chat(messages, **kw):
        calls.append(messages)
        return "AI 起的标题\nAI 写的导语"

    items = _items(3)
    n = suggester.ai_enhance(items, top_n=2, chat=fake_chat)
    assert n == 2 and len(calls) == 2  # top_n 截断
    assert items[0]["ai_title"] == "AI 起的标题"
    assert items[0]["ai_intro"] == "AI 写的导语"
    assert "ai_title" not in items[2]  # top_n 之外不动


def test_ai_enhance_ai_error_skips_without_crash():
    def bad_chat(messages, **kw):
        raise AIError("AI 未配置")

    items = _items(2)
    assert suggester.ai_enhance(items, chat=bad_chat) == 0
    assert all("ai_title" not in it for it in items)


def test_ai_enhance_malformed_reply_skipped():
    def one_line_chat(messages, **kw):
        return "只有一行"

    items = _items(1)
    assert suggester.ai_enhance(items, chat=one_line_chat) == 0
    assert "ai_title" not in items[0]
