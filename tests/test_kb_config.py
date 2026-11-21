"""KB 源设置 config 层与 CLI 对等（第十八期 F46，M1 Group A）。

覆盖：
  - config 层读侧级联：无配置 → kb_root.txt → kb_sources.json（新文件优先；
    文件 root 为空回退旧文件读根）；坏 JSON 宽容降级；sources 形状归一；
  - knowledge_root()：三态优先级与既有 kb_root.txt 语义零迁移；
  - 校验器 validate_kb_root/validate_kb_source（绝对/存在/..上跳/越界）；
  - CLI 四命令：config kb list|add|remove|set-root（CliRunner + MUSESPIRAL_HOME 隔离；
    违规 exit code 1、存量源换根告警保留）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral import config
from musespiral.cli import cli


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def kb(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    (root / "13-source-analysis").mkdir(parents=True)
    (root / "11-learning-path").mkdir()
    (root / "13-source-analysis" / "模式家族").mkdir()
    return root


def _write_legacy(kb: Path) -> None:
    home = Path(config.home_dir())
    home.mkdir(parents=True, exist_ok=True)
    (home / config.KB_ROOT_FILE).write_text(str(kb), encoding="utf-8")


def _write_sources(root: str | None, sources: list[dict]) -> None:
    home = Path(config.home_dir())
    home.mkdir(parents=True, exist_ok=True)
    (home / config.KB_SOURCES_FILE).write_text(
        json.dumps({"root": root, "sources": sources}, ensure_ascii=False), encoding="utf-8"
    )


# ── config 层：读侧级联 ────────────────────────────────────


def test_kb_default_sources_table() -> None:
    """内置默认源表：与拆分前发现页硬编码三 chips 同集（key/label/relpath）。"""
    sources = config.kb_default_sources()
    assert [(s["key"], s["label"], s["relpath"]) for s in sources] == [
        ("13-source-analysis", "源码分析", "13-source-analysis"),
        ("11-learning-path", "学习路径", "11-learning-path"),
        ("pattern-family", "模式家族", "13-source-analysis/模式家族"),
    ]
    # 返回新 list：改副本不污染常量
    sources.clear()
    assert len(config.kb_default_sources()) == 3


def test_load_kb_sources_cascade_builtin_then_legacy_then_file(kb) -> None:
    # ① 零配置：builtin
    st = config.load_kb_sources()
    assert st == {"root": None, "sources": config.kb_default_sources(), "origin": "builtin"}
    assert config.knowledge_root() is None
    # ② 仅旧文件：root 生效 + 内置默认源表
    _write_legacy(kb)
    st = config.load_kb_sources()
    assert st["origin"] == config.KB_ROOT_FILE
    assert Path(st["root"]) == kb.resolve()
    assert st["sources"] == config.kb_default_sources()
    assert config.knowledge_root() == kb.resolve()
    # ③ 新文件：权威（root+sources 全取自文件）
    _write_sources(str(kb), [{"key": "k", "label": "标", "relpath": "13-source-analysis"}])
    st = config.load_kb_sources()
    assert st["origin"] == config.KB_SOURCES_FILE
    assert Path(st["root"]) == kb.resolve()
    assert [s["key"] for s in st["sources"]] == ["k"]
    assert config.knowledge_root() == kb.resolve()  # knowledge_root 优先新文件


def test_load_kb_sources_file_empty_root_falls_back_legacy(kb) -> None:
    """kb_sources.json 只写源清单（root null）→ 读根回退 kb_root.txt（中间态兼容）。"""
    _write_legacy(kb)
    _write_sources(None, [{"key": "k", "label": "标", "relpath": "13-source-analysis"}])
    st = config.load_kb_sources()
    assert st["origin"] == config.KB_SOURCES_FILE
    assert Path(st["root"]) == kb.resolve()
    assert config.knowledge_root() == kb.resolve()


def test_load_kb_sources_bad_json_treated_as_absent(kb) -> None:
    _write_legacy(kb)
    home = Path(config.home_dir())
    (home / config.KB_SOURCES_FILE).write_text("{not json", encoding="utf-8")
    st = config.load_kb_sources()
    assert st["origin"] == config.KB_ROOT_FILE  # 坏文件按缺席回退旧文件
    assert config.knowledge_root() == kb.resolve()


def test_save_kb_sources_normalizes_rows(kb) -> None:
    config.save_kb_sources(str(kb), [
        {"key": "ok", "label": "好", "relpath": "13-source-analysis"},
        {"key": "", "label": "空 key 丢弃", "relpath": "x"},      # 坏行
        {"key": "t", "label": "类型错", "relpath": 123},           # relpath 非 str → 丢弃
        "not-a-dict",                                              # 非对象 → 丢弃
    ])
    saved = json.loads(config.kb_sources_path().read_text(encoding="utf-8"))
    assert [s["key"] for s in saved["sources"]] == ["ok"]
    assert saved["root"] == str(kb)


# ── 校验器 ─────────────────────────────────────────────────


def test_validate_kb_root_rules(kb) -> None:
    assert config.validate_kb_root(str(kb)) == kb.resolve()
    for bad in ("kb", "docs/kb", str(kb.parent / "no-such")):
        with pytest.raises(ValueError):
            config.validate_kb_root(bad)


def test_validate_kb_source_rules(kb) -> None:
    assert config.validate_kb_source(kb, "13-source-analysis") == kb / "13-source-analysis"
    for bad in (
        str(kb),                              # 绝对路径
        "../kb",                              # .. 上跳（解析后等于根也算）
        "13-source-analysis/../../..",        # 逃逸
        "no-such-dir",                        # 目录缺失
        "",                                   # 空
    ):
        with pytest.raises(ValueError):
            config.validate_kb_source(kb, bad)


# ── CLI：config kb list|add|remove|set-root ────────────────


def _run(*args: str):
    return CliRunner().invoke(cli, ["config", "kb", *args], prog_name="musespiral")


def test_cli_kb_list_states(kb) -> None:
    r = _run("list")
    assert r.exit_code == 0
    assert "根: 未配置" in r.output
    assert "源码分析" in r.output and "模式家族" in r.output
    _write_legacy(kb)
    r = _run("list")
    assert "回退" in r.output  # origin=kb_root.txt 标注
    assert "✅" in r.output     # 存在源校验态


def test_cli_kb_set_root(kb, tmp_path) -> None:
    bad = _run("set-root", str(tmp_path / "no-such"))
    assert bad.exit_code == 1 and "不存在" in bad.output
    r = _run("set-root", str(kb))
    assert r.exit_code == 0
    assert json.loads(config.kb_sources_path().read_text(encoding="utf-8"))["root"] == str(kb.resolve())
    # 存量源（内置默认表）对新根全部可用 → 无告警
    assert "⚠️" not in r.output
    # 换根到空目录：内置源全部缺失 → 告警保留
    other = tmp_path / "other-kb"
    other.mkdir()
    r = _run("set-root", str(other))
    assert r.exit_code == 0 and r.output.count("⚠️") == 3


def test_cli_kb_add_remove(kb) -> None:
    _run("set-root", str(kb))
    # 缺失目录 → exit 1
    r = _run("add", "x", "标签", "no-such")
    assert r.exit_code == 1 and "不存在" in r.output
    # 合法添加
    r = _run("add", "learn", "学习", "11-learning-path")
    assert r.exit_code == 0
    saved = json.loads(config.kb_sources_path().read_text(encoding="utf-8"))
    assert {"key": "learn", "label": "学习", "relpath": "11-learning-path"} in saved["sources"]
    # key 重复 → exit 1
    r = _run("add", "learn", "再来", "11-learning-path")
    assert r.exit_code == 1 and "已存在" in r.output
    # remove
    r = _run("remove", "learn")
    assert r.exit_code == 0
    saved = json.loads(config.kb_sources_path().read_text(encoding="utf-8"))
    assert all(s["key"] != "learn" for s in saved["sources"])
    r = _run("remove", "learn")
    assert r.exit_code == 1 and "不存在" in r.output


def test_cli_kb_add_without_root_warns_and_saves(kb) -> None:
    """根未配置：跳过存在性校验（告警）仍可登记源——set-root 后生效。"""
    r = _run("add", "pre", "预备", "13-source-analysis")
    assert r.exit_code == 0 and "⚠️" in r.output
    saved = json.loads(config.kb_sources_path().read_text(encoding="utf-8"))
    assert saved["root"] is None
    assert {"key": "pre", "label": "预备", "relpath": "13-source-analysis"} in saved["sources"]
    # root null 读侧回退：knowledge_root 无旧文件 → None
    assert config.knowledge_root() is None
