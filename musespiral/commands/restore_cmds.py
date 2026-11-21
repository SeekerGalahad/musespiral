"""编辑级版本回滚命令（第十六期 F43，裁决⑤）：musespiral restore。

只定义不注册——`musespiral restore` 挂入 cli 主组的注册与快照基线扩展统一归
集成阶段（两组并行禁碰 cli.py，与 plan_cmds 同款纪律）。

数据面全在 infra/storage/filesystem 的版本池（单一持有者：命名约定
<article>.md.bak.<YYYYMMDD-HHMMSS>、滚动保留 N、旧无时间戳 .md.bak 读侧
兼容）；本模块只做参数解析与用户提示。恢复 = 当前版先入池（恢复可再恢复）
+ 第 N 版原子覆盖原文，只写文件不登记。
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from ..infra.storage.filesystem import list_backups, restore_backup

# 首行摘要展示宽度（超出截断加省略号）
_SUMMARY_WIDTH = 60


def _first_line_summary(path: Path) -> str:
    """版本首行摘要：首个非空且非 frontmatter 界定符的行（截断展示）。

    只读展示（不落盘）：按字节读首个非空行再解码，非 UTF-8 行如实标
    「不可读」——绝不经 errors=replace 展示 U+FFFD 伪文本。
    """
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return "（读取失败）"
    for raw in head.splitlines():
        s = raw.strip()
        if not s or s == b"---":
            continue  # 空行与 frontmatter 界定符行不入摘要
        try:
            text = s.decode("utf-8")
        except UnicodeDecodeError:
            return "（首行非 UTF-8，不可读）"
        if len(text) > _SUMMARY_WIDTH:
            return text[:_SUMMARY_WIDTH] + "…"
        return text
    return "（空文件）"


def _format_ts(ts: str | None) -> str:
    """池时间戳 YYYYMMDD-HHMMSS → 展示形态；None = 旧单版本。"""
    if ts is None:
        return "旧单版本"
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option("--apply", "apply_n", default=None, type=int,
              help="恢复第 N 个版本（当前版先入版本池——恢复可再恢复）")
def restore(article: str, apply_n: int | None) -> None:
    """列出文章的版本池（F43）；--apply N 以第 N 版覆盖原文。"""
    path = Path(article)
    entries = list_backups(path)

    if apply_n is None:  # 列表面（新→旧；旧单版本恒排尾部）
        if not entries:
            click.echo(f"版本池为空（{path.name} 尚无写回备份）")
            return
        click.echo(f"{path.name} 版本池（新→旧，共 {len(entries)} 份）：")
        for i, e in enumerate(entries, 1):
            click.echo(f"  {i}. {_format_ts(e.ts)}  {_first_line_summary(e.path)}")
        click.echo("\n恢复某版: musespiral restore <article> --apply N")
        return

    if not entries:
        click.echo(f"❌ 版本池为空，无版本可恢复（{path.name}）", err=True)
        sys.exit(1)
    if apply_n < 1 or apply_n > len(entries):
        click.echo(f"❌ 只有 {len(entries)} 个版本，--apply {apply_n} 超出范围", err=True)
        sys.exit(1)
    entry = entries[apply_n - 1]
    restore_backup(path, entry)
    click.echo(
        f"✅ 已恢复第 {apply_n} 版（{_format_ts(entry.ts)}）；当前版已先入版本池，"
        "musespiral restore 可再恢复（撤销可撤销）"
    )
