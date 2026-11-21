"""plan / log add 命令集成测试（第十四期 F36/F36c，正典 §五 CliRunner）。

命令对象直接 invoke（cli.py 注册与快照基线归集成阶段）。覆盖：log add
manual 行落盘 + article 存在校验 + 平台码校验 + 标题覆盖；plan 立即可见；
plan --week 非法用法错误。全部走 tmp 双隔离。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.plan_cmds import log as log_group
from musespiral.commands.plan_cmds import plan
from musespiral.services import plan_service


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    home = tmp_path / "musespiral-home"
    root = tmp_path / "arts"
    root.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setattr(plan_service, "DEFAULT_ARTICLES_ROOT", root)
    (root / "c.md").write_text(
        "---\ntitle: 终发篇\ncover: cover.png\n---\n正文。\n", encoding="utf-8"
    )
    return {"home": home, "root": root}


def _rows(env: dict[str, Path]) -> list[dict]:
    return [
        json.loads(x)
        for x in (env["home"] / "publish_log.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]


def test_log_add_writes_manual_row_and_plan_sees_it(env: dict[str, Path]) -> None:
    """登记落盘 manual:true 行（title 缺省读 frontmatter）→ plan 立即可见。"""
    result = CliRunner().invoke(
        log_group, ["add", "c.md", "--platform", "mp", "--url", "https://mp.weixin.qq.com/s/x"]
    )
    assert result.exit_code == 0, result.output
    assert "已登记发表: mp 终发篇（c.md）" in result.output

    rows = _rows(env)
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "publish" and r["manual"] is True
    assert r["platform"] == "mp" and r["article"] == "c.md"
    assert r["title"] == "终发篇" and r["url"] == "https://mp.weixin.qq.com/s/x"
    assert r["ts"]  # ts=now 由服务层生成

    # plan 立即可见：ts=now 落今天，必在当前周 → 「已发布」归位
    seen = CliRunner().invoke(plan, [])
    assert seen.exit_code == 0, seen.output
    assert "已发布: mp 终发篇" in seen.output
    assert "本周 计划 0 / 已交付 0 / 已发布 1" in seen.output


def test_log_add_title_override_and_no_url_shape(env: dict[str, Path]) -> None:
    """--title 覆盖生效；无 --url 时行内不出现 url 键。"""
    result = CliRunner().invoke(
        log_group, ["add", "c.md", "--platform", "xhs", "--title", "手改标题"]
    )
    assert result.exit_code == 0, result.output
    r = _rows(env)[0]
    assert r["title"] == "手改标题" and r["platform"] == "xhs"
    assert "url" not in r


def test_log_add_unknown_article_fails_without_write(env: dict[str, Path]) -> None:
    """article 须存在于 root：不存在 → 可读错误退出非零，不落任何行。"""
    result = CliRunner().invoke(log_group, ["add", "ghost.md", "--platform", "mp"])
    assert result.exit_code == 1
    assert "不存在" in result.output
    assert not (env["home"] / "publish_log.jsonl").exists()


def test_log_add_unknown_platform_rejected(env: dict[str, Path]) -> None:
    """--platform 白名单（registry ∪ adapt 变体码）外的值 → 用法错误。"""
    result = CliRunner().invoke(log_group, ["add", "c.md", "--platform", "nope"])
    assert result.exit_code == 2
    assert "未知平台代码" in result.output
    assert not (env["home"] / "publish_log.jsonl").exists()


def test_plan_bad_week_usage_error(env: dict[str, Path]) -> None:
    """非法周序 → BadParameter（exit 2），不当堆栈。"""
    result = CliRunner().invoke(plan, ["--week", "2026-W54"])
    assert result.exit_code == 2
    assert "周序越界" in result.output
    bad_fmt = CliRunner().invoke(plan, ["--week", "2026w47"])
    assert bad_fmt.exit_code == 2 and "YYYY-Www" in bad_fmt.output  # 提示正确格式


def test_plan_renders_current_week(env: dict[str, Path]) -> None:
    """空数据面：正常渲染周历头与汇总行（读侧宽容零事件不报错）。"""
    result = CliRunner().invoke(plan, [])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("周历 2")
    assert "本周 计划 0 / 已交付 0 / 已发布 0" in result.output
