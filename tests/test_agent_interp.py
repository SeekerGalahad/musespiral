"""编排解释器（§五 F75-2~F75-6 + §七 引擎验收标准）。

纯函数单测：脚本化假 chat_fn 按调用序返回预设响应，零 I/O 零 mock HTTP。
每条用例对应 §五矩阵的一行，末尾 grp 标注用例号。
"""
from __future__ import annotations

import json

import pytest

from musespiral.core.agent import budgets, interp, plans_builtin
from musespiral.core.agent.plan import validate_plan

TASKS = ("agent_extract", "agent_synthesize", "tag_suggest")


# ── 脚本化假 chat_fn（按调用序，或按 (task, n) 逻辑）────────────────


def scripted(responses):
    """按调用序返回 responses[i]；每项可以是 dict / 异常 / 可调用。

    返回的 dict 形状与 gateway 契约一致（text/cache_hit/chars_in/chars_out）。
    """
    calls: list[tuple[str, int]] = []
    state = {"i": 0}

    def fn(prompt, *, task, cache):
        calls.append((task, len(prompt)))
        item = responses[state["i"]] if state["i"] < len(responses) else responses[-1]
        state["i"] += 1
        if callable(item):
            return item(prompt, task, cache)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return {"text": item, "cache_hit": False, "chars_in": len(prompt), "chars_out": len(item)}
        return item

    fn.calls = calls
    return fn


class BudgetExceededLike(RuntimeError):
    """测试用限额态（鸭子协议：budget_exceeded=True + 类名 BudgetExceeded）。

    刻意**不 import infra**（本文件测 L2 纯逻辑，限额态的跨层协议就是
    「属性/类名」——interp 不依赖具体类）。
    """

    budget_exceeded = True


# 类名恰为 BudgetExceeded（interp 的第二道鸭子判定）
BudgetExceededLike.__name__ = "BudgetExceeded"


class AIErrorLike(Exception):
    """测试用可重试失败（无 budget_exceeded 属性 → interp 判为普通 AIError）。"""


def _plan(steps, budget=None):
    p = {"plan": "t", "steps": steps}
    if budget is not None:
        p["budget"] = budget
    return p


def _step(step_id, out, *, task="agent_extract", inputs=None, max_out=200, **over):
    st = {
        "id": step_id,
        "task": task,
        "inputs": inputs if inputs is not None else {"x": "字面量"},
        "out": out,
        "max_out": max_out,
    }
    st.update(over)
    return st


CTX = {"materials": ["素材甲", "素材乙"], "params": {"k": "v"}}


# ── F75-2：确定性（同 plan + ctx + 脚本 → 逐字段相等）────────────────


def test_run_plan_is_deterministic():
    plan = plans_builtin.build_synthesis(["m0", "m1"])
    ctx = {"materials": ["正文" * 30, "正文" * 30], "params": {}}

    fn = scripted([
        {"text": "笔记A", "cache_hit": False, "chars_in": 10, "chars_out": 3},
        {"text": "笔记B", "cache_hit": False, "chars_in": 10, "chars_out": 3},
        {"text": "综合稿", "cache_hit": False, "chars_in": 10, "chars_out": 3},
    ])
    r1 = interp.run_plan(plan, ctx, fn, tasks=TASKS)

    fn2 = scripted([
        {"text": "笔记A", "cache_hit": False, "chars_in": 10, "chars_out": 3},
        {"text": "笔记B", "cache_hit": False, "chars_in": 10, "chars_out": 3},
        {"text": "综合稿", "cache_hit": False, "chars_in": 10, "chars_out": 3},
    ])
    r2 = interp.run_plan(plan, ctx, fn2, tasks=TASKS)

    assert json.dumps(r1, ensure_ascii=False, sort_keys=True) == json.dumps(
        r2, ensure_ascii=False, sort_keys=True
    )
    # steps_meta 逐字段（chars_in/out、cache_hit、attempts）
    for a, b in zip(r1["steps"], r2["steps"]):
        assert a == b


# ── F75-3：carry 尾部携带 ──────────────────────────────────────────


def test_carry_tail_exact_1200_chars_with_mark_prefix():
    """前步输出 3000 字符 + carry tail:1200 → 后步 prompt 含尾 1200 字符 + 前置标注。"""
    plan = _plan([
        _step("a", "big", max_out=5000),
        _step("b", "tail_out", inputs={"x": "指令"}, carry={"big": "tail:1200"}, max_out=5000),
    ])
    big = "".join(str(i % 10) for i in range(3000))
    fn = scripted([big, "done"])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "done"

    # 抓第二次调用的 prompt 文本（重跑一次并记录 prompt）
    prompts: list[str] = []

    def capture(prompt, *, task, cache):
        prompts.append(prompt)
        return {"text": big if len(prompts) == 1 else "done",
                "cache_hit": False, "chars_in": len(prompt), "chars_out": 4}

    interp.run_plan(plan, {}, capture, tasks=TASKS)
    second_prompt = prompts[1]
    assert budgets.TRUNCATED_MARK in second_prompt
    assert second_prompt.endswith(big[-1200:]), "尾部携带未取到末 1200 字符"
    # 标注在尾段之前（截断处前置）
    mark_pos = second_prompt.index(budgets.TRUNCATED_MARK)
    tail_pos = second_prompt.rindex(big[-1200:])
    assert mark_pos < tail_pos


def test_assemble_result_respects_budget_in():
    """assemble 后 prompt 经 clip ≤ budget_in。"""
    plan = _plan([
        _step("a", "big", max_out=5000),
        _step(
            "b", "out_b", inputs={"x": "指" * 5000},
            carry={"big": "tail:1200"}, budget_in=3000, max_out=500,
        ),
    ])
    fn = scripted(["X" * 3000, "ok"])
    prompts: list[str] = []

    def cap(prompt, *, task, cache):
        prompts.append(prompt)
        return {"text": "X" * 3000 if len(prompts) == 1 else "ok",
                "cache_hit": False, "chars_in": len(prompt), "chars_out": 4}

    interp.run_plan(plan, {}, cap, tasks=TASKS)
    assert len(prompts[1]) <= 3000, f"prompt 超 budget_in: {len(prompts[1])}"


def test_carry_short_output_is_passed_whole():
    """前步输出 < N 时不加标注（原样携带）。"""
    plan = _plan([
        _step("a", "small"),
        _step("b", "b_out", inputs={"x": "指令"}, carry={"small": "tail:1200"}),
    ])
    prompts: list[str] = []

    def cap(prompt, *, task, cache):
        prompts.append(prompt)
        return {"text": "短笔记" if len(prompts) == 1 else "ok",
                "cache_hit": False, "chars_in": len(prompt), "chars_out": 4}

    interp.run_plan(plan, {}, cap, tasks=TASKS)
    assert budgets.TRUNCATED_MARK not in prompts[1]
    assert "短笔记" in prompts[1]


# ── F75-4：重试与 BudgetExceeded 特判 ───────────────────────────────


def test_retries_then_success():
    """前 2 次 AIError、第 3 次成功（retries=2）→ done、attempts=3。"""
    plan = _plan([_step("a", "out", retries=2)])
    fn = scripted([AIErrorLike("net"), AIErrorLike("net2"), "成功了"])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "done"
    assert r["steps"][0]["attempts"] == 3
    assert r["steps"][0]["chars_out"] == len("成功了")


def test_retries_exhausted_failed_with_step_id_and_reason():
    """恒败 → failed，error 含步 id 与末次原因；已完成步存证保留。"""
    plan = _plan([
        _step("a", "first"),
        _step("b", "second", retries=1),
    ])
    fn = scripted(["ok", AIErrorLike("boom1"), AIErrorLike("boom2")])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "failed"
    assert r["steps"][0]["id"] == "a" and r["steps"][0]["status"] == "done"
    assert "步骤 b" in r["error"] and "boom2" in r["error"]
    assert len(r["steps"]) == 1, "失败步不应产 meta 项"


def test_budget_exceeded_not_retried():
    """限额态**不重试**（attempts 计数不增）→ 立即 failed，error 含预算文案。"""
    plan = _plan([_step("a", "out", retries=3)])
    fn = scripted([BudgetExceededLike("任务 x 今日已用 1/上限 1 次")])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "failed"
    assert "预算触顶" in r["error"]
    assert "今日已用" in r["error"] and "上限" in r["error"]
    assert len(fn.calls) == 1, "限额态必须只尝试一次（不重试）"


def test_budget_exceeded_preserves_completed_steps():
    """限额态前已完成步的存证必须保留（无部分采纳但要有审计面）。"""
    plan = _plan([
        _step("a", "first"),
        _step("b", "second", retries=2),
    ])
    fn = scripted(["ok", BudgetExceededLike("触顶")])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "failed"
    assert [s["id"] for s in r["steps"]] == ["a"]
    assert r["slots"]["first"] == "ok"


def test_empty_text_response_treated_as_retryable_failure():
    """空响应 → 按可重试失败（与 chat() 的「AI 响应为空」同语义）。"""
    plan = _plan([_step("a", "out", retries=1)])
    fn = scripted([
        {"text": "", "cache_hit": False, "chars_in": 5, "chars_out": 0},
        {"text": "补上了", "cache_hit": False, "chars_in": 5, "chars_out": 3},
    ])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "done"
    assert r["steps"][0]["attempts"] == 2


# ── F75-5：budget_stopped 存证 ───────────────────────────────────────


def test_budget_stopped_second_step_with_full_first_step_evidence():
    """plan.budget.out_chars=2500、两步各 max_out=2000 → 第二步前触停。

    status=budget_stopped（**非错误**）、steps[0] 完整存证、error=None。
    """
    plan = _plan(
        [_step("a", "first", max_out=2000), _step("b", "second", max_out=2000)],
        budget={"steps": 8, "in_chars": 40_000, "out_chars": 2500},
    )
    fn = scripted([{"text": "A" * 2000, "cache_hit": False, "chars_in": 100, "chars_out": 2000}])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "budget_stopped"
    assert r["error"] is None, "budget_stopped 是守门非错误"
    assert len(r["steps"]) == 1 and r["steps"][0]["id"] == "a"
    assert r["steps"][0]["chars_out"] == 2000
    assert len(fn.calls) == 1, "触停后不得再调 chat_fn"


def test_budget_stopped_on_steps_key():
    """steps 键触顶（预算 2 步、计划 3 步）→ 第 3 步前停。"""
    plan = _plan([
        _step("a", "s1"), _step("b", "s2"), _step("c", "s3"),
    ], budget={"steps": 2, "in_chars": 40_000, "out_chars": 20_000})
    fn = scripted(["1", "2", "3"])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "budget_stopped"
    assert [s["id"] for s in r["steps"]] == ["a", "b"]
    assert len(fn.calls) == 2


def test_budget_stopped_on_in_chars_key():
    """in_chars 触顶 → 步前停（已用实数 + 本步输入上界 > run 上限）。

    对账用**已用的实际 in_chars**（interp ⑧ `used += res["chars_in"]`）＋
    本步 `budget_in` 上界：第一步实际花 2400 → 2400+3000 > 5000 即第 2 步前停。
    """
    plan = _plan([
        _step("a", "s1", budget_in=3000),
        _step("b", "s2", budget_in=3000),
    ], budget={"steps": 8, "in_chars": 5000, "out_chars": 20_000})
    fn = scripted([
        {"text": "1", "cache_hit": False, "chars_in": 2400, "chars_out": 1},
        {"text": "2", "cache_hit": False, "chars_in": 2400, "chars_out": 1},
    ])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "budget_stopped"
    assert len(fn.calls) == 1


def test_budget_stopped_before_first_step_yields_zero_evidence():
    """首步即不可负担 → budget_stopped 且 steps 为空（不是 failed）。"""
    plan = _plan([_step("a", "out", budget_in=9000)],
                 budget={"steps": 8, "in_chars": 100, "out_chars": 20_000})
    fn = scripted(["x"])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["status"] == "budget_stopped" and r["steps"] == [] and fn.calls == []


# ── F75-6：缓存命中免计 run 预算 ────────────────────────────────────


def test_cache_hit_does_not_consume_run_budget():
    """chat_fn 报 cache_hit=true → used.in_chars/out_chars 不增、meta.cache_hit=true。"""
    plan = _plan([
        _step("a", "s1", cache=True),
        _step("b", "s2", cache=True),
    ])
    fn = scripted([
        {"text": "A" * 100, "cache_hit": True, "chars_in": 500, "chars_out": 100},
        {"text": "B" * 100, "cache_hit": False, "chars_in": 500, "chars_out": 100},
    ])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    # 第一步命中零成本；第二步出账 = 500 in + 100 out
    assert r["budget"]["in_chars"] == 500
    assert r["budget"]["out_chars"] == 100
    assert r["steps"][0]["cache_hit"] is True
    assert r["steps"][1]["cache_hit"] is False


def test_all_cache_hits_zero_run_cost():
    plan = _plan([_step("a", "s1", cache=True), _step("b", "s2", cache=True)])
    fn = scripted([{"text": "x", "cache_hit": True, "chars_in": 99, "chars_out": 1},
                   {"text": "y", "cache_hit": True, "chars_in": 99, "chars_out": 1}])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["budget"] == {"steps": 2, "in_chars": 0, "out_chars": 0}


# ── 输出截断（⑦ max_out）──────────────────────────────────────────


def test_output_clipped_to_max_out_with_mark_inside_bound():
    plan = _plan([_step("a", "out", max_out=100)])
    fn = scripted(["Z" * 500])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    text = r["slots"]["out"]
    assert len(text) <= 100, f"输出超 max_out: {len(text)}"
    assert text.endswith(budgets.TRUNCATED_MARK)


def test_output_within_max_out_untouched():
    plan = _plan([_step("a", "out", max_out=100)])
    fn = scripted(["Z" * 50])
    r = interp.run_plan(plan, {}, fn, tasks=TASKS)
    assert r["slots"]["out"] == "Z" * 50
    assert budgets.TRUNCATED_MARK not in r["slots"]["out"]


# ── 槽位装配（materials/params/steps）运行期求值 ────────────────────


def test_materials_and_params_render():
    plan = _plan([_step("a", "out", inputs={"m": "{{materials[1]}}", "p": "{{params.k}}"})])
    prompts: list[str] = []

    def cap(prompt, *, task, cache):
        prompts.append(prompt)
        return {"text": "ok", "cache_hit": False, "chars_in": len(prompt), "chars_out": 2}

    r = interp.run_plan(plan, CTX, cap, tasks=TASKS)
    assert r["status"] == "done"
    assert "素材乙" in prompts[0] and "v" in prompts[0]


def test_materials_index_out_of_range_is_readable_error():
    plan = _plan([_step("a", "out", inputs={"m": "{{materials[9]}}"})])
    with pytest.raises(ValueError, match="槽位引用越界"):
        interp.run_plan(plan, CTX, scripted(["x"]), tasks=TASKS)


def test_missing_param_key_is_readable_error():
    plan = _plan([_step("a", "out", inputs={"p": "{{params.absent}}"})])
    with pytest.raises(ValueError, match="槽位引用缺失"):
        interp.run_plan(plan, {"params": {}}, scripted(["x"]), tasks=TASKS)


def test_steps_reference_renders_previous_output():
    plan = _plan([
        _step("a", "first"),
        _step("b", "second", inputs={"x": "{{steps.first}}"}, max_out=500),
    ])
    prompts: list[str] = []

    def cap(prompt, *, task, cache):
        prompts.append(prompt)
        return {"text": "甲" if len(prompts) == 1 else "ok",
                "cache_hit": False, "chars_in": len(prompt), "chars_out": 2}

    r = interp.run_plan(plan, {}, cap, tasks=TASKS)
    assert "甲" in prompts[1]
    assert r["slots"]["second"] == "ok"


# ── 非法 plan 在解释前就被拒（validate 前置）──────────────────────


def test_run_plan_validates_before_calling_chat():
    bad = _plan([_step("a", "out", task="compose")])
    fn = scripted(["should-not-run"])
    with pytest.raises(ValueError, match="不在 TASKS 白名单"):
        interp.run_plan(bad, {}, fn, tasks=TASKS)
    assert fn.calls == [], "非法 plan 不得触达 chat_fn"


# ── RunResult 形状（agent_service 落盘契约）───────────────────────


def test_run_result_shape():
    plan = _plan([_step("a", "out")])
    r = interp.run_plan(plan, {}, scripted(["ok"]), tasks=TASKS)
    assert set(r) == {"status", "steps", "slots", "budget", "error"}
    assert r["status"] in interp.STATUSES
    assert set(r["budget"]) == {"steps", "in_chars", "out_chars"}
    assert set(r["steps"][0]) == {
        "id", "task", "status", "chars_in", "chars_out", "cache_hit", "attempts",
    }
    assert r["error"] is None


def test_result_slots_are_not_shared_with_internal_state():
    """返回的 slots 是浅拷贝——调用方改动不回渗解释器内部（下一 run 不受影响）。"""
    plan = _plan([_step("a", "out")])
    r = interp.run_plan(plan, {}, scripted(["ok"]), tasks=TASKS)
    r["slots"]["out"] = "被调用方改了"
    r2 = interp.run_plan(plan, {}, scripted(["ok2"]), tasks=TASKS)
    assert r2["slots"]["out"] == "ok2"


def test_plan_input_not_mutated():
    """interp 不改写入参 plan（validate 前置 + 只读折叠）。"""
    plan = plans_builtin.build_synthesis(["m0", "m1"])
    snapshot = json.dumps(plan, ensure_ascii=False, sort_keys=True)
    interp.run_plan(plan, {"materials": ["甲", "乙"], "params": {}},
                    scripted(["a", "b", "c"]), tasks=TASKS)
    assert json.dumps(plan, ensure_ascii=False, sort_keys=True) == snapshot
