"""编排计划校验（§五 F75-1 全枚举拒 + F79 治理矩阵落点）。

纯函数单测：零 I/O、零 mock HTTP。`validate_plan` 的**每一种**违规都要有
一条用例——它的价值就在「没有一种坏计划能通过」；漏掉一类，那类坏计划
就会在运行期变成静默错误或半途 failed。
"""
from __future__ import annotations

import pytest

from musespiral.core.agent import budgets, plans_builtin
from musespiral.core.agent.plan import MAX_STEPS, slot_kind, validate_plan

# 测试用固定白名单（不 import profiles——本文件只测 L2 逻辑；
# 生产由 services 层注入 profiles.TASKS）
TASKS = ("agent_extract", "agent_synthesize", "tag_suggest")


def _step(**over):
    base = {
        "id": "s1",
        "task": "agent_extract",
        "inputs": {"instruction": "写笔记", "material": "{{materials[0]}}"},
        "out": "notes_1",
        "max_out": 200,
    }
    base.update(over)
    return base


def _plan(steps, budget=None):
    p = {"plan": "test", "steps": steps}
    if budget is not None:
        p["budget"] = budget
    return p


# ── F75-1：全枚举拒（每条一个断言，消息含违规项名）───────────────────


def test_plan_must_be_dict():
    with pytest.raises(ValueError, match="plan 必须是 dict"):
        validate_plan(["not-a-dict"], tasks=TASKS)


def test_steps_must_be_nonempty_list():
    for bad in ({}, [], None, "steps"):
        with pytest.raises(ValueError, match="steps"):
            validate_plan(_plan(bad), tasks=TASKS)


def test_step_must_be_dict():
    with pytest.raises(ValueError, match=r"steps\[0\] 必须是 dict"):
        validate_plan(_plan(["nope"]), tasks=TASKS)


def test_step_id_required_nonempty_str():
    for bad in (None, "", "   ", 42):
        with pytest.raises(ValueError, match="id 必须是非空字符串"):
            validate_plan(_plan([_step(id=bad)]), tasks=TASKS)


def test_step_id_duplicate_rejected_naming_the_id():
    with pytest.raises(ValueError, match="步骤 id 重复: 'dup'"):
        validate_plan(_plan([_step(id="dup"), _step(id="dup", out="notes_2")]), tasks=TASKS)


def test_more_than_max_steps_rejected():
    steps = [_step(id=f"s{i}", out=f"notes_{i}") for i in range(MAX_STEPS + 1)]
    with pytest.raises(ValueError, match=f"步数上限 {MAX_STEPS}，实际 {MAX_STEPS + 1} 步"):
        validate_plan(_plan(steps), tasks=TASKS)


def test_exactly_max_steps_allowed():
    steps = [_step(id=f"s{i}", out=f"notes_{i}") for i in range(MAX_STEPS)]
    validate_plan(_plan(steps), tasks=TASKS)  # 不抛即通过


def test_task_not_in_whitelist_rejected_naming_the_task():
    with pytest.raises(ValueError, match="任务 'compose' 不在 TASKS 白名单"):
        validate_plan(_plan([_step(task="compose")]), tasks=TASKS)


def test_task_missing_or_non_str_rejected():
    for bad in (None, "", 7):
        with pytest.raises(ValueError, match="task 必须是非空字符串"):
            validate_plan(_plan([_step(task=bad)]), tasks=TASKS)


def test_out_required_and_globally_unique():
    with pytest.raises(ValueError, match="out 必须是非空字符串"):
        validate_plan(_plan([_step(out=None)]), tasks=TASKS)
    with pytest.raises(ValueError, match="out 槽名重复: 'dup_slot'"):
        validate_plan(
            _plan([
                _step(id="a", out="dup_slot"),
                _step(id="b", out="dup_slot"),
            ]),
            tasks=TASKS,
        )


def test_max_out_required_positive_int():
    p = _plan([_step()])
    del p["steps"][0]["max_out"]
    with pytest.raises(ValueError, match="缺少必填键 max_out"):
        validate_plan(p, tasks=TASKS)
    for bad in (0, -1, "200", True, 1.5):
        with pytest.raises(ValueError, match="max_out 必须是 ≥1 的整数"):
            validate_plan(_plan([_step(max_out=bad)]), tasks=TASKS)


def test_budget_in_positive_int_when_given():
    for bad in (0, -5, "x", True):
        with pytest.raises(ValueError, match="budget_in 必须是 ≥1 的整数"):
            validate_plan(_plan([_step(budget_in=bad)]), tasks=TASKS)


def test_retries_non_negative_int_when_given():
    for bad in (-1, "2", True, 1.5):
        with pytest.raises(ValueError, match="retries 必须是 ≥0 的整数"):
            validate_plan(_plan([_step(retries=bad)]), tasks=TASKS)
    validate_plan(_plan([_step(retries=0)]), tasks=TASKS)


def test_cache_must_be_bool_when_given():
    for bad in ("true", 1, None):
        with pytest.raises(ValueError, match="cache 必须是布尔值"):
            validate_plan(_plan([_step(cache=bad)]), tasks=TASKS)


def test_inputs_must_be_dict():
    with pytest.raises(ValueError, match="inputs 必须是 dict"):
        validate_plan(_plan([_step(inputs=["x"])]), tasks=TASKS)


def test_unknown_step_key_rejected():
    with pytest.raises(ValueError, match=r"steps\[0\] 含未知键 'retry'"):
        validate_plan(_plan([_step(retry=3)]), tasks=TASKS)


def test_unknown_plan_key_rejected():
    with pytest.raises(ValueError, match="plan 含未知键 'version'"):
        validate_plan({"plan": "t", "steps": [_step()], "version": 2}, tasks=TASKS)


def test_budget_negative_rejected_naming_the_key():
    with pytest.raises(ValueError, match="in_chars 必须是 ≥0 的整数"):
        validate_plan(_plan([_step()], budget={"steps": 2, "in_chars": -1, "out_chars": 10}), tasks=TASKS)


def test_budget_unknown_key_rejected():
    with pytest.raises(ValueError, match="plan\\['budget'\\] 含未知键 'steps_max'"):
        validate_plan(_plan([_step()], budget={"steps_max": 3}), tasks=TASKS)


def test_budget_must_be_dict():
    with pytest.raises(ValueError, match="plan\\['budget'\\] 必须是 dict"):
        validate_plan(_plan([_step()], budget=[3]), tasks=TASKS)


# ── 槽位引用文法：悬空 / 指向后方 / 未知文法 ────────────────────────


def test_steps_reference_to_later_step_rejected():
    """`{{steps.later}}` 指向后方步 → 拒（线性无环的等价校验）。"""
    with pytest.raises(ValueError, match=r"引用 {{steps\.later}} 的槽位不存在或指向后方步"):
        validate_plan(
            _plan([
                _step(id="a", out="first", inputs={"x": "{{steps.later}}"}),
                _step(id="b", out="later", inputs={"x": "字面量"}),
            ]),
            tasks=TASKS,
        )


def test_steps_reference_to_missing_slot_rejected():
    with pytest.raises(ValueError, match="槽位不存在或指向后方步"):
        validate_plan(_plan([_step(inputs={"x": "{{steps.ghost}}"})]), tasks=TASKS)


def test_steps_reference_forward_is_allowed():
    validate_plan(
        _plan([
            _step(id="a", out="first", inputs={"x": "字面量"}),
            _step(id="b", out="second", inputs={"x": "{{steps.first}}"}),
        ]),
        tasks=TASKS,
    )


def test_unknown_brace_grammar_rejected_naming_value():
    """未知 {{…}} 文法（`{{params}}` / `{{foo}}` / 嵌套）→ 拒，含原文。"""
    for bad in ("{{params}}", "{{foo}}", "{{materials}}", "{{steps}}", "{{outer {{inner}} }}"):
        with pytest.raises(ValueError, match="含未知槽位引用文法"):
            validate_plan(_plan([_step(inputs={"x": bad})]), tasks=TASKS)


def test_materials_index_non_decimal_rejected():
    with pytest.raises(ValueError, match="含未知槽位引用文法"):
        validate_plan(_plan([_step(inputs={"x": "{{materials[x]}}"})]), tasks=TASKS)


def test_params_reference_accepted():
    validate_plan(_plan([_step(inputs={"x": "{{params.k}}"})]), tasks=TASKS)


def test_plain_literal_inputs_are_not_interpreted():
    validate_plan(_plan([_step(inputs={"x": "普通字面量，无花括号"})]), tasks=TASKS)


def test_non_str_input_value_is_literal_not_reference():
    validate_plan(_plan([_step(inputs={"n": 42, "flag": True})]), tasks=TASKS)


# ── carry 规格 ──────────────────────────────────────────────────────


def test_carry_to_unproduced_slot_rejected():
    with pytest.raises(ValueError, match="carry 引用的槽位 'ghost' 尚未产出"):
        validate_plan(_plan([_step(carry={"ghost": "tail:1200"})]), tasks=TASKS)


def test_carry_to_later_step_rejected():
    """carry 指向后方步（尚未产出）→ 拒。"""
    with pytest.raises(ValueError, match="carry 引用的槽位 'later' 尚未产出"):
        validate_plan(
            _plan([
                _step(id="a", out="first", carry={"later": "tail:1200"}),
                _step(id="b", out="later"),
            ]),
            tasks=TASKS,
        )


def test_carry_spec_must_be_tail_positive_int():
    for bad in ("last:1200", "tail:", "tail:0", "tail:-1", "tail:abc", 1200, None):
        with pytest.raises(ValueError, match="carry"):
            validate_plan(
                _plan([
                    _step(id="a", out="first"),
                    _step(id="b", out="second", carry={"first": bad}),
                ]),
                tasks=TASKS,
            )


def test_carry_must_be_dict():
    with pytest.raises(ValueError, match="carry 必须是 dict"):
        validate_plan(_plan([_step(carry="tail:1200")]), tasks=TASKS)


def test_valid_carry_accepted():
    validate_plan(
        _plan([
            _step(id="a", out="first"),
            _step(id="b", out="second", carry={"first": "tail:1200"}),
        ]),
        tasks=TASKS,
    )


# ── slot_kind 文法归类（纯函数小件）────────────────────────────────


def test_slot_kind_classifies_three_grammars():
    assert slot_kind("{{materials[0]}}") == ("materials", 0)
    assert slot_kind("{{materials[12]}}") == ("materials", 12)
    assert slot_kind("{{params.k}}") == ("params", "k")
    assert slot_kind("{{steps.notes_1}}") == ("steps", "notes_1")


def test_slot_kind_returns_none_for_non_references():
    for bad in ("literal", "{{foo}}", "{{materials[x]}}", "{{params.9bad}}", "{{steps.}}"):
        assert slot_kind(bad) is None


# ── 构造器产出的计划通过校验（F76-1 / F77 接缝）─────────────────────


def test_builtin_plans_validate_clean_for_runnable_sizes():
    """可执行区间（综合 2-8 篇 / 标签 1-16 篇）内，构造器产出直接过校验。

    综合上界取「构造器边界（F76.1 2-8 篇）」与「步数上限对应可跑上界」的
    较小者——R3.5 批（2026-11-18）RUN_STEPS 8→16 后步数不再收紧综合计划，
    8 篇（9 步）可执行；构造器边界仍是 8 篇（F76.1 设计值未动）。
    """
    synth_hi = min(
        plans_builtin.SYNTHESIS_MAX_MATERIALS,
        plans_builtin.SYNTHESIS_MAX_RUNNABLE_MATERIALS,
    )
    for n in range(plans_builtin.SYNTHESIS_MIN_MATERIALS, synth_hi + 1):
        p = plans_builtin.build_synthesis([f"m{i}" for i in range(n)])
        validate_plan(p, tasks=TASKS)
        assert len(p["steps"]) == n + 1
    for n in range(1, MAX_STEPS + 1):
        p = plans_builtin.build_tag_batch([f"m{i}" for i in range(n)])
        validate_plan(p, tasks=TASKS)
        assert len(p["steps"]) == n


def test_synthesis_plan_declares_budget_covering_its_own_step_bounds():
    """计划的 run 预算必须 ≥ 各步上界之和（自相一致的构造器契约）。"""
    synth_hi = min(
        plans_builtin.SYNTHESIS_MAX_MATERIALS,
        plans_builtin.SYNTHESIS_MAX_RUNNABLE_MATERIALS,
    )
    for n in range(plans_builtin.SYNTHESIS_MIN_MATERIALS, synth_hi + 1):
        p = plans_builtin.build_synthesis([f"m{i}" for i in range(n)])
        sum_in = sum(s.get("budget_in", budgets.STEP_BUDGET) for s in p["steps"])
        sum_out = sum(s["max_out"] for s in p["steps"])
        assert p["budget"]["in_chars"] >= sum_in, f"n={n} in 预算不足"
        assert p["budget"]["out_chars"] >= sum_out, f"n={n} out 预算不足"


def test_max_steps_constant_single_source_with_budgets():
    """plan.MAX_STEPS 与 budgets.RUN_STEPS 同值（单一事实源，不许两处漂移）。"""
    assert MAX_STEPS == budgets.RUN_STEPS
