"""文件系统共用件（正典 §2.3 infra/storage/filesystem.py）。

原子写（tmp + os.replace）与行尾字节保留——此前在 jobs/store.py 与
infra/wechat/analytics.py 各有一份逐字相同的私有副本（F17 字节级经验
的两处复制），Phase C 修复轮收编为单一实现共用。

F43 编辑级版本回滚（第十六期裁决⑤）的版本池唯一持有者：
  - 池 = 文件系统命名约定（<article>.md.bak.<YYYYMMDD-HHMMSS>，无索引
    文件，glob 即真相）；
  - 滚动保留最近 N 份（默认 5，MUSESPIRAL_BAK_KEEP 环境变量可调，非法回退 5）；
  - 三处写回（Studio 保存 / cover 写回 / title --apply）共用 backup_article；
  - 旧无时间戳 <article>.md.bak 读侧兼容（list_backups 尾部「旧单版本」），
    写侧不迁移——下次写回起按新命名滚动。

红线：临时文件写回失败必须清理（不留半写产物）；绝不 errors="replace"
读写（U+FFFD 不可逆替换会损坏非 UTF-8 字节行）。
"""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


def line_tail(line: bytes) -> bytes:
    """行尾字节（CRLF/LF/无）——更新行重写时保留原行尾风格。"""
    if line.endswith(b"\r\n"):
        return b"\r\n"
    if line.endswith(b"\n"):
        return b"\n"
    return b""


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """临时文件写回 + os.replace 原子替换（按字节）；失败清理临时文件。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── F43 编辑级版本池（正典 第十六期 §三；单一持有者）──

# 版本池文件名：原文档名 + ".bak." + 秒级时间戳（YYYYMMDD-HHMMSS）。
# 时间戳定长定序——文件名字典序即时间序，排序不依赖 mtime。
_BAK_TS_RE = re.compile(r"^\d{8}-\d{6}$")
# 旧单版本备份名（无时间戳，F43 之前三处写回的单版本覆盖形态）
_LEGACY_BAK_SUFFIX = ".bak"
_DEFAULT_BAK_KEEP = 5


@dataclass(frozen=True)
class BackupEntry:
    """版本池一个可恢复版本（glob 即真相，无索引文件）。"""

    path: Path
    ts: str | None  # "YYYYMMDD-HHMMSS"；None = 旧单版本 .md.bak（读侧兼容）


def _now() -> datetime:
    """版本池时间戳时钟（独立小函数：测试可 monkeypatch 造不同秒的版本）。"""
    return datetime.now()


def backup_keep_count() -> int:
    """滚动保留份数：MUSESPIRAL_BAK_KEEP 环境变量（≥1）；缺失/非法回退默认 5。"""
    raw = os.environ.get("MUSESPIRAL_BAK_KEEP", "").strip()
    if not raw:
        return _DEFAULT_BAK_KEEP
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_BAK_KEEP
    return n if n >= 1 else _DEFAULT_BAK_KEEP


def _pool_paths(path: Path) -> list[Path]:
    """时间戳版本池文件清单（旧→新，按文件名=时间戳序）；不含旧无时间戳 .md.bak。"""
    prefix = path.name + ".bak."
    hits: list[Path] = []
    try:
        entries = list(path.parent.iterdir())
    except OSError:
        return []
    for p in entries:
        if not p.name.startswith(prefix):
            continue
        if not _BAK_TS_RE.fullmatch(p.name[len(prefix):]):
            continue  # 非法时间戳后缀（非池成员）不入池、不误删
        if p.is_file():
            hits.append(p)
    hits.sort(key=lambda p: p.name)
    return hits


def backup_article(path: Path) -> Path:
    """当前版整文件字节入版本池 + 滚动保留 N；返回落盘的备份路径。

    命名 <name>.md.bak.<YYYYMMDD-HHMMSS>（秒级；同秒重复写回同名覆盖——
    单人工具可接受，与旧单版本语义一致）。超出保留份数的最旧份删除；
    旧无时间戳 .md.bak 不在池内（不计数、不删除，读侧兼容归 restore）。
    """
    ts = _now().strftime("%Y%m%d-%H%M%S")
    dst = path.with_name(f"{path.name}.bak.{ts}")
    dst.write_bytes(path.read_bytes())
    pool = _pool_paths(path)
    keep = backup_keep_count()
    for old in pool[: max(len(pool) - keep, 0)]:
        old.unlink(missing_ok=True)
    return dst


def list_backups(path: Path) -> list[BackupEntry]:
    """版本池清单（新→旧）；旧无时间戳 .md.bak 恒排尾部（「旧单版本」）。

    设计 §三-3：restore 列表尾部显示旧单版本可恢复；下次写回按新命名滚动
    （旧 .md.bak 由使用者随新版本落盘后自行清理，工具不代删）。
    """
    entries = [BackupEntry(p, p.name[len(path.name) + len(".bak."):]) for p in _pool_paths(path)]
    entries.reverse()  # 旧→新 → 新→旧（列表第 1 项最新）
    legacy = path.with_name(path.name + _LEGACY_BAK_SUFFIX)
    if legacy.is_file():
        entries.append(BackupEntry(legacy, None))
    return entries


def restore_backup(path: Path, entry: BackupEntry) -> Path:
    """恢复 entry 版本覆盖原文（F43：恢复本身先存版本——撤销可撤销）。

    先读定 entry 整文件字节，再 backup_article 把当前版入池（同秒时间戳
    撞名覆写也读的是恢复目标内容，不因入池顺序破坏），最后原子覆盖原文
    （tmp + chmod + os.replace，权限位随原文——与 _save_body 同款纪律）；
    entry 池内保留（池只增+滚动，恢复不消耗版本）。
    """
    data = entry.path.read_bytes()
    backup_article(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return entry.path
