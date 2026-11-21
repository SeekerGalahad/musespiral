"""AI 日预算（§五 F79-3 预算超限 + per_task 覆盖 / F79-2 `--set` 点路径）。

F79-3 的关键语义在 `budget.check(task, today, task_usage=…)`：**per_task 覆盖
= 该任务用自己的计数比自己的上限**，而不是拿全局计数去比一个小值（否则
「synthesize 限 3 次」会被别的任务的调用误伤）。
"""
from __future__ import annotations

import json

import pytest

from musespiral.infra.ai import budget


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


# ── 缺省与读写 ─────────────────────────────────────────────────────


def test_defaults_when_file_missing(env):
    data = budget.load()
    assert data["daily_calls"] == budget.DEFAULT_DAILY_CALLS == 200
    assert data["daily_out_chars"] == budget.DEFAULT_DAILY_OUT_CHARS == 200_000
    assert data["per_task"] == {}


def test_save_then_load_roundtrip(env):
    budget.save({"daily_calls": 150, "daily_out_chars": 300_000,
                 "per_task": {"compose": {"daily_calls": 10}}})
    data = budget.load()
    assert data["daily_calls"] == 150
    assert data["daily_out_chars"] == 300_000
    assert data["per_task"]["compose"]["daily_calls"] == 10


def test_save_is_atomic_no_tmp_leftover(env):
    budget.save({"daily_calls": 5})
    d = env
    assert not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_bad_json_falls_back_to_defaults(env):
    (env / "ai_budget.json").write_text("{broken", encoding="utf-8")
    assert budget.load()["daily_calls"] == budget.DEFAULT_DAILY_CALLS


def test_bad_values_fall_back_per_key(env):
    (env / "ai_budget.json").write_text(
        json.dumps({"daily_calls": -5, "daily_out_chars": "many"}), encoding="utf-8"
    )
    data = budget.load()
    assert data["daily_calls"] == budget.DEFAULT_DAILY_CALLS
    assert data["daily_out_chars"] == budget.DEFAULT_DAILY_OUT_CHARS


def test_per_task_bad_values_skipped(env):
    (env / "ai_budget.json").write_text(
        json.dumps({"per_task": {"compose": {"daily_calls": 0}, "x": "not-dict"}}),
        encoding="utf-8",
    )
    assert budget.load()["per_task"] == {}


# ── limits_for：per_task 逐键覆盖全局 ──────────────────────────────


def test_limits_inherit_global_when_not_overridden(env):
    budget.save({"daily_calls": 100, "daily_out_chars": 5000,
                 "per_task": {"compose": {"daily_calls": 10}}})
    limits = budget.limits_for("compose")
    assert limits["daily_calls"] == 10          # 覆盖
    assert limits["daily_out_chars"] == 5000    # 继承


def test_limits_for_unlisted_task_all_global(env):
    budget.save({"daily_calls": 100, "daily_out_chars": 5000, "per_task": {}})
    assert budget.limits_for("tag_suggest") == {"daily_calls": 100, "daily_out_chars": 5000}


# ── F79-3：check 超限判定 ──────────────────────────────────────────


def test_check_passes_when_under_limits(env):
    ok, reason = budget.check("tag_suggest", {"calls": 0, "out_chars": 0})
    assert ok is True and reason is None


def test_check_rejects_at_call_limit_with_readable_message(env):
    budget.save({"daily_calls": 1, "daily_out_chars": 200_000, "per_task": {}})
    ok, reason = budget.check("tag_suggest", {"calls": 1, "out_chars": 0})
    assert ok is False
    assert "tag_suggest" in reason
    assert "今日已用 1" in reason and "上限 1" in reason
    assert "musespiral ai budget" in reason, "须含引导语"


def test_check_rejects_at_out_chars_limit(env):
    budget.save({"daily_calls": 200, "daily_out_chars": 100, "per_task": {}})
    ok, reason = budget.check("t", {"calls": 0, "out_chars": 100})
    assert ok is False and "字符" in reason


def test_check_boundary_is_inclusive(env):
    """已用 == 上限即拒（>= 语义）。"""
    budget.save({"daily_calls": 5, "daily_out_chars": 200_000, "per_task": {}})
    assert budget.check("t", {"calls": 4, "out_chars": 0})[0] is True
    assert budget.check("t", {"calls": 5, "out_chars": 0})[0] is False


def test_per_task_override_uses_its_own_counter(env):
    """**核心**：per_task.agent_synthesize.daily_calls=3，全局 200。

    - synthesize 自己的计数 3 → 拒（按 3）
    - 但全局计数已到 50（别的任务在用）→ **仍放行**（不被误伤）
    """
    budget.save({
        "daily_calls": 200, "daily_out_chars": 200_000,
        "per_task": {"agent_synthesize": {"daily_calls": 3}},
    })
    ok, reason = budget.check(
        "agent_synthesize",
        {"calls": 50, "out_chars": 0},          # 全局计数很多
        task_usage={"calls": 2, "out_chars": 0},  # 本任务 2 < 3
    )
    assert ok is True, f"per_task 覆盖不该被全局计数误伤: {reason}"

    ok2, reason2 = budget.check(
        "agent_synthesize",
        {"calls": 50, "out_chars": 0},
        task_usage={"calls": 3, "out_chars": 0},
    )
    assert ok2 is False and "该任务今日" in reason2


def test_per_task_independent_across_tasks(env):
    """同日 tag_suggest 不受 agent_synthesize 的 per_task 影响（独立）。"""
    budget.save({
        "daily_calls": 200, "daily_out_chars": 200_000,
        "per_task": {"agent_synthesize": {"daily_calls": 3}},
    })
    assert budget.check("agent_synthesize", {"calls": 1, "out_chars": 0},
                        task_usage={"calls": 3, "out_chars": 0})[0] is False
    assert budget.check("tag_suggest", {"calls": 1, "out_chars": 0},
                        task_usage={"calls": 3, "out_chars": 0})[0] is True


# ── --set 点路径表达式 ─────────────────────────────────────────────


def test_parse_set_global_keys():
    assert budget.parse_set_expression("daily_calls=150") == ("daily_calls", 150)
    assert budget.parse_set_expression("daily_out_chars=300000") == ("daily_out_chars", 300_000)


def test_parse_set_per_task_key():
    assert budget.parse_set_expression("per_task.compose.daily_calls=10") == (
        "per_task.compose.daily_calls", 10,
    )


def test_parse_set_rejects_bad_forms():
    for bad in ("daily_calls", "daily_calls=x", "daily_calls=0", "daily_calls=-1",
                "per_task.compose", "per_task.compose.nope=1",
                "per_task..daily_calls=1", "other=1"):
        with pytest.raises(ValueError):
            budget.parse_set_expression(bad)


def test_apply_set_global(env):
    key, value, data = budget.apply_set("daily_calls=150")
    assert (key, value) == ("daily_calls", 150)
    assert data["daily_calls"] == 150
    assert budget.load()["daily_calls"] == 150


def test_apply_set_per_task_creates_and_merges(env):
    budget.apply_set("per_task.compose.daily_calls=10")
    budget.apply_set("per_task.compose.daily_out_chars=500")
    data = budget.load()
    assert data["per_task"]["compose"] == {"daily_calls": 10, "daily_out_chars": 500}


def test_apply_set_two_tasks_independent(env):
    budget.apply_set("per_task.compose.daily_calls=10")
    budget.apply_set("per_task.tag_suggest.daily_calls=5")
    data = budget.load()
    assert data["per_task"]["compose"]["daily_calls"] == 10
    assert data["per_task"]["tag_suggest"]["daily_calls"] == 5


def test_apply_set_persists_as_valid_json(env):
    budget.apply_set("daily_out_chars=250000")
    raw = json.loads((env / "ai_budget.json").read_text(encoding="utf-8"))
    assert raw["daily_out_chars"] == 250000
    assert "per_task" in raw


# ── budget 与 ai.json 分离（设计 §1.2 迁移表裁决）───────────────────


def test_budget_file_is_separate_from_ai_json(env):
    """预算状态独立于档案配置（ai.json 仍是档案+路由唯一事实源）。"""
    budget.save({"daily_calls": 99})
    names = {p.name for p in env.iterdir()}
    assert "ai_budget.json" in names
    assert "ai.json" not in names, "预算不得写进 ai.json"
