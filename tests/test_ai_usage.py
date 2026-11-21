"""AI 用量台账（§五 F79-5 治理文件无敏感 + F79-2 计量/出账口径）。

红线断言的重点：canary 注入后全文件 grep 零命中（沿
`test_studio_refine.test_refine_error_never_echoes_api_key` 先例），
以及**结构 schema**（键集恒等于 ROW_KEYS 的子集——没有可以塞正文的键）。
"""
from __future__ import annotations

import json

import pytest

from musespiral.infra.ai import usage


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


def _rows(env):
    path = env / "ai_usage.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── 行形状与红线（无正文/无密钥）─────────────────────────────────────


def test_append_row_shape_is_exactly_row_keys(env):
    row = usage.append(
        ts="2026-11-17T10:00:00", task="tag_suggest", profile="local",
        cache_hit=False, chars_in=100, chars_out=40,
    )
    assert set(row) == set(usage.ROW_KEYS) - {"run_id"}
    assert _rows(env)[-1] == row


def test_append_with_run_id_includes_key(env):
    row = usage.append(
        ts="2026-11-17T10:00:00", task="agent_extract", profile="local",
        cache_hit=False, chars_in=10, chars_out=5, run_id="run-123",
    )
    assert row["run_id"] == "run-123"
    assert set(row) <= set(usage.ROW_KEYS)


def test_row_keys_contain_no_content_fields():
    """结构 schema 断言：键集里没有任何可承载 prompt/输出正文的键。"""
    for forbidden in ("prompt", "text", "messages", "content", "api_key", "output", "body"):
        assert forbidden not in usage.ROW_KEYS


def test_append_signature_rejects_body_fields():
    """签名层即红线：`append` 不接受 text/prompt 参数（无处可传）。"""
    with pytest.raises(TypeError):
        usage.append(  # type: ignore[call-arg]
            ts="2026-11-17T10:00:00", task="t", profile="p", cache_hit=False,
            chars_in=1, chars_out=1, text="不该能传",
        )


def test_canary_key_never_appears_in_usage_file(env):
    """注入 canary api_key 与 canary 正文后，全文件 grep 零命中。

    模拟：调用方手里有密钥与 prompt，但只有计量数字流进 append——
    文件里因此不可能出现它们。
    """
    canary_key = "sk-CANARY-API-KEY-999"
    canary_prompt = "CANARY-PROMPT-BODY-should-never-persist"
    canary_output = "CANARY-OUTPUT-BODY-should-never-persist"
    # 服务层会拿这些调用 AI，但它的 append 只传了计量
    usage.append(
        ts="2026-11-17T10:00:00", task="tag_suggest", profile="local",
        cache_hit=False, chars_in=len(canary_prompt), chars_out=len(canary_output),
    )
    blob = (env / "ai_usage.jsonl").read_text(encoding="utf-8")
    assert canary_key not in blob
    assert canary_prompt not in blob
    assert canary_output not in blob


def test_jsonl_append_only_preserves_existing_lines(env):
    """追加式纪律：先前行字节不动（JSONL 只增）。"""
    usage.append(ts="2026-11-17T09:00:00", task="a", profile="p",
                 cache_hit=False, chars_in=1, chars_out=1)
    first = (env / "ai_usage.jsonl").read_text(encoding="utf-8")
    usage.append(ts="2026-11-17T09:01:00", task="b", profile="p",
                 cache_hit=False, chars_in=2, chars_out=2)
    second = (env / "ai_usage.jsonl").read_text(encoding="utf-8")
    assert second.startswith(first)


def test_negative_chars_clamped_to_zero(env):
    row = usage.append(ts="2026-11-17T10:00:00", task="t", profile="p",
                       cache_hit=False, chars_in=-5, chars_out=-1)
    assert row["chars_in"] == 0 and row["chars_out"] == 0


def test_bad_lines_are_skipped_on_read(env):
    path = env / "ai_usage.jsonl"
    path.write_text('{"ts":"2026-11-17T10:00:00","task":"a"}\n\ngarbage\n[1,2]\n',
                    encoding="utf-8")
    rows = usage.read_all()
    assert len(rows) == 1 and rows[0]["task"] == "a"


# ── today_summary / task_summary：命中不出账 ────────────────────────


def test_today_summary_counts_only_misses(env):
    """命中行只计数（hits），不进 calls/out_chars（预算对账口径）。"""
    usage.append(ts="2026-11-17T10:00:00", task="t", profile="p",
                 cache_hit=False, chars_in=100, chars_out=50)
    usage.append(ts="2026-11-17T10:01:00", task="t", profile="p",
                 cache_hit=True, chars_in=100, chars_out=50)
    usage.append(ts="2026-11-16T10:00:00", task="t", profile="p",  # 昨日不计
                 cache_hit=False, chars_in=999, chars_out=999)
    s = usage.today_summary("2026-11-17")
    assert s == {"calls": 1, "out_chars": 50, "hits": 1}


def test_task_summary_filters_by_task(env):
    usage.append(ts="2026-11-17T10:00:00", task="a", profile="p",
                 cache_hit=False, chars_in=10, chars_out=5)
    usage.append(ts="2026-11-17T10:01:00", task="b", profile="p",
                 cache_hit=False, chars_in=20, chars_out=7)
    assert usage.task_summary("a", "2026-11-17") == {"calls": 1, "out_chars": 5, "hits": 0}
    assert usage.task_summary("b", "2026-11-17")["out_chars"] == 7


def test_today_summary_empty(env):
    assert usage.today_summary("2026-11-17") == {"calls": 0, "out_chars": 0, "hits": 0}


# ── summarize：日汇总表 ─────────────────────────────────────────────


def test_summarize_rows_and_totals(env):
    usage.append(ts="2026-11-17T10:00:00", task="a", profile="local",
                 cache_hit=False, chars_in=100, chars_out=40)
    usage.append(ts="2026-11-17T10:01:00", task="a", profile="local",
                 cache_hit=True, chars_in=100, chars_out=40)
    s = usage.summarize(days=1, today="2026-11-17")
    assert len(s["rows"]) == 1
    row = s["rows"][0]
    assert (row["task"], row["profile"]) == ("a", "local")
    assert row["calls"] == 1 and row["hits"] == 1
    assert row["hit_rate"] == 0.5
    assert s["totals"] == {"calls": 1, "hits": 1, "chars_in": 100, "chars_out": 40}


def test_summarize_window_excludes_older_days(env):
    usage.append(ts="2026-11-15T10:00:00", task="old", profile="p",
                 cache_hit=False, chars_in=5, chars_out=5)
    usage.append(ts="2026-11-17T10:00:00", task="new", profile="p",
                 cache_hit=False, chars_in=5, chars_out=5)
    s = usage.summarize(days=1, today="2026-11-17")
    assert [r["task"] for r in s["rows"]] == ["new"]
    s7 = usage.summarize(days=3, today="2026-11-17")
    assert {r["task"] for r in s7["rows"]} == {"old", "new"}


def test_summarize_empty(env):
    s = usage.summarize(days=7, today="2026-11-17")
    assert s["rows"] == [] and s["totals"] == {"calls": 0, "hits": 0, "chars_in": 0, "chars_out": 0}
