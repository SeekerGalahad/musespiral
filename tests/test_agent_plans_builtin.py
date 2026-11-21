"""内置计划构造器（§五 F76-1 展平形态与边界 / F77 批量形态）。

纯函数单测：构造器只产数据，零 I/O。断言聚焦**形态**（步名/任务/缓存位/
预算位/槽位），不重复 validate_plan 的枚举面（那在 test_agent_plan）。
"""
from __future__ import annotations

import pytest

from musespiral.core.agent import budgets, plans_builtin
from musespiral.core.agent.plan import validate_plan

TASKS = ("agent_extract", "agent_synthesize", "tag_suggest")


# ── F76-1：展平计划形态与边界 ───────────────────────────────────────


def test_build_synthesis_flattens_extract_steps_and_one_synthesize():
    p = plans_builtin.build_synthesis(["m1", "m2", "m3"])
    assert p["plan"] == plans_builtin.PLAN_SYNTHESIS
    extract = [s for s in p["steps"] if s["task"] == "agent_extract"]
    synth = [s for s in p["steps"] if s["task"] == "agent_synthesize"]
    assert len(extract) == 3 and len(synth) == 1
    assert [s["id"] for s in extract] == ["extract_1", "extract_2", "extract_3"]
    assert p["steps"][-1]["id"] == "synthesize"


def test_extract_steps_have_distinct_material_refs_and_cache_on():
    p = plans_builtin.build_synthesis(["a", "b", "c"])
    refs = [s["inputs"]["material"] for s in p["steps"][:3]]
    assert refs == ["{{materials[0]}}", "{{materials[1]}}", "{{materials[2]}}"]
    assert len({r for r in refs}) == 3, "各步 materials 下标须各异（否则同素材重复提取）"
    for s in p["steps"][:3]:
        assert s["cache"] is True
        assert s["budget_in"] <= plans_builtin.EXTRACT_BUDGET_IN
        assert s["max_out"] == plans_builtin.EXTRACT_MAX_OUT


def test_synthesize_step_carries_all_notes_with_tail_spec():
    p = plans_builtin.build_synthesis(["a", "b", "c"])
    synth = p["steps"][-1]
    assert synth["task"] == "agent_synthesize"
    assert synth["cache"] is False, "生成类恒不缓存（§八-3）"
    assert set(synth["carry"]) == {"notes_1", "notes_2", "notes_3"}
    assert all(spec == "tail:1200" for spec in synth["carry"].values())
    assert synth["max_out"] == plans_builtin.SYNTHESIS_MAX_OUT


def test_synthesize_budget_in_covers_all_carry():
    """synthesize 的 budget_in 必须 ≥ 指令 + N 条 carry 的最大占用。"""
    for n in (2, 5, 7):
        p = plans_builtin.build_synthesis([f"m{i}" for i in range(n)])
        synth = p["steps"][-1]
        carry_max = n * (1 + len(budgets.TRUNCATED_MARK) + budgets.CARRY_TAIL_DEFAULT)
        assert synth["budget_in"] >= len(plans_builtin.SYNTHESIZE_INSTRUCTION) + carry_max


def test_synthesis_lower_and_upper_bound_material_counts():
    """2 篇下界与 8 篇「构造上界」各通过构造（8 篇的校验拒绝在 plan 层另测）。"""
    assert len(plans_builtin.build_synthesis(["m1", "m2"])["steps"]) == 3
    assert len(plans_builtin.build_synthesis([f"m{i}" for i in range(8)])["steps"]) == 9


def test_synthesis_one_material_rejected():
    with pytest.raises(ValueError, match="需要 2-8 篇素材，实际 1 篇"):
        plans_builtin.build_synthesis(["only"])


def test_synthesis_nine_materials_rejected():
    with pytest.raises(ValueError, match="需要 2-8 篇素材，实际 9 篇"):
        plans_builtin.build_synthesis([f"m{i}" for i in range(9)])


def test_synthesis_empty_and_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="至少 1 个素材 id"):
        plans_builtin.build_synthesis([])
    with pytest.raises(ValueError, match="含重复 id"):
        plans_builtin.build_synthesis(["m1", "m1"])


def test_synthesis_non_str_id_rejected():
    with pytest.raises(ValueError, match="必须是非空字符串"):
        plans_builtin.build_synthesis(["m1", 42])


def test_synthesis_budget_steps_equals_step_count():
    """构造器声明的 budget.steps == 实际步数（不虚报额度）。"""
    for n in range(2, 8):
        p = plans_builtin.build_synthesis([f"m{i}" for i in range(n)])
        assert p["budget"]["steps"] == len(p["steps"]) == n + 1


def test_synthesis_eight_materials_now_validates_under_raised_step_cap():
    """设计文档内部张力（F76.1 2-8 篇 vs F75.2 ≤8 步）的历史落点重锚：

    R3.5 书籍提炼批（2026-11-18）把 `budgets.RUN_STEPS` 8→16（build_book_digest
    需要 N+1 步单计划展平）——「8 篇构造出 9 步被 validate 拒」不再是事实，
    8 篇综合计划现可执行（in/out 上界由构造器自报 run 预算承载）。本测试
    锚定新语义；真正的步数上限拒在 17 步（16+1）另测。
    """
    p = plans_builtin.build_synthesis([f"m{i}" for i in range(8)])
    validate_plan(p, tasks=TASKS)  # 9 步 ≤ 16，可执行
    assert len(p["steps"]) == 9
    # 步数上限仍在：16 步计划可过、17 步拒（validate 层枚举面）
    p16 = plans_builtin.build_tag_batch([f"m{i}" for i in range(16)])
    validate_plan(p16, tasks=TASKS)
    with pytest.raises(ValueError, match="步数上限 16，实际 17 步"):
        validate_plan(
            plans_builtin.build_tag_batch([f"m{i}" for i in range(17)]), tasks=TASKS
        )


# ── F77：批量标签计划 ──────────────────────────────────────────────


def test_build_tag_batch_one_step_per_material():
    p = plans_builtin.build_tag_batch(["m1", "m2", "m3"])
    assert p["plan"] == plans_builtin.PLAN_TAG_BATCH
    assert [s["id"] for s in p["steps"]] == ["tag_1", "tag_2", "tag_3"]
    assert [s["out"] for s in p["steps"]] == ["tags_1", "tags_2", "tags_3"]
    for s in p["steps"]:
        assert s["task"] == "tag_suggest"
        assert s["cache"] is True, "tag_suggest 恒缓存（§八-3）"
        assert s["max_out"] == plans_builtin.TAG_MAX_OUT
        assert s["budget_in"] == plans_builtin.TAG_BUDGET_IN


def test_tag_batch_out_is_raw_ai_text_parsed_downstream():
    """步 out = AI 原文槽名；计划**不含解析动作**（解析归消费侧 tag_service）。

    `TAG: ` 出现在**提示词**里是对的（那是模型须遵守的输出协议）；不出现
    的应该是「解析/切分」这类动作面——DSL 只有 input 装配与输出截断，
    没有 post-processing 键（不图灵完备的直接体现）。
    """
    p = plans_builtin.build_tag_batch(["m1"])
    step = p["steps"][0]
    assert step["out"] == "tags_1"  # 槽名，不是什么解析结果的容器
    assert "parse" not in step and "postprocess" not in step
    # 计划的全部键都是 schema 内的（无扩展面）
    assert set(step) <= {"id", "task", "inputs", "carry", "out", "budget_in",
                         "max_out", "cache", "retries"}


def test_tag_batch_requires_at_least_one_id():
    with pytest.raises(ValueError, match="至少 1 个素材 id"):
        plans_builtin.build_tag_batch([])


def test_tag_batch_over_step_limit_is_rejected_at_plan_layer():
    """超上限批次的拒绝归 validate_plan（构造器不静默切片——静默截断会让
    「提交 20 个」与「只跑 16 个」不可区分）。上限 8→16：R3.5 批随
    RUN_STEPS 上调（见 budgets 注），CLI 分批大小同源放大。"""
    p = plans_builtin.build_tag_batch([f"m{i}" for i in range(17)])
    with pytest.raises(ValueError, match="步数上限 16，实际 17 步"):
        validate_plan(p, tasks=TASKS)


def test_tag_batch_budget_matches_step_count():
    p = plans_builtin.build_tag_batch([f"m{i}" for i in range(5)])
    assert p["budget"]["steps"] == 5
    assert p["budget"]["in_chars"] == 5 * plans_builtin.TAG_BUDGET_IN
    assert p["budget"]["out_chars"] == 5 * plans_builtin.TAG_MAX_OUT


# ── 内置清单（`musespiral agent plans` 数据面）──────────────────────────


def test_list_builtin_plans_lists_two():
    rows = plans_builtin.list_builtin_plans()
    assert [r["plan"] for r in rows] == list(plans_builtin.BUILTIN_PLANS)
    for row in rows:
        assert set(row) == {"plan", "purpose", "steps", "budget"}


def test_builtin_plan_names_stable():
    assert plans_builtin.PLAN_SYNTHESIS == "synthesis_v1"
    assert plans_builtin.PLAN_TAG_BATCH == "tag_batch"


def test_prompts_are_dialect_free_and_nonempty():
    """三套模板非空且无模型/供应商方言（由架构门 D4 全量扫描兜底，此处快断言）。"""
    for name in ("EXTRACT_INSTRUCTION", "SYNTHESIZE_INSTRUCTION", "TAG_INSTRUCTION"):
        text = getattr(plans_builtin, name)
        assert text.strip(), f"{name} 为空"
    from musespiral.config import DIALECT_FORBIDDEN

    joined = (
        plans_builtin.EXTRACT_INSTRUCTION
        + plans_builtin.SYNTHESIZE_INSTRUCTION
        + plans_builtin.TAG_INSTRUCTION
    ).lower()
    assert not any(tok in joined for tok in DIALECT_FORBIDDEN)


def test_synthesis_prompt_mandates_four_section_structure():
    """F76.2 固定四节（prompt 约定，非机器门——但模板必须写出来）。"""
    text = plans_builtin.SYNTHESIZE_INSTRUCTION
    assert "## 共同点" in text
    assert "## 分歧" in text
    assert "## 时间线" in text
    assert "## 选题角度" in text


def test_tag_prompt_declares_tag_prefix_protocol():
    assert "TAG: " in plans_builtin.TAG_INSTRUCTION
