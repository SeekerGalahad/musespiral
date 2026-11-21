"""aihabits（风格飞轮 v1）与 ai.rewrite 习惯注入的单元测试。"""
from __future__ import annotations

import json

from musespiral.infra import ai
from musespiral.infra.ai import client as _ai_client
from musespiral.services.rewrite_service import rewrite
from musespiral.infra.ai.habits_inject import habits_prompt
from musespiral.core.habits import (
    Habit,
    extract_habits,
    load_habits,
    save_habits,
)


def _rules(habits: list[Habit]) -> list[str]:
    return [h.rule for h in habits]


def test_extract_no_change_returns_empty() -> None:
    text = "缓存是提升系统性能的第一道防线。\n\n合理设置过期时间同样关键。"
    assert extract_habits(text, text) == []


def test_extract_courtesy_removal() -> None:
    draft = (
        "大家好，欢迎来到我的频道，废话不多说，我们直接开始今天的话题。\n\n"
        "缓存是提升系统性能的第一道防线。\n\n"
        "合理设置过期时间同样关键。"
    )
    final = "缓存是提升系统性能的第一道防线。\n\n合理设置过期时间同样关键。"
    habits = extract_habits(draft, final)
    assert "客套删除" in _rules(habits)
    assert "直接进正题" in next(h for h in habits if h.rule == "客套删除").example


def test_extract_heading_change() -> None:
    draft = "# 论缓存策略的五种技术方案取舍\n\n正文第一段，内容一致。\n\n## 小结\n\n正文第二段，内容一致。"
    final = "# 缓存策略怎么选？一文讲透\n\n正文第一段，内容一致。\n\n## 小结\n\n正文第二段，内容一致。"
    habits = extract_habits(draft, final)
    assert "标题风格" in _rules(habits)
    example = next(h for h in habits if h.rule == "标题风格").example
    assert "论缓存策略的五种技术方案取舍" in example
    assert "缓存策略怎么选" in example


def test_extract_emoji_added() -> None:
    draft = "缓存是提升系统性能的第一道防线。\n\n合理设置过期时间同样关键。"
    final = "缓存是提升系统性能的第一道防线 🚀。\n\n合理设置过期时间同样关键 ✅。"
    habits = extract_habits(draft, final)
    assert "emoji 增删" in _rules(habits)
    assert "用户会加 emoji" in next(h for h in habits if h.rule == "emoji 增删").example


def test_extract_emoji_removed() -> None:
    draft = "缓存是提升系统性能的第一道防线 🚀。"
    final = "缓存是提升系统性能的第一道防线。"
    habits = extract_habits(draft, final)
    assert "emoji 增删" in _rules(habits)
    assert "用户会删 emoji" in next(h for h in habits if h.rule == "emoji 增删").example


def test_extract_paragraph_split() -> None:
    long_para = (
        "这是一个比较长的段落，讲述缓存的两个要点。"
        "其一是本地缓存，其二是分布式缓存，两者各有适用场景，需要结合业务规模来权衡取舍。"
    )
    draft = "第一段引入。\n\n" + long_para + "\n\n收尾段。"
    final = (
        "第一段引入。\n\n"
        "这是一个比较长的段落，讲述缓存的两个要点。\n\n"
        "其一是本地缓存，其二是分布式缓存，两者各有适用场景，需要结合业务规模来权衡取舍。\n\n"
        "收尾段。"
    )
    habits = extract_habits(draft, final)
    assert "长段拆分" in _rules(habits)
    assert "拆成 2 段" in next(h for h in habits if h.rule == "长段拆分").example


def test_extract_code_block_added() -> None:
    draft = "安装依赖后即可使用。\n\n配置文件放在用户目录下。"
    final = "安装依赖后即可使用。\n\n```bash\npip install musespiral\n```\n\n配置文件放在用户目录下。"
    habits = extract_habits(draft, final)
    assert "代码示例补充" in _rules(habits)
    assert "补充了 1 个代码块" in next(h for h in habits if h.rule == "代码示例补充").example


def test_extract_formal_word_casualized() -> None:
    draft = (
        "缓存可以显著降低数据库压力。因此我们建议在热点读路径上引入缓存层。\n\n"
        "同时要注意缓存一致性问题。"
    )
    final = (
        "缓存可以显著降低数据库压力。所以我们建议在热点读路径上引入缓存层。\n\n"
        "同时要注意缓存一致性问题。"
    )
    habits = extract_habits(draft, final)
    assert "书面词口语化" in _rules(habits)
    assert "「因此」→「所以」" in next(h for h in habits if h.rule == "书面词口语化").example


def test_save_habits_appends_not_overwrites(tmp_path) -> None:
    p = tmp_path / "habits.json"
    save_habits([Habit("客套删除", "不要开场客套")], p)
    save_habits([Habit("emoji 增删", "用户会加 emoji")], p)
    loaded = load_habits(p)
    assert _rules(loaded) == ["客套删除", "emoji 增删"]


def test_save_habits_dedupes_identical_entry(tmp_path) -> None:
    """F11b：rule+example 完全相同的条目跳过追加；同 rule 不同 example 保留。"""
    p = tmp_path / "habits.json"
    save_habits([Habit("客套删除", "不要开场客套")], p)
    save_habits([Habit("客套删除", "不要开场客套")], p)  # 完全相同 → 跳过
    loaded = load_habits(p)
    assert loaded == [Habit("客套删除", "不要开场客套")]
    # 同 rule 不同 example → 视为新观察，正常追加
    save_habits([Habit("客套删除", "曾整段删除「大家好，废话不多说…」")], p)
    loaded = load_habits(p)
    assert len(loaded) == 2
    assert all(h.rule == "客套删除" for h in loaded)
    assert loaded[1].example == "曾整段删除「大家好，废话不多说…」"


def test_repeat_learn_same_diff_does_not_double(tmp_path) -> None:
    """F11b learn 语义：同一对底稿/终稿重复 learn，条目不翻倍。"""
    draft = "大家好，废话不多说，我们开始。\n\n缓存是提升系统性能的第一道防线。"
    final = "缓存是提升系统性能的第一道防线 🚀。"
    p = tmp_path / "habits.json"
    save_habits(extract_habits(draft, final), p)
    save_habits(extract_habits(draft, final), p)  # 重复 learn 同一 diff
    loaded = load_habits(p)
    assert loaded == extract_habits(draft, final)


def test_load_habits_missing_or_broken_file(tmp_path) -> None:
    assert load_habits(tmp_path / "nope.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_habits(broken) == []


def test_default_path_under_mdpub_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    save_habits([Habit("客套删除", "x")])
    assert (tmp_path / "habits.json").exists()
    assert load_habits()[0].rule == "客套删除"


def test_habits_prompt_format() -> None:
    s = habits_prompt([Habit("客套删除", "不要开场客套，直接进正题"), Habit("长段拆分", "段落更短")])
    assert s.startswith("用户改稿习惯")
    assert "- 客套删除：不要开场客套，直接进正题" in s
    assert "- 长段拆分：段落更短" in s
    assert habits_prompt([]) == ""


class _FakeResp:
    status_code = 200
    text = ""

    @staticmethod
    def json() -> dict:
        return {"choices": [{"message": {"content": "改写后的正文内容。"}}]}


def _setup_ai_env(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    note = tmp_path / "note.md"
    note.write_text("缓存笔记内容，待改写。", encoding="utf-8")
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(_ai_client, "_post_chat_stream", lambda u,h,b,to: ai.adapt_mock(fake_post, u, h, b, to))
    return captured


def test_rewrite_injects_habits_into_system_prompt(tmp_path, monkeypatch) -> None:
    captured = _setup_ai_env(tmp_path, monkeypatch)
    save_habits([Habit("客套删除", "不要开场客套，直接进正题")])

    out = rewrite(tmp_path / "note.md", tmp_path / "article.md")
    assert out.exists()
    messages = captured["json"]["messages"]
    assert messages[0]["role"] == "system"
    assert "用户改稿习惯" in messages[0]["content"]
    assert "客套删除" in messages[0]["content"]
    assert messages[-1]["role"] == "user"
    assert "原始笔记" in messages[-1]["content"]


def test_rewrite_without_habits_skips_injection(tmp_path, monkeypatch) -> None:
    captured = _setup_ai_env(tmp_path, monkeypatch)

    rewrite(tmp_path / "note.md", tmp_path / "article.md")
    messages = captured["json"]["messages"]
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
