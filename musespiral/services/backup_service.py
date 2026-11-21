"""文章库备份服务（F35 独立先行切片——差距分析 §四/§六热修批）。

一条命令把 articles root 与 ~/.musespiral 打成带时间戳 tar.gz 至备份目录，
--keep 轮转保留最近 N 份。纯 stdlib（tarfile/datetime/pathlib）。

- 排除 cookies/（README 纪律：登录态明文不随包走，失效重扫码即可；
  其余 ~/.musespiral 文件含凭证——敏感，备份目录请放本地/私有盘）
- 任一源缺失不报错（空文章库/全新安装也可备份——首份即全量基线）
- tar 内两顶层目录 articles/ 与 musespiral-home/（互不冲突、恢复时各取所需）
"""
from __future__ import annotations

import tarfile
from datetime import datetime
from pathlib import Path

from .. import config
from .discover_service import DEFAULT_ARTICLES_ROOT

DEFAULT_BACKUP_DIR = Path.home() / "musespiral-backups"
_PREFIX = "musespiral-backup-"
_ARTICLES_ARC = "articles"
_HOME_ARC = "musespiral-home"
# ~/.musespiral 排除项（顶层名）：README 纪律——登录态不随备份走
_HOME_EXCLUDE = {"cookies"}


def _home_filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """tar.add filter：跳过 musespiral-home/cookies/** 并归一属主（恢复不绑 uid）。"""
    parts = Path(member.name).parts
    if len(parts) > 1 and parts[0] == _HOME_ARC and parts[1] in _HOME_EXCLUDE:
        return None
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    return member


def _rotate(out_dir: Path, keep: int) -> list[Path]:
    """删除最旧的超额备份，返回被删路径列表（测试可断言）。"""
    backups = sorted(p for p in out_dir.glob(f"{_PREFIX}*.tar.gz") if p.is_file())
    doomed = backups[:-keep] if len(backups) > keep else []
    for p in doomed:
        p.unlink()
    return doomed


def create_backup(
    output_dir: str | Path | None = None,
    keep: int = 5,
    articles_root: str | Path | None = None,
    musespiral_home: str | Path | None = None,
) -> Path:
    """打包 articles root + ~/.musespiral（除 cookies/）→ tar.gz，轮转后返回路径。

    - 文件名 musespiral-backup-YYYYMMDD-HHMMSS.tar.gz：同秒重跑覆盖同名（幂等
      不叠份），跨秒各成一份
    - keep < 1 按 1 计（永远至少留一份——刚写的那份绝不自删）
    - articles_root 缺省 = discover_service.DEFAULT_ARTICLES_ROOT（单一
      事实源）；musespiral_home 缺省 = config.home_dir()（随 MUSESPIRAL_HOME 解析，
      测试 monkeypatch env 即隔离）
    """
    out_dir = Path(output_dir).expanduser() if output_dir is not None else DEFAULT_BACKUP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    arts = Path(articles_root).expanduser() if articles_root is not None else DEFAULT_ARTICLES_ROOT
    home = Path(musespiral_home).expanduser() if musespiral_home is not None else config.home_dir()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = out_dir / f"{_PREFIX}{stamp}.tar.gz"
    with tarfile.open(dst, "w:gz") as tar:
        if arts.is_dir():
            tar.add(arts, arcname=_ARTICLES_ARC)
        if home.is_dir():
            tar.add(home, arcname=_HOME_ARC, filter=_home_filter)
    _rotate(out_dir, max(1, keep))
    return dst
