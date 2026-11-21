"""digest（备选摘要候选）单元测试——AI 路径全程 monkeypatch requests.post，不真实联网。"""
from __future__ import annotations

import json

from musespiral.core.quality import digest
from musespiral.infra.ai import client as ai_mod


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _write_article(tmp_path, content: str):
    p = tmp_path / "article.md"
    p.write_text(f"---\ntitle: 测试文章\ndigest: 旧摘要\n---\n\n{content}", encoding="utf-8")
    return p


def _no_ai(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))  # 无 ai.json → 走启发式


def _setup_ai(tmp_path, monkeypatch, ai_content: str) -> dict:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResp(ai_content)

    monkeypatch.setattr(ai_mod, "_post_chat_stream", lambda u,h,b,to: ai_mod.adapt_mock(fake_post, u, h, b, to))
    return captured


def test_heuristic_two_candidates_within_54(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    p = _write_article(
        tmp_path,
        "缓存是提升系统性能的第一道防线。\n\n"
        "实测引入多级缓存后，接口耗时从 320ms 降到了 45ms。\n\n"
        "过期时间的设置同样关键。",
    )
    cands = digest.candidates(p)
    assert len(cands) == 2
    assert all(len(c) <= 54 for c in cands)
    assert cands[0] == "缓存是提升系统性能的第一道防线。"
    assert "320ms" in cands[1]  # 含数字的句子被截取为第二条


def test_heuristic_fallback_to_first_line(tmp_path, monkeypatch) -> None:
    _no_ai(tmp_path, monkeypatch)
    p = _write_article(tmp_path, "一篇没有任何句末标号的短文首行\n\n第二行同样没有标号")
    assert digest.candidates(p) == ["一篇没有任何句末标号的短文首行"]


def test_ai_path_parses_three_fenced_json(tmp_path, monkeypatch) -> None:
    captured = _setup_ai(
        tmp_path, monkeypatch, '```json\n["导语式候选", "结论式候选", "悬念式候选"]\n```'
    )
    p = _write_article(tmp_path, "任意正文。\n\n第二句带数字 123。")
    cands = digest.candidates(p)
    assert cands == ["导语式候选", "结论式候选", "悬念式候选"]
    assert "文章正文" in captured["json"]["messages"][0]["content"]


def test_ai_path_parses_line_by_line(tmp_path, monkeypatch) -> None:
    _setup_ai(tmp_path, monkeypatch, "1. 导语式候选\n2. 结论式候选\n3. 悬念式候选")
    p = _write_article(tmp_path, "任意正文。\n\n第二句带数字 123。")
    assert digest.candidates(p) == ["导语式候选", "结论式候选", "悬念式候选"]


def test_ai_candidates_trimmed_to_54(tmp_path, monkeypatch) -> None:
    long_text = "这是一条远远超过五十四个字的超长候选摘要需要被修剪截断处理才能进入候选清单" * 2
    _setup_ai(tmp_path, monkeypatch, json.dumps([long_text, "短候选"], ensure_ascii=False))
    p = _write_article(tmp_path, "任意正文。")
    cands = digest.candidates(p)
    assert cands[1] == "短候选"
    assert len(cands[0]) <= 54
    assert cands[0].endswith("…")
