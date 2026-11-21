"""factcheck（AI 事实自检）单元测试——requests.post 全程 monkeypatch，不真实联网。"""
from __future__ import annotations

import json

import pytest
import requests

from musespiral.core.quality import factcheck
from musespiral.infra.ai import client as ai_mod


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _setup(tmp_path, monkeypatch, ai_content: str) -> dict:
    """配置假 AI（ai.json + monkeypatch post），返回捕获请求载荷的字典。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    (tmp_path / "note.md").write_text(
        "今天的笔记：缓存命中率 92%。\n\n过期时间建议 10 分钟。", encoding="utf-8"
    )
    (tmp_path / "article.md").write_text(
        "---\ntitle: 缓存实战\ndigest: 短摘要\n---\n\n缓存是性能的第一道防线。\n\n据说命中率高达 99%。",
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp(ai_content)

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(fake_post, u, h, b, to))
    return captured


def test_check_facts_parses_claim_list(tmp_path, monkeypatch) -> None:
    captured = _setup(tmp_path, monkeypatch, '[{"claim": "命中率高达 99%", "where": "第二段"}]')
    flags = factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md")
    assert flags == [{"claim": "命中率高达 99%", "where": "第二段"}]
    # 请求同时携带原笔记与改写稿全文
    prompt = captured["json"]["messages"][0]["content"]
    assert "缓存命中率 92%" in prompt
    assert "命中率高达 99%" in prompt
    assert captured["url"].endswith("/chat/completions")


def test_check_facts_tolerates_code_fence(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, '```json\n[{"claim": "数字 A", "where": "第一段"}]\n```')
    flags = factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md")
    assert flags == [{"claim": "数字 A", "where": "第一段"}]


def test_check_facts_empty_array_means_clean(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, "[]")
    assert factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md") == []


def test_check_facts_invalid_json_raises_unavailable(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, "抱歉，我无法完成这个任务。")
    with pytest.raises(factcheck.FactcheckUnavailable):
        factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md")


def test_check_facts_network_error_raises_unavailable(tmp_path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch, "[]")

    def fake_post(url, headers=None, json=None, timeout=None):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(fake_post, u, h, b, to))
    with pytest.raises(factcheck.FactcheckUnavailable):
        factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md")


def test_check_facts_not_configured(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json → 未配置

    def bomb(*args, **kwargs):
        raise AssertionError("AI 未配置时不应发起请求")

    monkeypatch.setattr(ai_mod, "_post_chat_stream", bomb)
    with pytest.raises(factcheck.FactcheckNotConfigured):
        factcheck.check_facts(tmp_path / "note.md", tmp_path / "article.md")


# ── C4（第十四期顺手批）：check-facts 报告同列呈现违禁词命中 ──


def _pair(tmp_path, body: str):
    note = tmp_path / "note.md"
    article = tmp_path / "article.md"
    note.write_text(f"---\ntitle: 原笔记\n---\n\n{body}\n", encoding="utf-8")
    article.write_text(f"---\ntitle: 改写稿\n---\n\n{body}\n", encoding="utf-8")
    return note, article


def test_check_facts_cli_lists_banned_words_same_column(tmp_path, monkeypatch) -> None:
    """AI 增量事实与违禁词命中在同一编号列续编呈现（F33A 遗留末句）。"""
    from click.testing import CliRunner

    from musespiral.cli import cli

    note, article = _pair(tmp_path, "这套方案最好用。\n\n第二段。")
    monkeypatch.setattr(
        factcheck, "check_facts",
        lambda n, a: [{"claim": "提速 3 倍", "where": "首段"}],
    )
    r = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert r.exit_code == 0
    assert "发现 1 条增量事实" in r.output
    assert "  1. 提速 3 倍（首段）" in r.output
    assert "违禁词命中 1 处（同列呈现，只标记不否决）" in r.output
    assert "  2. 最好×L1" in r.output  # 与 AI 事实同列续编（编号接续）


def test_check_facts_cli_banned_words_without_ai_flags(tmp_path, monkeypatch) -> None:
    """AI 无增量事实时违禁词仍呈现；AI 不可用（降级）也不丢违禁词列。"""
    from click.testing import CliRunner

    from musespiral.cli import cli

    note, article = _pair(tmp_path, "性能提升明显，堪称完美。\n\n正文。")
    monkeypatch.setattr(factcheck, "check_facts", lambda n, a: [])
    r = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert r.exit_code == 0
    assert "未发现增量事实" in r.output
    assert "完美×L1" in r.output

    def unavailable(n, a):
        raise factcheck.FactcheckUnavailable("自检不可用：网络失败")

    monkeypatch.setattr(factcheck, "check_facts", unavailable)
    r2 = CliRunner().invoke(cli, ["ai", "check-facts", str(article), "--note", str(note)])
    assert r2.exit_code == 0
    assert "自检不可用" in r2.output
    assert "完美×L1" in r2.output
