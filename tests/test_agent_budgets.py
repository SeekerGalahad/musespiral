"""字符预算口径与三级预算常量（§五 F79-4 的口径面 + F75-3 的截断面）。

纯函数单测：`est_tokens`/`clip`/`tail`/`over_any`/`remaining` 全无 I/O。
"""
from __future__ import annotations

import pytest

from musespiral.core.agent import budgets


# ── 常量单一事实源（设计 §三-D2 / §八-4 裁决值）──────────────────────


def test_budget_constants_match_design_ruling():
    """§八-4 缺省：daily-related 不在本模块；run 级 40k-in/20k-out。

    步数 8→16：R3.5 书籍提炼批（2026-11-18）上调——`build_book_digest` 需要
    「N 章 digest + 1 综述」单计划展平（设计正典 9 章书示例=10 步、R4 前端
    「最新 10 章」预设=11 步）。锚定值随之上调（见 budgets.RUN_STEPS 注）。
    """
    assert budgets.RUN_IN_CHARS == 40_000
    assert budgets.RUN_OUT_CHARS == 20_000
    assert budgets.RUN_STEPS == 16
    assert budgets.SLOT_BUDGET == 2000
    assert budgets.STEP_BUDGET == 8000
    assert budgets.CARRY_TAIL_DEFAULT == 1200


def test_budget_keys_three():
    assert budgets.BUDGET_KEYS == ("steps", "in_chars", "out_chars")


def test_unit_is_chars_honest_approximation():
    """口径诚实标注（§二 F79.4：不做 tokenizer，meta 恒带 unit:chars）。"""
    assert budgets.UNIT == "chars"
    m = budgets.meta(chars_in=100, chars_out=50)
    assert m == {"unit": "chars", "chars_in": 100, "chars_out": 50}


def test_run_budget_returns_fresh_dict():
    a = budgets.run_budget()
    b = budgets.run_budget()
    assert a == {"steps": 16, "in_chars": 40_000, "out_chars": 20_000}
    a["steps"] = 1
    assert b["steps"] == 16, "run_budget 必须每次新 dict（防污染常量）"


# ── est_tokens 启发式（诚实近似，非精确）─────────────────────────────


def test_est_tokens_cjk_counts_one_per_char():
    assert budgets.est_tokens("中文四字") == 4


def test_est_tokens_ascii_halved_up():
    assert budgets.est_tokens("abcd") == 2
    assert budgets.est_tokens("abc") == 2  # 向上取整
    assert budgets.est_tokens("") == 0


def test_est_tokens_mixed():
    # 2 CJK + 4 ASCII → 2 + 2 = 4
    assert budgets.est_tokens("中文abcd") == 4


# ── clip：结果长度恒 ≤ limit，标注在限内 ────────────────────────────


def test_clip_short_text_untouched():
    assert budgets.clip("短", 100) == "短"


def test_clip_exact_length_untouched():
    text = "x" * 100
    assert budgets.clip(text, 100) == text


def test_clip_truncates_and_result_within_limit():
    """核心契约：截断结果长度**恒 ≤ limit**（标注占限内，不外溢）。"""
    text = "y" * 500
    out = budgets.clip(text, 100)
    assert len(out) <= 100
    assert out.endswith(budgets.TRUNCATED_MARK)
    assert out.startswith("y")


def test_clip_limit_smaller_than_mark_yields_pure_content():
    """limit < 标注长度时，标注让位于内容（仍守 ≤limit）。"""
    out = budgets.clip("z" * 100, 3)
    assert len(out) == 3 and out == "zzz"


def test_clip_zero_or_negative_limit_empty():
    assert budgets.clip("abc", 0) == ""
    assert budgets.clip("abc", -5) == ""


# ── tail：取尾 N，标注前置 ──────────────────────────────────────────


def test_tail_shorter_text_passed_whole_no_mark():
    assert budgets.tail("短文本", "tail:1200") == "短文本"


def test_tail_exact_length_no_mark():
    text = "x" * 1200
    assert budgets.tail(text, "tail:1200") == text


def test_tail_takes_last_n_with_mark_prefix():
    text = "".join(str(i % 10) for i in range(3000))
    out = budgets.tail(text, "tail:1200")
    assert out == budgets.TRUNCATED_MARK + text[-1200:]
    assert text[-1200:] in out


def test_tail_spec_validation():
    for bad in ("tail:0", "tail:-1", "tail:abc", "last:1200", "", 1200, None):
        with pytest.raises(ValueError):
            budgets.tail("x", bad)


def test_parse_tail_spec_positive_int():
    assert budgets.parse_tail_spec("tail:1200") == 1200
    assert budgets.parse_tail_spec("tail: 42 ") == 42


# ── over_any / remaining：run 级对账数学 ────────────────────────────


def test_over_any_reports_first_exceeded_key():
    budget = {"steps": 2, "in_chars": 100, "out_chars": 100}
    assert budgets.over_any(budget, {"steps": 2, "in_chars": 0, "out_chars": 0}) == "steps"
    assert budgets.over_any(budget, {"steps": 0, "in_chars": 100, "out_chars": 0}) == "in_chars"
    assert budgets.over_any(budget, {"steps": 0, "in_chars": 0, "out_chars": 100}) == "out_chars"
    assert budgets.over_any(budget, {"steps": 1, "in_chars": 99, "out_chars": 99}) is None


def test_over_any_at_boundary_is_exceeded():
    """已用 == 上限即触顶（>= 语义，与 gateway 预算边界一致）。"""
    budget = {"steps": 1, "in_chars": 10, "out_chars": 10}
    assert budgets.over_any(budget, {"steps": 1, "in_chars": 0, "out_chars": 0}) == "steps"


def test_remaining_reports_three_keys_with_unit():
    r = budgets.remaining(
        {"steps": 3, "in_chars": 100, "out_chars": 100},
        {"steps": 1, "in_chars": 40, "out_chars": 60},
    )
    assert r == {"steps": 2, "in_chars": 60, "out_chars": 40, "unit": "chars"}
