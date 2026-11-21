"""F35 文章库备份（backup_service / musespiral backup）——独立先行切片。

覆盖：tar 内容面（articles + musespiral-home、cookies 排除）/ 轮转 / 源缺失
容错 / CLI 出口。全部走 tmp（MUSESPIRAL_HOME 隔离 + DEFAULT_ARTICLES_ROOT
monkeypatch），不触真实 ~/musespiral-articles 与 ~/.musespiral。
"""
from __future__ import annotations

import json
import tarfile
from pathlib import Path

from click.testing import CliRunner

from musespiral.cli import cli
from musespiral.services import backup_service


def _setup(tmp_path: Path, monkeypatch) -> dict:
    """隔离环境：tmp 文章根（1 篇文章）+ tmp ~/.musespiral（含 cookies/）。"""
    arts = tmp_path / "articles"
    arts.mkdir()
    (arts / "第一篇.md").write_text("---\ntitle: 第一篇\n---\n\n正文\n", encoding="utf-8")
    home = tmp_path / "musespiral-home"
    (home / "cookies").mkdir(parents=True)
    (home / "ai.json").write_text(json.dumps({"default": "x"}), encoding="utf-8")
    (home / "cookies" / "xhs.txt").write_text("LOGIN-STATE", encoding="utf-8")
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setattr(backup_service, "DEFAULT_ARTICLES_ROOT", arts)
    return {"arts": arts, "home": home, "out": tmp_path / "backups"}


def test_backup_contains_articles_and_home_excludes_cookies(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    dst = backup_service.create_backup(output_dir=env["out"], keep=5)
    assert dst.exists() and dst.parent == env["out"]
    with tarfile.open(dst) as tar:
        names = tar.getnames()
    assert "articles/第一篇.md" in names
    assert "musespiral-home/ai.json" in names
    # README 纪律：登录态不随备份走（目录与其下文件都不得出现）
    assert not any(n == "musespiral-home/cookies" or n.startswith("musespiral-home/cookies/") for n in names)


def test_backup_rotation_keeps_newest(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    out = env["out"]
    out.mkdir(parents=True)
    # 预置 4 份旧备份（时间戳递增）→ 共 5 份，keep=3 → 删最旧 2 份
    for i in range(4):
        (out / f"musespiral-backup-2020010{i + 1}-000000.tar.gz").write_bytes(b"old")
    dst = backup_service.create_backup(output_dir=out, keep=3)
    remaining = sorted(p.name for p in out.glob("musespiral-backup-*.tar.gz"))
    assert len(remaining) == 3
    assert "musespiral-backup-20200101-000000.tar.gz" not in remaining
    assert "musespiral-backup-20200102-000000.tar.gz" not in remaining
    assert dst.name in remaining  # 刚写的这份必留（keep 再小也不自删）


def test_backup_keep_floor_is_one(tmp_path, monkeypatch):
    """keep<1 按 1 计——刚写的这份绝不自删。"""
    env = _setup(tmp_path, monkeypatch)
    out = env["out"]
    out.mkdir(parents=True)
    (out / "musespiral-backup-20200101-000000.tar.gz").write_bytes(b"old")
    dst = backup_service.create_backup(output_dir=out, keep=0)
    assert dst.exists()
    assert [p.name for p in out.glob("musespiral-backup-*.tar.gz")] == [dst.name]


def test_backup_missing_sources_ok(tmp_path, monkeypatch):
    """源全缺失不报错——首份即全量基线（空 tar 也成立）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "no-home"))
    monkeypatch.setattr(backup_service, "DEFAULT_ARTICLES_ROOT", tmp_path / "no-arts")
    dst = backup_service.create_backup(output_dir=tmp_path / "bk", keep=5)
    assert dst.exists()


def test_backup_cli(tmp_path, monkeypatch):
    env = _setup(tmp_path, monkeypatch)
    runner = CliRunner()
    result = runner.invoke(cli, ["backup", "--output", str(env["out"]), "--keep", "2"])
    assert result.exit_code == 0, result.output
    assert "备份完成" in result.output
    assert len(list(env["out"].glob("musespiral-backup-*.tar.gz"))) == 1
