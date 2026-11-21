"""CLI design 命令组测试（commands/visual_cmds.py）——CliRunner 快照只增不删。

覆盖：list-templates 四内置；render/save/export 全链；infographic 抽数打印；
preset save/list/show 双入口一致；--help 输出快照。
全部走 tmp_path + MUSESPIRAL_HOME 隔离；无浏览器时导出降 C 路（route 注记）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image

from musespiral.cli import cli


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    return home


@pytest.fixture
def article(tmp_path: Path) -> Path:
    path = tmp_path / "demo.md"
    path.write_text(
        "---\ntitle: 缓存优化三步走\n---\n\n首段钩子。\n\n## 甲\n内容\n\n## 乙\n内容\n",
        encoding="utf-8",
    )
    return path


# ── list-templates ───────────────────────────────────────────


def test_design_list_templates_four_builtins() -> None:
    runner = CliRunner()
    res = runner.invoke(cli, ["design", "list-templates"])
    assert res.exit_code == 0
    for tid in ("vermilion-slab", "indigo-slab", "xhs-card", "points-three"):
        assert tid in res.output


def test_design_list_templates_shows_specs() -> None:
    res = CliRunner().invoke(cli, ["design", "list-templates"])
    assert "xhs-3x4" in res.output  # specs 列


# ── render ──────────────────────────────────────────────────


def test_design_render_stdout(article: Path) -> None:
    """render 缺省打印 SVG 到 stdout（含 viewBox）。"""
    res = CliRunner().invoke(
        cli, ["design", "render", "--article", str(article),
              "--template", "vermilion-slab", "--spec", "mp"],
    )
    assert res.exit_code == 0
    assert "<svg" in res.output
    assert 'viewBox="0 0 900 383"' in res.output


def test_design_render_to_file(article: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.svg"
    res = CliRunner().invoke(
        cli, ["design", "render", "--article", str(article),
              "--template", "vermilion-slab", "--spec", "mp", "-o", str(out)],
    )
    assert res.exit_code == 0
    assert out.exists() and "<svg" in out.read_text(encoding="utf-8")


def test_design_render_unknown_template_fails(article: Path) -> None:
    res = CliRunner().invoke(
        cli, ["design", "render", "--article", str(article), "--template", "nope", "--spec", "mp"],
    )
    assert res.exit_code != 0
    assert "未知模板" in res.output


def test_design_render_bad_params_json(article: Path) -> None:
    res = CliRunner().invoke(
        cli, ["design", "render", "--article", str(article), "--template", "vermilion-slab",
              "--spec", "mp", "--params", "not json"],
    )
    assert res.exit_code != 0
    assert "JSON" in res.output


# ── save ────────────────────────────────────────────────────


def test_design_save_creates_design_dir(article: Path) -> None:
    res = CliRunner().invoke(
        cli, ["design", "save", str(article), "--kind", "cover", "--template", "vermilion-slab",
              "--spec", "mp", "--params", '{"hook":"钩子"}'],
    )
    assert res.exit_code == 0
    design_dir = article.parent / "demo.design"
    assert (design_dir / "design.json").exists()
    assert (design_dir / "cover.svg").exists()
    design = json.loads((design_dir / "design.json").read_text(encoding="utf-8"))
    assert design["template"] == "vermilion-slab"
    assert design["kind"] == "cover"


def test_design_save_invalid_kind(article: Path) -> None:
    res = CliRunner().invoke(
        cli, ["design", "save", str(article), "--kind", "nope", "--template", "vermilion-slab"],
    )
    assert res.exit_code != 0


# ── export ──────────────────────────────────────────────────


def test_design_export_cover_mp(article: Path, tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli, ["design", "save", str(article), "--kind", "cover",
                       "--template", "vermilion-slab", "--spec", "mp"])
    res = runner.invoke(cli, ["design", "export", str(article), "--kind", "cover"])
    assert res.exit_code == 0
    assert "cover.png" in res.output
    assert "900×383" in res.output
    assert "降 C 路" in res.output  # 无浏览器注记
    assert Image.open(article.parent / "cover.png").size == (900, 383)


def test_design_export_social_batch_naming(article: Path) -> None:
    """social 导出命名递增（<stem>.social-<n>.png）。"""
    runner = CliRunner()
    runner.invoke(cli, ["design", "save", str(article), "--kind", "social",
                       "--template", "xhs-card", "--spec", "xhs-3x4",
                       "--params", '{"title":"社媒标题"}'])
    res = runner.invoke(cli, ["design", "export", str(article), "--kind", "social"])
    assert res.exit_code == 0
    assert "social-1.png" in res.output
    assert Image.open(article.parent / "demo.social-1.png").size == (1242, 1656)


def test_design_export_without_design_fails(article: Path) -> None:
    res = CliRunner().invoke(cli, ["design", "export", str(article), "--kind", "cover"])
    assert res.exit_code != 0
    assert "先存稿" in res.output or "无设计稿" in res.output


# ── infographic ─────────────────────────────────────────────


def test_design_infographic_points(article: Path) -> None:
    res = CliRunner().invoke(
        cli, ["design", "infographic", str(article), "--source", "points"],
    )
    assert res.exit_code == 0
    assert "抽数完成" in res.output
    assert "甲" in res.output and "乙" in res.output


def test_design_infographic_radar(article: Path) -> None:
    """radar 抽数：空素材 → 0 行有效（空态）。"""
    res = CliRunner().invoke(
        cli, ["design", "infographic", str(article), "--source", "radar"],
    )
    assert res.exit_code == 0
    assert "抽数完成" in res.output


# ── preset ──────────────────────────────────────────────────


def test_design_preset_save_and_list() -> None:
    runner = CliRunner()
    save = runner.invoke(cli, ["design", "preset", "save", "--id", "my-cover-1",
                              "--template", "vermilion-slab",
                              "--params", '{"font_size":"L"}'])
    assert save.exit_code == 0
    lst = runner.invoke(cli, ["design", "preset", "list"])
    assert lst.exit_code == 0
    assert "my-cover-1" in lst.output


def test_design_preset_invalid_id() -> None:
    res = CliRunner().invoke(cli, ["design", "preset", "save", "--id", "我的模板 1",
                                  "--template", "vermilion-slab"])
    assert res.exit_code != 0
    assert "kebab" in res.output


def test_design_preset_show_pure_json() -> None:
    """show 输出纯 JSON（可 json.load 解析；无代码痕迹）。"""
    runner = CliRunner()
    runner.invoke(cli, ["design", "preset", "save", "--id", "p-1",
                       "--template", "vermilion-slab", "--params", '{"font_size":"S"}'])
    show = runner.invoke(cli, ["design", "preset", "show", "p-1"])
    assert show.exit_code == 0
    data = json.loads(show.output)
    assert data["id"] == "p-1"
    assert "lambda" not in show.output.lower()


# ── --help 快照（只增不删：本组自身快照）─────────────────


def test_design_help_lists_subcommands() -> None:
    res = CliRunner().invoke(cli, ["design", "--help"])
    assert res.exit_code == 0
    for sub in ("render", "save", "export", "infographic", "preset", "list-templates"):
        assert sub in res.output


def test_design_preset_help() -> None:
    res = CliRunner().invoke(cli, ["design", "preset", "--help"])
    assert res.exit_code == 0
    assert "save" in res.output and "list" in res.output


def test_cover_help_includes_from_design_route() -> None:
    """既有 cover --help 快照只增不删：新增 from-design 路线在帮助中可见。"""
    res = CliRunner().invoke(cli, ["cover", "--help"])
    assert res.exit_code == 0
    assert "from-design" in res.output or "from_design" in res.output
