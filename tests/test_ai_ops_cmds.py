"""Phase D F79.2 M4 运维命令——ai cache/usage/budget（CLI 薄壳呈现层）。

数据面（stats/clear/summarize/load/apply_set）的单元测试在 test_ai_cache.py /
test_ai_usage.py / test_ai_budget.py；本文件只测 **CLI 组装**：参数解析、
退出码、输出行形态、落盘副作用。全部 MUSESPIRAL_HOME 沙箱（不触真实 ~/.musespiral）。

`agent runs --prune` 同属 M4 交付，已在 test_agent_service.py::test_cli_runs_prune
覆盖（本文件不重复）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.cli import cli


@pytest.fixture
def env(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _invoke(args: list[str]):
    return CliRunner().invoke(cli, args, prog_name="musespiral")


def _seed_cache(env: Path, n: int = 2) -> None:
    from musespiral.infra.ai import cache

    for i in range(n):
        cache.put(
            key=f"{i:016x}", task="tag_suggest", profile="local", temperature=0.6,
            template_sha_value="s", text=f"正文{i}", created_at=f"2026-11-17T10:0{i}:00",
        )


# ── ai cache ────────────────────────────────────────────────────────


def test_cache_empty_overview(env):
    res = _invoke(["ai", "cache"])
    assert res.exit_code == 0, res.output
    assert "缓存条目: 0/200" in res.output
    assert "（空）" in res.output


def test_cache_overview_and_stats_table(env):
    _seed_cache(env, 2)
    res = _invoke(["ai", "cache", "--stats"])
    assert res.exit_code == 0, res.output
    assert "缓存条目: 2/200" in res.output
    assert "2026-11-17T10:00:00" in res.output          # 最旧条目
    assert "按任务分布:" in res.output
    assert "tag_suggest: 2" in res.output


def test_cache_clear_prints_count_and_empties_dir(env):
    _seed_cache(env, 3)
    res = _invoke(["ai", "cache", "--clear"])
    assert res.exit_code == 0, res.output
    assert "已清除 3 个缓存条目" in res.output
    res2 = _invoke(["ai", "cache"])
    assert "缓存条目: 0/200" in res2.output


def test_cache_clear_without_stats_flag_is_default_view(env):
    """无 flag = 概览视图（不打印分布表）；--clear 优先于 --stats。"""
    _seed_cache(env, 1)
    res = _invoke(["ai", "cache"])
    assert res.exit_code == 0
    assert "按任务分布:" not in res.output


# ── ai usage ────────────────────────────────────────────────────────


def _seed_usage(env: Path) -> str:
    """种两行当日台账（返回所用日期）——**用 today 而非写死日期**：

    `--days 1` 的窗口按「今天」开（usage.summarize 缺省 today=date.today()），
    写死 2026-11-17 会在次日及以后全部落窗外（2026-11-18 实测 HEAD 即红——
    日期翻转脆弱判例）。"""
    from datetime import date

    from musespiral.infra.ai import usage

    today = date.today().isoformat()
    usage.append(ts=f"{today}T10:00:00", task="tag_suggest", profile="local",
                 cache_hit=False, chars_in=100, chars_out=40)
    usage.append(ts=f"{today}T10:01:00", task="tag_suggest", profile="local",
                 cache_hit=True, chars_in=100, chars_out=40)
    return today


def test_usage_table_rows_and_totals(env):
    today = _seed_usage(env)
    res = _invoke(["ai", "usage", "--days", "1"])
    assert res.exit_code == 0, res.output
    assert today in res.output                            # 日期列
    assert "tag_suggest" in res.output and "local" in res.output
    assert "命中率" in res.output and "50%" in res.output
    assert "合计: 调用 1 / 命中 1 / in 100 / out 40" in res.output


def test_usage_empty_reads_clean(env):
    res = _invoke(["ai", "usage"])
    assert res.exit_code == 0, res.output
    assert "（无记录）" in res.output
    assert "合计: 调用 0 / 命中 0 / in 0 / out 0" in res.output


def test_usage_does_not_write(env):
    _seed_usage(env)
    before = (env / "ai_usage.jsonl").read_bytes()
    _invoke(["ai", "usage", "--days", "7"])
    assert (env / "ai_usage.jsonl").read_bytes() == before   # 读侧现算不回写


# ── ai budget ───────────────────────────────────────────────────────


def test_budget_defaults_view(env):
    res = _invoke(["ai", "budget"])
    assert res.exit_code == 0, res.output
    assert "daily_calls=200" in res.output
    assert "daily_out_chars=200000" in res.output
    assert "per_task 覆盖: （无）" in res.output


def test_budget_set_global_persists(env):
    res = _invoke(["ai", "budget", "--set", "daily_calls=150"])
    assert res.exit_code == 0, res.output
    assert "✅ daily_calls = 150" in res.output
    assert "daily_calls=150" in res.output               # 落盘后视图同步
    raw = json.loads((env / "ai_budget.json").read_text(encoding="utf-8"))
    assert raw["daily_calls"] == 150


def test_budget_set_multiple_and_per_task(env):
    res = _invoke([
        "ai", "budget",
        "--set", "per_task.tag_suggest.daily_calls=5",
        "--set", "daily_out_chars=300000",
    ])
    assert res.exit_code == 0, res.output
    assert "✅ per_task.tag_suggest.daily_calls = 5" in res.output
    assert "✅ daily_out_chars = 300000" in res.output
    assert "tag_suggest: daily_calls=5" in res.output    # per_task 视图行
    data = json.loads((env / "ai_budget.json").read_text(encoding="utf-8"))
    assert data["per_task"]["tag_suggest"] == {"daily_calls": 5}
    assert data["daily_out_chars"] == 300_000


def test_budget_set_bad_expr_exit_1_readable(env):
    res = _invoke(["ai", "budget", "--set", "bogus=1"])
    assert res.exit_code == 1
    assert "❌" in res.output and "--set" in res.output
    assert not (env / "ai_budget.json").exists()         # 非法表达式零落盘


def test_budget_set_zero_rejected(env):
    res = _invoke(["ai", "budget", "--set", "daily_calls=0"])
    assert res.exit_code == 1
    assert ">0" in res.output
