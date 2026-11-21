"""title（F32 标题多候选优选）单元测试——AI 路径全程 monkeypatch，不真实联网。"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from musespiral.commands.core_cmds import title as title_cmd
from musespiral.core.quality import title
from musespiral.infra.ai import client as ai_mod


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _write_article(tmp_path, body: str, meta: str = "title: 测试文章\ntags: [AI, 写作]\n"):
    p = tmp_path / "article.md"
    p.write_text(f"---\n{meta}---\n\n{body}", encoding="utf-8")
    return p


def _setup_ai_home(tmp_path) -> None:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "ai.json").write_text(
        json.dumps({"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"}),
        encoding="utf-8",
    )


# ── 启发式（无 AI）──


def test_heuristic_candidates_without_ai(tmp_path, monkeypatch) -> None:
    """无 ai.json → 启发式 ≥2 条（H1 + 首句钩子 + tags 重组），来源逐条标注。"""
    p = _write_article(
        tmp_path, "# 缓存实战指南\n\n缓存是提升系统性能的重要手段。\n\n第二段正文。"
    )
    items = title.candidates(p)
    assert len(items) >= 2
    sources = {c.source for c in items}
    assert "启发式·H1" in sources
    assert "启发式·首句钩子" in sources
    assert "启发式·tags重组" in sources
    by_src = {c.source: c for c in items}
    assert by_src["启发式·H1"].text == "缓存实战指南"
    assert by_src["启发式·首句钩子"].text == "缓存是提升系统性能的重要手段。"
    assert by_src["启发式·tags重组"].text == "测试文章｜AI·写作"


def test_heuristic_hook_truncated_to_default(tmp_path) -> None:
    """无 --platform 时首句钩子按内部默认 30 字截断。"""
    long_sentence = "这是一句特别长的首句" * 5 + "。"
    p = _write_article(tmp_path, f"{long_sentence}\n\n第二段。", meta="title: 无标签文\n")
    items = title.candidates(p)
    hook = next(c for c in items if c.source == "启发式·首句钩子")
    assert len(hook.text) <= 30 and hook.text.endswith("…")


def test_empty_article_returns_empty(tmp_path) -> None:
    """frontmatter 无 title、正文为空 → 空列表（命令层报『正文为空』）。"""
    p = tmp_path / "empty.md"
    p.write_text("---\ncover: c.png\n---\n\n", encoding="utf-8")
    assert title.candidates(p) == []


# ── --platform 字限过滤（PLIMITS 唯一事实源，不新造字限）──


def test_platform_xhs_filters_over_limit(tmp_path) -> None:
    """--platform xhs：超 20 字的候选直接不产出，产出的全部 ≤20 字。"""
    p = _write_article(
        tmp_path,
        "# 一个非常非常非常非常非常长的超长标题肯定超过二十个字了\n\n"
        "缓存是提升系统性能的重要手段。\n\n第二段。",
    )
    items = title.candidates(p, platform="xhs")
    assert items, "短句钩子与 tags 重组应仍有合规候选"
    assert all(len(c.text) <= 20 for c in items)
    assert all("超长标题" not in c.text for c in items), "超限 H1 不得产出"


def test_platform_xhs_hook_truncated_to_limit(tmp_path) -> None:
    """首句钩子按平台字限截断生成合规变体（≤20 字）。"""
    p = _write_article(
        tmp_path,
        "缓存是提升系统性能的重要手段而且效果立竿见影反复验证有效。\n\n第二段。",
        meta="title: 无标签文\n",
    )
    items = title.candidates(p, platform="xhs")
    hook = next(c for c in items if c.source == "启发式·首句钩子")
    assert len(hook.text) <= 20


def test_platform_mp_uses_byte_limit(tmp_path) -> None:
    """mp 走 title_max_bytes 字节口径（既有换算惯例，与 checks 标题检查同源）。"""
    items = title.candidates(_write_article(tmp_path, "# 短标题\n\n正文首句。"), platform="mp")
    assert all(len(c.text.encode("utf-8")) <= 64 for c in items)
    limit = title.title_limit("mp")
    assert limit == {"max_bytes": 64}
    assert "字节" in title.describe_limit("mp")


def test_platform_f42_juejin_bilibili_filters(tmp_path) -> None:
    """F42 --platform juejin(100)/bilibili(80)：超限候选不产出、合规候选全过
    （PLIMITS 联动，不新造字限表）。"""
    long_h1 = "超长标题占位" * 20  # 120 字，同时超 juejin 100 与 bilibili 80
    p = _write_article(
        tmp_path,
        f"# {long_h1}\n\n缓存是提升系统性能的重要手段。\n\n第二段。",
    )
    for platform, limit in (("juejin", 100), ("bilibili", 80)):
        items = title.candidates(p, platform=platform)
        assert items, f"{platform} 短句钩子应仍有合规候选"
        assert all(len(c.text) <= limit for c in items), platform
        assert all("超长标题占位" not in c.text for c in items), f"{platform} 超限 H1 不得产出"
        assert title.title_limit(platform) == {"max_chars": limit, "min_chars": None}
        assert f"≤{limit} 字" in title.describe_limit(platform)


def test_platform_unknown_raises(tmp_path) -> None:
    with pytest.raises(ValueError, match="未知平台"):
        title.candidates(_write_article(tmp_path, "正文。"), platform="nope")


# ── AI 3 候选（mock，懒导入 infra.ai）──


def test_ai_three_candidates_first(tmp_path, monkeypatch) -> None:
    """AI 已配置 → 3 条 AI 候选在前，启发式随后，task 走 title 白名单。"""
    _setup_ai_home(tmp_path)
    import musespiral.core.quality.title as t

    monkeypatch.setattr(t, "_ai_chat", lambda fm, c, p: '["直陈式标题", "数字式标题", "悬念式标题"]')
    items = title.candidates(_write_article(tmp_path, "# 指南\n\n正文首句。"))
    assert [c.text for c in items[:3]] == ["直陈式标题", "数字式标题", "悬念式标题"]
    assert all(c.source == "AI" for c in items[:3])
    assert any(c.source.startswith("启发式") for c in items)


def test_ai_failure_silently_degrades(tmp_path, monkeypatch) -> None:
    """AI 已配置但调用失败（_ai_chat 捕获 AIError 后返回空串）→ 静默降级为纯启发式。"""
    _setup_ai_home(tmp_path)
    import musespiral.core.quality.title as t

    monkeypatch.setattr(t, "_ai_chat", lambda fm, c, p: "")
    items = title.candidates(_write_article(tmp_path, "# 指南\n\n正文首句。"))
    assert items and all(c.source.startswith("启发式") for c in items)


# ── AI 真实降级（零 mock：端点真不可达，覆盖 _ai_chat→chat() 真路径）──


def test_ai_real_unreachable_endpoint_degrades(tmp_path) -> None:
    """真实降级路径零 mock（P13 复核/总账 T2）：ai.json v2 档案指向不可达端点
    （127.0.0.1:9 本机闭端口，连接秒级被拒）→ chat() 真实发起 POST 失败抛
    AIError → _ai_chat 捕获返回空串 → 纯启发式候选、exit 0、无栈。"""
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "ai.json").write_text(
        json.dumps(
            {
                "default": "dead",
                "profiles": {
                    "dead": {"base_url": "http://127.0.0.1:9/v1", "api_key": "k", "model": "m"}
                },
            }
        ),
        encoding="utf-8",
    )
    p = _write_article(tmp_path, "# 指南\n\n正文首句。")
    r = CliRunner().invoke(title_cmd, [str(p)])
    assert r.exit_code == 0, r.output
    assert r.exception is None, "降级不得抛栈"
    assert "条标题候选" in r.output
    assert "启发式" in r.output
    # 候选行格式「N. 文本（N字，来源）」——AI 候选行必然以「，AI）」收尾；
    # 不能断言 "AI" 不在全文（tags 重组候选可能合法含字面 AI，如标签 [AI, 写作]）
    assert "，AI）" not in r.output, "端点不可达时不得产出 AI 候选"


# ── 命令层：--apply 写回 + 版本池 ──


def test_cli_apply_writes_back_with_bak(tmp_path) -> None:
    p = _write_article(tmp_path, "# 指南\n\n正文首句。")
    runner = CliRunner()
    r = runner.invoke(title_cmd, [str(p)])
    assert r.exit_code == 0, r.output
    assert "条标题候选" in r.output
    assert "字" in r.output and "启发式" in r.output  # 逐条标字数与来源

    before = p.read_text(encoding="utf-8")
    r2 = runner.invoke(title_cmd, [str(p), "--apply", "1"])
    assert r2.exit_code == 0, r2.output
    # F43 版本池：写回前先落 <name>.md.bak.<时间戳>（滚动保留，Studio 保存同语义）
    pool = sorted(p.parent.glob(f"{p.name}.bak.*"))
    assert len(pool) == 1, "写回前必须先入时间戳版本池"
    bak = pool[0]
    assert bak.read_text(encoding="utf-8") == before, "池内版本必须是写回前的完整旧版字节"

    import frontmatter

    after = frontmatter.load(p)
    assert after.metadata["title"] != "测试文章"
    # 精确断言：写回值 = 写回前候选清单第 1 条
    items = title.candidates(bak)
    assert after.metadata["title"] == items[0].text


def test_cli_apply_out_of_range(tmp_path) -> None:
    p = _write_article(tmp_path, "# 指南\n\n正文首句。")
    r = CliRunner().invoke(title_cmd, [str(p), "--apply", "99"])
    assert r.exit_code == 1
    assert "超出范围" in r.output


def test_cli_platform_flag_in_header(tmp_path) -> None:
    p = _write_article(tmp_path, "# 指南\n\n正文首句。")
    r = CliRunner().invoke(title_cmd, [str(p), "--platform", "xhs"])
    assert r.exit_code == 0, r.output
    assert "--platform xhs" in r.output and "≤20 字" in r.output


def test_cli_unknown_platform_errors(tmp_path) -> None:
    p = _write_article(tmp_path, "# 指南\n\n正文首句。")
    r = CliRunner().invoke(title_cmd, [str(p), "--platform", "nope"])
    assert r.exit_code == 1
    assert "未知平台" in r.output


def test_tasks_whitelist_contains_title() -> None:
    """TASKS 白名单含 title（ai route Choice 单一事实源自动跟上）。"""
    from musespiral.infra.ai.profiles import TASKS

    assert "title" in TASKS


def test_platform_mp_long_hook_byte_truncated_keeps_candidates(tmp_path) -> None:
    """C3：mp 长首句钩子按字节限截断生成合规变体，而非超限整条丢弃。

    30 字中文首句 = 90 字节 > mp 64 字节限：旧口径 _trim(30字) 产物过不了
    fits 的字节判定被整条滤掉，无 AI 时候选可能 <2；修复后钩子按 64 字节
    截断（字符边界切，省略号计 3 字节）且仍在候选清单。
    """
    p = _write_article(
        tmp_path,
        "缓存优化是提升系统吞吐与响应速度最直接有效的工程手段没有之一。\n\n第二段。",
        meta="title: 无标签文\n",
    )
    items = title.candidates(p, platform="mp")
    hooks = [c for c in items if c.source == "启发式·首句钩子"]
    assert hooks, "mp 长首句钩子被整条丢弃（C3 回归）"
    assert len(hooks[0].text.encode("utf-8")) <= 64  # 截断产物过字节限
    assert hooks[0].text.endswith("…")
    assert len(items) >= 2  # 无 AI 候选 ≥2（正典 §二 F32 保底语义）
