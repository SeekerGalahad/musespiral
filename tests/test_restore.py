"""F43 编辑级版本回滚 — 版本池（滚动保留/命名/旧 .bak 兼容）+ restore 命令。

版本池 = 文件系统命名约定（<article>.md.bak.<YYYYMMDD-HHMMSS>，无索引
文件，glob 即真相）；三处写回（Studio 保存 / cover 写回 / title --apply）
共用 infra/storage/filesystem.backup_article 单一持有者。时钟经
monkeypatch filesystem._now 造不同秒的版本（确定性，不依赖真实睡眠）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

import musespiral.infra.storage.filesystem as fs
from musespiral.commands.core_cmds import title as title_cmd
from musespiral.commands.restore_cmds import restore as restore_cmd


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    """MUSESPIRAL_HOME 指向临时目录：title 命令的 AI 探测不触真实 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


def _clock(monkeypatch, base: str = "2026-11-14 09:00:00") -> dict:
    """把 filesystem._now 换成每次 +1s 的假时钟；返回状态 dict（测试可再拨）。"""
    state = {"now": datetime.fromisoformat(base)}

    def _tick():
        state["now"] += timedelta(seconds=1)
        return state["now"]

    monkeypatch.setattr(fs, "_now", _tick)
    return state


def _write_article(tmp_path: Path, body: str = "正文首句。") -> Path:
    p = tmp_path / "article.md"
    p.write_text(f"---\ntitle: 测试文章\n---\n\n{body}", encoding="utf-8")
    return p


def _pool_names(art: Path) -> list[str]:
    return [p.name for p in fs._pool_paths(art)]


# ── 版本池：滚动保留 / 命名 / MUSESPIRAL_BAK_KEEP ────────────────


def test_pool_rolling_7_keep_5(tmp_path: Path, monkeypatch) -> None:
    """造 7 版留 5：最旧 2 份删除，留存的是最近 5 份（含最后写入的内容）。"""
    _clock(monkeypatch)
    art = _write_article(tmp_path)
    for i in range(7):
        art.write_text(f"v{i}", encoding="utf-8")
        bak = fs.backup_article(art)
        assert bak.name.startswith("article.md.bak."), bak.name  # 时间戳命名
        assert bak.read_text(encoding="utf-8") == f"v{i}"

    pool = fs._pool_paths(art)
    assert len(pool) == 5  # 7 版滚动留 5
    assert [p.read_text(encoding="utf-8") for p in pool] == ["v2", "v3", "v4", "v5", "v6"]
    # 时间戳定长定序：文件名字典序 = 时间序（YYYYMMDD-HHMMSS）
    assert pool == sorted(pool, key=lambda p: p.name)


def test_pool_keep_env_override(tmp_path: Path, monkeypatch) -> None:
    """MUSESPIRAL_BAK_KEEP=2 → 只留 2 份；非法值回退默认 5。"""
    art = _write_article(tmp_path)
    assert fs.backup_keep_count() == 5  # 缺省

    monkeypatch.setenv("MUSESPIRAL_BAK_KEEP", "2")
    assert fs.backup_keep_count() == 2
    _clock(monkeypatch)
    for i in range(4):
        art.write_text(f"v{i}", encoding="utf-8")
        fs.backup_article(art)
    assert len(fs._pool_paths(art)) == 2
    assert [p.read_text(encoding="utf-8") for p in fs._pool_paths(art)] == ["v2", "v3"]

    monkeypatch.setenv("MUSESPIRAL_BAK_KEEP", "not-a-number")
    assert fs.backup_keep_count() == 5  # 非法回退默认
    monkeypatch.setenv("MUSESPIRAL_BAK_KEEP", "0")
    assert fs.backup_keep_count() == 5  # <1 回退默认（保底 1 份起）


# ── restore 命令：列表 / 恢复 / 恢复后入池 ──────────────────


def _craft_pool(art: Path, versions: list[str]) -> list[str]:
    """直接按命名约定造时间戳版本池（确定性，08 点段——晚于默认假时钟 09 点段
    造的入池序）；返回时间戳列表（旧→新）。"""
    stamps: list[str] = []
    for i, v in enumerate(versions):
        ts = f"20261114-08010{i}"
        stamps.append(ts)
        art.with_name(f"{art.name}.bak.{ts}").write_text(v, encoding="utf-8")
    return stamps


def test_restore_list_shows_time_and_summary(tmp_path: Path) -> None:
    """列表：新→旧逐条「时间 + 首行摘要」；空池给可读提示。"""
    art = _write_article(tmp_path, "当前版正文。")
    _craft_pool(art, ["v1 正文", "v2 正文"])
    art.with_name("article.md.bak").write_text("旧单版本内容", encoding="utf-8")

    r = CliRunner().invoke(restore_cmd, [str(art)])
    assert r.exit_code == 0, r.output
    assert "共 3 份" in r.output
    assert "2026-11-14 08:01:00  v1 正文" in r.output
    assert "2026-11-14 08:01:01  v2 正文" in r.output
    assert r.output.index("08:01:01") < r.output.index("08:01:00")  # 新→旧
    assert "旧单版本  旧单版本内容" in r.output  # 旧 .md.bak 恒排尾部
    assert "musespiral restore <article> --apply N" in r.output

    # 空池：另一篇无备份文章
    art2 = tmp_path / "b.md"
    art2.write_text("---\ntitle: B\n---\n\nb 正文", encoding="utf-8")
    r2 = CliRunner().invoke(restore_cmd, [str(art2)])
    assert r2.exit_code == 0, r2.output
    assert "版本池为空" in r2.output


def test_restore_apply_restores_and_pools_current(tmp_path: Path, monkeypatch) -> None:
    """--apply N：第 N 版内容原子覆盖原文；恢复前当前版先入池（可再恢复）。"""
    _clock(monkeypatch)
    art = _write_article(tmp_path, "当前版正文。")
    _craft_pool(art, ["v1 正文", "v2 正文"])

    r = CliRunner().invoke(restore_cmd, [str(art), "--apply", "1"])
    assert r.exit_code == 0, r.output
    assert "已恢复第 1 版" in r.output
    assert art.read_text(encoding="utf-8") == "v2 正文"  # 列表第 1 项 = 最新版 v2

    # 恢复前当前版（「当前版正文。」版）已入池且为最新份 → 撤销可撤销
    pool = fs._pool_paths(art)
    assert pool[-1].read_text(encoding="utf-8") == "---\ntitle: 测试文章\n---\n\n当前版正文。"
    r2 = CliRunner().invoke(restore_cmd, [str(art), "--apply", "1"])  # 新→旧第 1 项=刚入池的当前版
    assert r2.exit_code == 0, r2.output
    assert art.read_text(encoding="utf-8") == "---\ntitle: 测试文章\n---\n\n当前版正文。"


def test_restore_apply_out_of_range_and_empty(tmp_path: Path, monkeypatch) -> None:
    """--apply 超范围 / 空池 → 可读错误 + 非零退出，原文不动。"""
    art = _write_article(tmp_path)
    before = art.read_bytes()

    r = CliRunner().invoke(restore_cmd, [str(art), "--apply", "1"])
    assert r.exit_code == 1 and "版本池为空" in r.output

    _craft_pool(art, ["v1", "v2"])
    r2 = CliRunner().invoke(restore_cmd, [str(art), "--apply", "3"])
    assert r2.exit_code == 1 and "超出范围" in r2.output
    assert art.read_bytes() == before


def test_legacy_bak_restorable_and_write_rolls_new(tmp_path: Path, monkeypatch) -> None:
    """旧无时间戳 .md.bak：列表尾部「旧单版本」可恢复；下次写回按新命名滚动。"""
    _clock(monkeypatch)
    art = _write_article(tmp_path, "当前版正文。")
    legacy = art.with_name("article.md.bak")
    legacy.write_text("旧单版本内容", encoding="utf-8")

    entries = fs.list_backups(art)
    assert len(entries) == 1 and entries[0].ts is None  # 旧版恒排尾部（唯一项）

    r = CliRunner().invoke(restore_cmd, [str(art), "--apply", "1"])
    assert r.exit_code == 0, r.output
    assert art.read_text(encoding="utf-8") == "旧单版本内容"

    # 恢复已把「当前版正文。」入池（新命名）；旧 .md.bak 原样保留不代删
    pool = fs._pool_paths(art)
    assert len(pool) == 1 and pool[-1].read_text(encoding="utf-8").endswith("当前版正文。")
    assert legacy.read_text(encoding="utf-8") == "旧单版本内容"

    # 下次写回：继续按新命名滚动，旧 .md.bak 不参与保留计数
    fs.backup_article(art)
    assert len(fs._pool_paths(art)) == 2
    assert legacy.exists()


# ── 三处写回共用 helper（单一持有者）────────────────────────


def test_three_writebacks_share_single_helper() -> None:
    """三处写回 import 的是同一函数对象——版本池唯一持有者的结构性断言。"""
    import musespiral.commands.core_cmds as cc
    import musespiral.services.cover_service as cs
    import musespiral.studio.annotations as ann

    assert ann.backup_article is fs.backup_article
    assert cs.backup_article is fs.backup_article
    assert cc.backup_article is fs.backup_article


def test_studio_save_writes_pool(tmp_path: Path, monkeypatch) -> None:
    """Studio 保存（annotations._save_body）：旧版整文件字节入时间戳版本池。"""
    from musespiral.studio.annotations import _save_body

    _clock(monkeypatch)
    art = _write_article(tmp_path, "旧正文。")
    before = art.read_bytes()
    _save_body(art, "新正文。\n")
    pool = fs._pool_paths(art)
    assert len(pool) == 1
    assert pool[0].read_bytes() == before  # 池内容 = 写回前整文件字节
    assert art.read_text(encoding="utf-8").endswith("新正文。\n")
    assert not (tmp_path / "article.md.bak").exists()  # 不再落旧单版本名


def test_cover_writeback_writes_pool(tmp_path: Path, monkeypatch) -> None:
    """cover 写回（cover_service._write_back_cover）：frontmatter cover 写回前入池。"""
    from musespiral.services.cover_service import _write_back_cover

    _clock(monkeypatch)
    art = _write_article(tmp_path)
    before = art.read_bytes()
    _write_back_cover(art, "cover.png")
    pool = fs._pool_paths(art)
    assert len(pool) == 1 and pool[0].read_bytes() == before
    import frontmatter

    assert frontmatter.load(art).metadata["cover"] == "cover.png"


def test_title_apply_writes_pool(tmp_path: Path, monkeypatch) -> None:
    """title --apply：写回 frontmatter.title 前当前版入时间戳版本池。"""
    _clock(monkeypatch)
    art = _write_article(tmp_path, "# 指南\n\n正文首句。")
    before = art.read_bytes()

    r = CliRunner().invoke(title_cmd, [str(art), "--apply", "1"])
    assert r.exit_code == 0, r.output
    assert "已入版本池 article.md.bak." in r.output
    pool = fs._pool_paths(art)
    assert len(pool) == 1 and pool[0].read_bytes() == before
    assert not (tmp_path / "article.md.bak").exists()

    import frontmatter

    assert frontmatter.load(art).metadata["title"] != "测试文章"
