"""core/composition 领域单测（第十八期 §2.8 块协议/会话/合并/守门清单）。

覆盖：定界解析容错 / 锚继承 / content-hash 与相似度回退 / 歧义恒新块 /
draft.md 双向 round-trip / 人工改文件锚存活 / 素材预算截断 / 轮次窗口 K /
三态合并幂等 / habits 训练对 / 架构门（core/composition 纯逻辑零 I/O）。
"""
from __future__ import annotations

import ast
import difflib
from pathlib import Path

import pytest

from musespiral.core.composition import (
    KINDS,
    STATUS_COMPOSING,
    Block,
    CompositionSession,
    ParsedBlock,
    Round,
    SourceRef,
    apply_decisions,
    blocks_from_markdown,
    excerpt_sources,
    habits_pairs,
    markdown_from_blocks,
    match_round,
    parse_ai_output,
    round_diff_summary,
    select_sections,
    validate_decision,
)
from musespiral.core.composition import protocol as protocol_mod
from musespiral.core.composition import session as session_mod

PKG = Path(__file__).resolve().parent.parent / "musespiral" / "core" / "composition"


# ── draft.md 双向 round-trip（§2.2）────────────────────────────────────


def test_roundtrip_all_kinds() -> None:
    """五类块 → markdown → 解析，逐块 (id, kind, text) 无损往返。"""
    blocks = [
        Block("b1", "heading", "# 缓存设计实录"),
        Block("b2", "para", "第一段正文。\n第二行仍在同一段。"),
        Block("b3", "code", "```python\ncache = {}\n```"),
        Block("b4", "quote", "> 原文引述第一行\n> 引述第二行"),
        Block("b5", "image", "![架构图](./assets/arch.png)"),
        Block("b6", "para", "收尾段。"),
    ]
    text = markdown_from_blocks(blocks)
    assert "<!--b:b1-->" in text and "<!--b:b6-->" in text
    got = blocks_from_markdown(text)
    assert [(b.id, b.kind, b.text) for b in got] == [(b.id, b.kind, b.text) for b in blocks]


def test_roundtrip_empty_and_anchorless() -> None:
    assert markdown_from_blocks([]) == ""
    assert blocks_from_markdown("") == []
    # 无锚文本（人工裸写）→ 解析自动补 fresh id，kind 推断正确
    got = blocks_from_markdown("裸写标题下的段落。")
    assert len(got) == 1 and got[0].id == "b1" and got[0].kind == "para"


def test_human_edit_keeps_anchor_and_id() -> None:
    """人工改文件：锚行未动只改正文 → id 存活；删除锚行 → 该块按新块（新 id）。"""
    blocks = [Block("b1", "para", "原始段落内容")]
    text = markdown_from_blocks(blocks)
    edited = text.replace("原始段落内容", "人工改写后的段落内容")
    got = blocks_from_markdown(edited)
    assert len(got) == 1 and got[0].id == "b1" and got[0].text == "人工改写后的段落内容"
    # 锚破坏（删锚行）：按新块处理——avoid 传会话既有 id，裸文本绝不撞回旧 id
    broken = edited.replace("<!--b:b1-->\n", "")
    got2 = blocks_from_markdown(broken, avoid={"b1"})
    assert got2[0].id != "b1"
    assert got2[0].text == "人工改写后的段落内容"


def test_anchor_binds_next_block_and_multi_anchor_takes_last() -> None:
    text = "<!--b:b9-->\n<!--b:b8-->\n实际段落"
    got = blocks_from_markdown(text)
    assert got[0].id == "b8"  # 连续多锚：最靠近块者生效


# ── 定界解析（§2.2 层1：容错）─────────────────────────────────────────


def test_parse_delimited_output() -> None:
    out = (
        "<<<block kind:heading>>># 标题\n\n"
        "<<<block id:b2 kind:para>>>改写后的段落，携带上轮锚。\n\n"
        "<<<block kind:code>>>```python\nx = 1\n```\n\n"
        "<<<block kind:para>>>"
    )
    parsed = parse_ai_output(out)
    assert [(p.anchor, p.kind) for p in parsed] == [
        (None, "heading"),
        ("b2", "para"),
        (None, "code"),
    ]
    assert parsed[0].text == "# 标题"
    assert parsed[2].text == "```python\nx = 1\n```"


def test_parse_no_delimiter_degrades_to_single_para() -> None:
    """定界整体缺失：整段降级为单 para 块（不报错——协议容错）。"""
    parsed = parse_ai_output("AI 忘了协议，直接输出整篇正文。\n\n第二段也在里面。")
    assert len(parsed) == 1
    assert parsed[0].kind == "para" and parsed[0].anchor is None
    assert "第二段也在里面。" in parsed[0].text


def test_parse_tolerates_bad_kind_and_noise() -> None:
    parsed = parse_ai_output("前置噪声行。\n<<<block kind:table>>>表格内容")
    assert len(parsed) == 1 and parsed[0].kind == "para"  # 非法 kind → para；定界前噪声忽略
    assert parse_ai_output("") == []
    # 空段跳过：首块无内容不产块，只留第二块
    assert parse_ai_output("<<<block kind:para>>>\n\n<<<block kind:para>>>有内容") == [
        ParsedBlock(None, "para", "有内容")
    ]


# ── 无锚回退匹配（§2.2 层2：hash → 相似度 → 新块；歧义恒新块）─────────


def _prev(*texts: str) -> list[Block]:
    return [Block(f"b{i}", "para", t) for i, t in enumerate(texts, 1)]


def test_match_anchor_inherits_id() -> None:
    previous = _prev("段落一", "段落二", "段落三")
    parsed = parse_ai_output("<<<block id:b2 kind:para>>>完全改写过的段落二")
    got = match_round(parsed, previous, {"b1", "b2", "b3"})
    assert got[0].id == "b2"  # 锚继承：文本变了 id 不变


def test_match_anchor_occupied_falls_through_and_new() -> None:
    """两块同锚 b1：第一块继承，第二块不可双承（占用）→ 相似度也只对未占用者。"""
    previous = _prev("段落一原文")
    parsed = [
        p for p in parse_ai_output(
            "<<<block id:b1 kind:para>>>段落一原文改写版"
            "\n\n<<<block id:b1 kind:para>>>另一段完全不同的内容"
        )
    ]
    got = match_round(parsed, previous, {"b1"})
    assert got[0].id == "b1"
    assert got[1].id == "b2"  # 锚被占 → 回退链 → 无相似 → 新块


def test_match_anchor_unknown_id_treated_as_unanchored() -> None:
    previous = _prev("段落一原文")
    parsed = parse_ai_output("<<<block id:zz9 kind:para>>>段落一原文")  # 幻觉锚 + 原文
    got = match_round(parsed, previous, {"b1"})
    assert got[0].id == "b1"  # 幻觉锚不当新块——落回 hash 命中继承


def test_match_content_hash_exact() -> None:
    previous = _prev("精确不变的段落", "另一段")
    parsed = parse_ai_output("<<<block kind:para>>>另一段")
    got = match_round(parsed, previous, {"b1", "b2"})
    assert got[0].id == "b2"  # 无锚但文本精确一致 → hash 命中


def test_match_similarity_fallback() -> None:
    base = "缓存穿透的三种解法：空值缓存、布隆过滤器、互斥重建。"
    edited = base + "实践中首选布隆过滤器。"
    assert difflib.SequenceMatcher(None, edited, base).ratio() >= 0.6  # 前置：确在阈值上
    previous = _prev(base, "毫不相关的另一段文字内容用于干扰")
    parsed = parse_ai_output(f"<<<block kind:para>>>{edited}")
    got = match_round(parsed, previous, {"b1", "b2"})
    assert got[0].id == "b1"  # 相似度最高分且未占用 → 继承


def test_match_similarity_below_threshold_is_new_block() -> None:
    previous = _prev("完全不同的一段旧文字，讲的是另一个主题 entirely different")
    parsed = parse_ai_output("<<<block kind:para>>>崭新的段落，与上轮毫无重叠，主题全新。")
    got = match_round(parsed, previous, {"b1"})
    assert got[0].id == "b2"  # 相似度不足 → 新块


def test_match_ambiguity_tie_always_new_block() -> None:
    """歧义恒新块（铁律）：两候选与新文本比分完全并列 → 新块，绝不猜。"""
    same = "两个上一轮块内容一模一样，构造相似度精确并列的歧义场景。"
    previous = _prev(same, same)
    assert previous[0].id != previous[1].id
    edited = same + "尾部稍有追加。"
    # 前置自证：两候选比分精确并列（同一文本→同一 ratio）
    r1 = difflib.SequenceMatcher(None, edited, previous[0].text).ratio()
    r2 = difflib.SequenceMatcher(None, edited, previous[1].text).ratio()
    assert r1 == r2 and r1 >= 0.6
    parsed = parse_ai_output(f"<<<block kind:para>>>{edited}")
    got = match_round(parsed, previous, {"b1", "b2"})
    assert got[0].id == "b3"  # 并列最高分 → 歧义 → 恒新块


def test_match_new_ids_avoid_session_taken() -> None:
    """fresh id 避开会话已占用的 b 系序号（draft+既有轮 taken 集）。"""
    parsed = parse_ai_output("<<<block kind:para>>>全新内容一\n\n<<<block kind:para>>>全新内容二")
    got = match_round(parsed, [], {"b1", "b3", "other"})
    assert [b.id for b in got] == ["b4", "b5"]


# ── Decision 三态（§2.1）──────────────────────────────────────────────


def test_validate_decision_three_states() -> None:
    assert validate_decision("keep", None, None) == {"action": "keep"}
    got = validate_decision("adopt_edited", " 人工定稿 ", None)
    assert got == {"action": "adopt_edited", "text": "人工定稿"}
    assert validate_decision("discard", None, "太啰嗦，压缩") == {
        "action": "discard",
        "direction": "太啰嗦，压缩",
    }
    with pytest.raises(ValueError, match="未知裁决动作"):
        validate_decision("maybe", None, None)
    with pytest.raises(ValueError, match="text"):
        validate_decision("adopt_edited", "  ", None)
    with pytest.raises(ValueError, match="direction"):
        validate_decision("discard", None, "")


# ── 素材节选与预算（§2.3 token 治理）─────────────────────────────────


def test_select_sections_picks_chosen_h2() -> None:
    body = "引言不入任何章节。\n\n## 一、概述\n\n概述正文。\n\n### 子小节\n\n子内容。\n\n## 二、架构\n\n架构正文。"
    got = select_sections(body, ["一、概述"])
    assert "概述正文。" in got and "子内容。" in got
    assert "架构正文。" not in got and "引言不入任何章节" not in got
    assert select_sections(body, []) == body.strip()  # 空选择=全文


def test_excerpt_sources_per_source_budget() -> None:
    sources = [{"path": "a.md", "title": "A", "content": "长" * 3000}]
    text, truncated = excerpt_sources(sources, per_limit=2000, total_limit=8000)
    assert truncated is True
    assert session_mod.TRUNCATED_MARK in text
    assert text.count("长") == 2000  # 截到预算位


def test_excerpt_sources_session_total_budget_and_placeholder() -> None:
    sources = [
        {"path": "a.md", "title": "A", "content": "甲" * 2000},
        {"path": "b.md", "title": "B", "content": "乙" * 2000},
        {"path": "c.md", "title": "C", "content": "丙" * 1000},
        {"path": "d.md", "title": "D", "content": "丁" * 3000},
    ]
    text, truncated = excerpt_sources(sources, per_limit=2000, total_limit=5000)
    assert truncated is True
    assert "甲" in text and "乙" in text and "丙" in text
    # 前三素材吃满 5000 总预算 → 第四素材整段占位（不静默丢弃）
    assert "因会话总预算已节选略" in text
    assert "丁" not in text


# ── 轮次窗口 K（§2.3：只带最近 K=2 轮块级 diff 摘要）──────────────────


def test_round_diff_summary_window_k() -> None:
    rounds = [
        Round(n=1, blocks=[Block("b1", "para", "一"), Block("b2", "para", "二")]),
        Round(n=2, blocks=[Block("b1", "para", "一"), Block("b3", "para", "三")]),
        Round(n=3, blocks=[Block("b3", "para", "三"), Block("b4", "para", "四")]),
    ]
    draft: list[Block] = []
    summary = round_diff_summary(rounds, draft, k=2)
    assert "第 2 轮" in summary and "第 3 轮" in summary
    assert "第 1 轮" not in summary  # 窗口外整轮不进摘要
    assert "b2" in summary  # 消失块一瞥（第 2 轮丢 b2 / 第 3 轮丢 b1）
    assert "b1" in summary


def test_round_diff_summary_baseline_is_draft_for_first_round() -> None:
    draft = [Block("h1", "heading", "# 预置稿标题")]
    rounds = [Round(n=1, blocks=[Block("b1", "para", "AI 首轮段")])]
    summary = round_diff_summary(rounds, draft, k=2)
    assert "第 1 轮" in summary and "h1" in summary  # 基线=当前真稿


# ── 三态合并（§2.4 幂等）与 habits 训练对 ────────────────────────────


def _round_blocks() -> list[Block]:
    return [
        Block("b1", "para", "AI 段一"),
        Block("b2", "para", "AI 段二，综上所述收尾。"),
        Block("b3", "para", "AI 段三"),
    ]


def _decisions() -> list[dict]:
    return [
        {"block": "b1", "action": "keep"},
        {"block": "b2", "action": "adopt_edited", "text": "人工段二，总的来说收尾。"},
        {"block": "b3", "action": "discard", "direction": "冗余"},
    ]


def test_apply_decisions_three_states_and_idempotent() -> None:
    merged = apply_decisions(_round_blocks(), _decisions())
    assert [(b.id, b.origin, b.text) for b in merged] == [
        ("b1", "ai", "AI 段一"),
        ("b2", "human", "人工段二，总的来说收尾。"),
    ]  # discard 弃；keep 血统 ai；adopt_edited 血统 human
    # 幂等：重复应用同结果
    assert [b.to_dict() for b in merged] == [
        b.to_dict() for b in apply_decisions(_round_blocks(), _decisions())
    ]
    # 未裁决块不入稿（AI 产预览绝不直写）
    partial = apply_decisions(_round_blocks(), [{"block": "b1", "action": "keep"}])
    assert [b.id for b in partial] == ["b1"]
    # 同块改主意：末次胜出
    changed = _decisions() + [{"block": "b1", "action": "discard", "direction": "改弃"}]
    assert [b.id for b in apply_decisions(_round_blocks(), changed)] == ["b2"]


def test_habits_pairs_only_differing_adopt_edited() -> None:
    decisions = _decisions() + [
        {"block": "b3", "action": "adopt_edited", "text": "AI 段三"},  # 同文本不算差分
    ]
    pairs = habits_pairs(_round_blocks(), decisions)
    assert pairs == [("AI 段二，综上所述收尾。", "人工段二，总的来说收尾。")]


# ── 会话聚合（§2.1 meta 形态）─────────────────────────────────────────


def test_session_meta_roundtrip_without_draft() -> None:
    s = CompositionSession(
        slug="demo",
        title="示例",
        sources=[SourceRef("/tmp/a.md", ["一、概述"])],
        rounds=[Round(n=1, blocks=[Block("b1", "para", "文本")])],
        status=STATUS_COMPOSING,
    )
    meta = s.to_meta()
    assert "draft" not in meta and meta["status"] == STATUS_COMPOSING  # draft.md 是真稿唯一表达
    got = CompositionSession.from_meta(meta)
    assert got.slug == "demo" and got.sources[0].sections == ["一、概述"]
    assert got.rounds[0].blocks[0].text == "文本" and got.draft == []


# ── 架构门：core/composition 纯逻辑零 I/O（§2.8 守门）────────────────


FORBIDDEN_MODULES = {
    "pathlib", "os", "io", "tempfile", "socket", "subprocess", "sqlite3",
    "urllib.request", "urllib.parse", "urllib", "requests",
}


def test_architecture_core_composition_zero_io() -> None:
    """core/composition 全部 .py：禁 I/O 库 import、禁 open() 调用（AST 级）。"""
    violations: list[str] = []
    files = sorted(PKG.glob("*.py"))
    assert files, "core/composition 包缺失"
    for f in files:
        if "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if alias.name in FORBIDDEN_MODULES or root in FORBIDDEN_MODULES:
                        violations.append(f"{f.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if node.module in FORBIDDEN_MODULES or root in FORBIDDEN_MODULES:
                    violations.append(f"{f.name}: from {node.module} import …")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "open":
                    violations.append(f"{f.name}: open() 调用")
    assert not violations, "core/composition I/O 违规:\n" + "\n".join(violations)


def test_kinds_constant_complete() -> None:
    assert KINDS == ("heading", "para", "code", "quote", "image")
    assert protocol_mod.SIMILARITY_THRESHOLD == 0.6
    assert session_mod.PER_SOURCE_BUDGET == 2000
    assert session_mod.SESSION_BUDGET == 8000
    assert session_mod.ROUND_CONTEXT_K == 2
