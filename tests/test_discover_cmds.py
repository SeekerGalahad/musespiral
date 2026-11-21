"""CLI discover 命令组注册冒烟（第十二期 F26-F29）。

--help 路径不触达 services/discover_service（命令函数体内延迟导入），服务
缺席也必须全绿；scan/suggest/draft 的行为链路由 test_discovery_*（域层
单测）与集成阶段合验覆盖。快照级基线在 test_cli_snapshot.py。
"""
from __future__ import annotations

from click.testing import CliRunner
import pytest

from musespiral.cli import cli


def test_discover_registered_in_root_help() -> None:
    """musespiral --help 命令清单含 discover（注册接线冒烟）。"""
    result = CliRunner().invoke(cli, ["--help"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert "discover" in result.output


def test_discover_group_lists_three_subcommands() -> None:
    """discover 组挂三个子命令：scan / suggest / draft。"""
    result = CliRunner().invoke(cli, ["discover", "--help"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    for name in ("scan", "suggest", "draft"):
        assert name in result.output, name


def test_discover_scan_help_options() -> None:
    """scan 选项齐全：--source / --limit / --min-score / --all（--all 默认过滤 drafted）。"""
    result = CliRunner().invoke(cli, ["discover", "scan", "--help"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    for opt in ("--source", "--limit", "--min-score", "--all"):
        assert opt in result.output, opt


def test_discover_draft_help_options() -> None:
    """draft 参数：PATH + --series + -o/--out。"""
    result = CliRunner().invoke(cli, ["discover", "draft", "--help"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    for opt in ("--series", "-o", "--out"):
        assert opt in result.output, opt


def test_discover_suggest_help_has_ai_flag() -> None:
    """suggest 可选 AI 增强：--ai 旗标存在。"""
    result = CliRunner().invoke(cli, ["discover", "suggest", "--help"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert "--ai" in result.output


def test_discover_suggest_ai_routes_through_domain(monkeypatch, tmp_path) -> None:
    """--ai 增强必须走 domain 层 suggester.ai_enhance（不自带第二套编排）。

    复核修复项：CLI 曾内联自己的 chat 调用与「标题=…\\n导语=…」解析，与
    ai_enhance 的两行解析/ai_title-ai_intro 字段语义分叉。本测试 monkeypatch
    两处，断言 --ai 时 ai_enhance 收到候选列表、其写入的 ai_title/ai_intro
    被展示层消费；并断言无 --ai 时 ai_enhance 零调用。
    """
    pytest.importorskip("musespiral.services.discover_service", reason="Group A 服务未落地")
    from musespiral.commands import discover_cmds
    from musespiral.core.discovery import suggester
    from musespiral.services import discover_service

    doc = tmp_path / "源码分析-demo.md"
    doc.write_text(
        "---\ntitle: 源码分析-demo\ncategory: 13-source-analysis\n"
        "created: 2026-11-01\ntags: [源码分析, Python, 数据库, demo/demo]\n---\n"
        "# 源码分析：demo\n## 一、项目概述\n内容。\n",
        encoding="utf-8",
    )
    fake_items = [{"path": str(doc), "title": "源码分析-demo", "series": "开源项目深度解读"}]
    monkeypatch.setattr(
        discover_service, "scan_candidates", lambda **kw: [dict(fake_items[0])], raising=True
    )

    calls: list[dict] = []

    def fake_enhance(items, top_n=5, chat=None):
        calls.append({"items": items, "top_n": top_n})
        items[0]["ai_title"] = "AI 标题 X"
        items[0]["ai_intro"] = "AI 导语 Y"
        return 1

    monkeypatch.setattr(suggester, "ai_enhance", fake_enhance, raising=True)

    result = CliRunner().invoke(cli, ["discover", "suggest", "--source", str(tmp_path), "--ai"], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert len(calls) == 1, "--ai 应恰好调用一次 ai_enhance"
    assert calls[0]["top_n"] == 5, "F28：对 top-5 增强"
    assert calls[0]["items"][0]["path"] == str(doc)
    assert "AI 标题 X" in result.output and "AI 导语 Y" in result.output

    calls.clear()
    result = CliRunner().invoke(cli, ["discover", "suggest", "--source", str(tmp_path)], prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert calls == [], "无 --ai 不触 AI 编排"
    assert "AI 标题" not in result.output

    # 全部增强失败（ai_enhance 返回 0）→ 可见降级警告（不静默）
    def zero_enhance(items, top_n=5, chat=None):
        calls.append({"items": items, "top_n": top_n})
        return 0

    monkeypatch.setattr(suggester, "ai_enhance", zero_enhance, raising=True)
    result = CliRunner().invoke(
        cli, ["discover", "suggest", "--source", str(tmp_path), "--ai"], prog_name="musespiral"
    )
    assert result.exit_code == 0, result.output
    assert "AI 增强未生效" in result.output
