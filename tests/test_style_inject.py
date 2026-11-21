"""F44 风格块注入单元测试——style_block 组装 + 六任务注入 + style init。

六任务（rewrite/adapt/title/digest/annotate_rewrite/discover_suggest）全部
mock chat 断言 system prompt 含 style 块（边界标记/节标题/范文首句）；空资产
不注入不报错；habits 超 12 条、范文超 3 段截断；style init 抽段与模板两路径。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.infra.ai import client as ai_client
from musespiral.infra.ai.habits_inject import (
    HABITS_MAX,
    STYLE_CLOSE,
    STYLE_HEADER,
    STYLE_OPEN,
    load_style_samples,
    sample_paragraphs,
    style_block,
)
from musespiral.core.habits import Habit

# 范文首句锚点（注入断言用）
SAMPLE_FIRST = "样例范文首句在此，语气要稳"


class _FakeResp:
    status_code = 200
    text = ""

    def __init__(self, content: str) -> None:
        self._content = content

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


def _fake_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reply: str) -> dict:
    """隔离 MUSESPIRAL_HOME + ai.json + mock 网络：AI 路径不真实联网，捕获发出的 body。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    (tmp_path / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )
    captured: dict = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResp(reply)

    monkeypatch.setattr(
        ai_client, "_post_chat_stream", lambda u, h, b, to: ai_client.adapt_mock(fake_post, u, h, b, to)
    )
    return captured


def _write_assets(home: Path, *, habits: bool = True, samples: str | None = None) -> None:
    """写入风格资产两件（现读现拼的注入源）；samples=None 用默认双段范文。"""
    if habits:
        (home / "habits.json").write_text(
            json.dumps([{"rule": "客套删除", "example": "不要开场客套，直接进正题"}], ensure_ascii=False),
            encoding="utf-8",
        )
    if samples is None:
        samples = f"<!-- 维护注释，不注入 -->\n\n{SAMPLE_FIRST}。第二句收尾。\n\n第二段范文。"
    (home / "style_samples.md").write_text(samples, encoding="utf-8")


def _assert_style_system(msgs: list[dict]) -> None:
    """公共断言：首消息为 system，含边界标记包裹的 style 块与范文首句。"""
    assert msgs[0]["role"] == "system"
    content = msgs[0]["content"]
    assert content.count(STYLE_OPEN) == 1 and content.count(STYLE_CLOSE) == 1
    assert STYLE_HEADER in content
    assert "客套删除" in content
    assert SAMPLE_FIRST in content  # 范文首句进 prompt
    assert msgs[-1]["role"] == "user"


# ── style_block 纯组装 ────────────────────────────────────────────────


def test_style_block_full_shape() -> None:
    s = style_block([Habit("客套删除", "不要开场客套")], f"{SAMPLE_FIRST}。")
    assert s.startswith(STYLE_OPEN) and s.endswith(STYLE_CLOSE)
    assert STYLE_HEADER in s
    assert "用户改稿习惯" in s and "- 客套删除：不要开场客套" in s
    assert "【范文 1】" in s and SAMPLE_FIRST in s


def test_style_block_both_empty_returns_empty_string() -> None:
    assert style_block([], "") == ""


def test_style_block_habits_only_and_samples_only() -> None:
    habits_only = style_block([Habit("长段拆分", "段落更短")], "")
    assert habits_only.startswith(STYLE_OPEN) and "【范文" not in habits_only
    samples_only = style_block([], f"{SAMPLE_FIRST}。")
    assert samples_only.startswith(STYLE_OPEN) and "用户改稿习惯" not in samples_only
    assert SAMPLE_FIRST in samples_only


def test_style_block_truncates_habits_at_12() -> None:
    habits = [Habit(f"规则{i:02d}", f"例{i}") for i in range(1, 16)]  # 15 条
    s = style_block(habits, "")
    assert "规则12" in s and "规则13" not in s
    assert len(sample_paragraphs("")) == 0  # 顺带：空范文零段
    assert HABITS_MAX == 12


def test_sample_paragraphs_truncates_at_3_and_skips_comments() -> None:
    text = "\n\n".join(
        ["<!-- 模板注释不注入 -->", "第一段。", "第二段。", "第三段。", "第四段。"]
    )
    paras = sample_paragraphs(text)
    assert paras == ["第一段。", "第二段。", "第三段。"]
    assert sample_paragraphs("<!-- 整文件只有注释 -->") == []


def test_load_style_samples_missing_and_present(tmp_path: Path) -> None:
    assert load_style_samples(tmp_path / "nope.md") == ""
    p = tmp_path / "style_samples.md"
    p.write_text(f"{SAMPLE_FIRST}。\n", encoding="utf-8")
    assert load_style_samples(p) == f"{SAMPLE_FIRST}。"


# ── 六任务注入（mock chat 断言 prompt）───────────────────────────────


def test_rewrite_injects_style_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.services.rewrite_service import rewrite

    captured = _fake_ai(tmp_path, monkeypatch, "改写后的正文内容。")
    _write_assets(tmp_path)
    (tmp_path / "note.md").write_text("缓存笔记，待改写。", encoding="utf-8")

    rewrite(tmp_path / "note.md", tmp_path / "article.md")
    _assert_style_system(captured["json"]["messages"])


def test_adapt_injects_style_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.core.adaptation import generator

    captured = _fake_ai(tmp_path, monkeypatch, "小红书正文\n\n话题：#测试")
    _write_assets(tmp_path)
    src = tmp_path / "a.md"
    src.write_text("---\ntitle: 测试\n---\n\n正文段落，适配小红书。", encoding="utf-8")

    generator.generate(src, "xhs")
    _assert_style_system(captured["json"]["messages"])


def test_title_injects_style_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.core.quality import title

    captured = _fake_ai(tmp_path, monkeypatch, '["标题一", "标题二", "标题三"]')
    _write_assets(tmp_path)
    p = tmp_path / "art.md"
    p.write_text("---\ntitle: T\n---\n\n正文首句。", encoding="utf-8")

    title.candidates(p)
    _assert_style_system(captured["json"]["messages"])


def test_digest_injects_style_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.core.quality import digest

    captured = _fake_ai(tmp_path, monkeypatch, '["导语式候选", "结论式候选", "悬念式候选"]')
    _write_assets(tmp_path)
    p = tmp_path / "art.md"
    p.write_text("---\ntitle: T\n---\n\n正文第一段。\n\n第二段带数字 123。", encoding="utf-8")

    digest.candidates(p)
    _assert_style_system(captured["json"]["messages"])


def test_annotate_rewrite_injects_style_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.services.annotate_service import annotate_rewrite

    captured = _fake_ai(tmp_path, monkeypatch, "改写后的完整正文。")
    _write_assets(tmp_path)
    p = tmp_path / "art.md"
    p.write_text("---\ntitle: T\n---\n\n第一段正文。\n\n第二段正文。", encoding="utf-8")

    annotate_rewrite(p, [{"line_start": 1, "line_end": 1, "comment": "改口语"}])
    _assert_style_system(captured["json"]["messages"])


def test_discover_suggest_appends_style_after_task_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discover_suggest 既有 system 任务指令——style 块追加在任务指令之后。"""
    from musespiral.core.discovery import suggester

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    _write_assets(tmp_path)
    captured: list[list[dict]] = []

    def fake_chat(messages, **kw):
        captured.append(messages)
        return "AI 标题\nAI 导语"

    items = [{"title": "源码分析-demo", "skeleton_hint": "角度参考"}]
    assert suggester.ai_enhance(items, top_n=1, chat=fake_chat) == 1
    system = captured[0][0]
    assert system["role"] == "system"
    assert system["content"].startswith("你是资深技术公众号编辑。")
    assert f"你是资深技术公众号编辑。\n\n{STYLE_OPEN}" in system["content"]  # 位置=任务指令之后
    assert SAMPLE_FIRST in system["content"]


# ── 空资产不注入不报错 ────────────────────────────────────────────────


def test_no_assets_skips_injection_without_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.services.rewrite_service import rewrite

    captured = _fake_ai(tmp_path, monkeypatch, "改写后的正文内容。")  # 两资产全无
    (tmp_path / "note.md").write_text("缓存笔记，待改写。", encoding="utf-8")

    rewrite(tmp_path / "note.md", tmp_path / "article.md")  # 不报错
    msgs = captured["json"]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"


def test_template_only_samples_zero_injection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """范文文件只有模板注释块（未填写）→ 零有效段 → 不注入。"""
    from musespiral.services.rewrite_service import rewrite

    captured = _fake_ai(tmp_path, monkeypatch, "改写后的正文内容。")
    _write_assets(tmp_path, habits=False, samples="<!-- 模板：粘贴 2-3 段定稿范文 -->")
    (tmp_path / "note.md").write_text("缓存笔记，待改写。", encoding="utf-8")

    rewrite(tmp_path / "note.md", tmp_path / "article.md")
    msgs = captured["json"]["messages"]
    assert len(msgs) == 1 and msgs[0]["role"] == "user"


def test_discover_suggest_system_unchanged_without_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from musespiral.core.discovery import suggester

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path))
    captured: list[list[dict]] = []
    suggester.ai_enhance(
        [{"title": "t", "skeleton_hint": "h"}],
        chat=lambda messages, **kw: captured.append(messages) or "标\n语",
    )
    assert captured[0][0]["content"] == "你是资深技术公众号编辑。"


# ── style init（抽段与模板两路径）────────────────────────────────────


def _final(root: Path, name: str, status: str, body: str) -> Path:
    p = root / name
    p.write_text(f"---\ntitle: {name}\nstatus: {status}\n---\n\n{body}", encoding="utf-8")
    return p


def test_style_init_extracts_from_published_and_reviewed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.commands.style_cmds import init_style

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    root = tmp_path / "arts"
    root.mkdir()
    _final(root, "a.md", "published", "# 标题\n\nA 的开头段，语气要稳。")
    _final(root, "b.md", "reviewed", "B 的开头段。")
    _final(root, "c.md", "draft", "草稿不入样。")
    _final(root, "d.md.annotated.md", "reviewed", "批注预览不入样。")  # 产物 sidecar 排除

    out, sources = init_style(root)
    assert out == tmp_path / "home" / "style_samples.md"
    assert sorted(sources) == ["a.md", "b.md"]
    text = out.read_text(encoding="utf-8")
    assert "A 的开头段" in text and "B 的开头段" in text
    assert "草稿不入样" not in text and "批注预览不入样" not in text
    assert len(sample_paragraphs(load_style_samples(out))) == 2  # 说明注释不计段


def test_style_init_caps_at_three_excerpts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.commands.style_cmds import init_style

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    root = tmp_path / "arts"
    root.mkdir()
    for i in range(4):
        _final(root, f"f{i}.md", "published", f"第 {i} 篇的开头段。")

    out, sources = init_style(root)
    assert len(sources) == 3
    assert len(sample_paragraphs(load_style_samples(out))) == 3


def test_style_init_template_when_no_finals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.commands.style_cmds import init_style

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    root = tmp_path / "arts"
    root.mkdir()
    _final(root, "only_draft.md", "draft", "草稿正文。")

    out, sources = init_style(root)
    assert sources == []
    text = out.read_text(encoding="utf-8").strip()
    assert text.startswith("<!--") and text.endswith("-->")  # 单一注释块模板
    assert sample_paragraphs(load_style_samples(out)) == []  # 未填写零注入


def test_style_init_missing_root_writes_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.commands.style_cmds import init_style

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    out, sources = init_style(tmp_path / "no-such-root")
    assert sources == [] and out.exists()


def test_style_init_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from musespiral.commands.style_cmds import init_style

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "style_samples.md").write_text("既有范文", encoding="utf-8")
    with pytest.raises(FileExistsError, match="拒绝覆盖"):
        init_style(tmp_path / "arts")


def test_extract_sample_paragraph_skips_and_truncates() -> None:
    from musespiral.commands.style_cmds import extract_sample_paragraph

    content = "# 主标题\n\n> 引用段\n\n- 列表项\n\n`代码`\n\n<!-- 注释 -->\n\n话题：#标签\n\n真正的散文段落。"
    assert extract_sample_paragraph(content) == "真正的散文段落。"
    long_para = "长" * 400
    got = extract_sample_paragraph(long_para)
    assert got.endswith("…") and len(got) <= 301
    assert extract_sample_paragraph("# 只有标题") == ""


def test_style_init_command_group_invocable_directly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令文件只写不注册：style 组可独立 CliRunner 调用（注册归集成轮）。"""
    from musespiral.commands.style_cmds import style as style_group

    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))
    root = tmp_path / "arts"
    root.mkdir()
    _final(root, "a.md", "published", "A 的开头段。")

    r = CliRunner().invoke(style_group, ["init", "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "已从 1 篇定稿抽取范文段" in r.output and "a.md" in r.output

    # 模板路径的命令输出
    home2 = tmp_path / "home2"
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home2))
    r2 = CliRunner().invoke(style_group, ["init", "--root", str(tmp_path / "empty-arts")])
    assert r2.exit_code == 0, r2.output
    assert "已生成模板" in r2.output
