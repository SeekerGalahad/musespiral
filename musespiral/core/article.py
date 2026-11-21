"""Article 状态机与产物回链（R3，重构正典 §四-R3）。

状态机写入文章 frontmatter.status（旧文章缺 status 读作 draft，不回写）。
迁移只有两类触发（正典 §三-2 红线「状态迁移不自动化」）：
  - 人工显式操作（CLI `musespiral status --set` / Studio 按钮——本模块只提供原子原语）
  - publish 成功自动置 published（mark_published，且仅 reviewed 可入）
check/adapt 不改状态。

迁移矩阵：draft→reviewed、reviewed→published、任意→draft（人工重置）、
draft/published→archived（人工归档）；其余组合拒绝并给可读错误。

回链：adapt.generate 产物 frontmatter 写 source（源文件名）与 source_sha1
（源正文哈希 body_sha1 前 12 位）；探测端与写入端共用同一算法——源文章改名
后仍可经 source 回链命中，源正文变更可经 sha1 漂移识别。
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from pathlib import Path

import frontmatter

DRAFT = "draft"
REVIEWED = "reviewed"
PUBLISHED = "published"
ARCHIVED = "archived"

# 状态集（frontmatter.status 合法取值）
STATUSES: tuple[str, ...] = (DRAFT, REVIEWED, PUBLISHED, ARCHIVED)

# 人工可经 CLI --set 迁移的目标（published 只由 publish 成功自动置位）
MANUAL_SETTABLE: tuple[str, ...] = (REVIEWED, DRAFT, ARCHIVED)

# frontmatter 顶层 status 键行（带缩进的是嵌套键，不属于文章状态，不碰）
_FM_STATUS_RE = re.compile(r"^status[ \t]*:")

# frontmatter 界定符行（--- / ...，容许行尾空白——与 python-frontmatter 的
# FM_BOUNDARY 正则同口径；opening 行带尾空白时它仍按 frontmatter 解析）
_FM_DELIM_RE = re.compile(r"^(?:---|\.\.\.)[ \t]*$")


def allowed_targets(status: str) -> list[str]:
    """状态的全部合法后继（§四-R3 迁移矩阵的读侧视图）。

    draft→draft 属「任意→draft」的幂等重置（合法，set_status 内不落盘）；
    archived 与未知历史值（脏数据）只有一条出路：人工重置回 draft。
    """
    if status == DRAFT:
        return [DRAFT, REVIEWED, ARCHIVED]
    if status == REVIEWED:
        return [DRAFT, PUBLISHED]
    if status == PUBLISHED:
        return [DRAFT, ARCHIVED]
    return [DRAFT]


def read_status(path: str | Path) -> str:
    """读文章状态；frontmatter 缺 status（或整篇无 frontmatter）读作 draft，不回写。

    值原样返回（含未知值）——校验只发生在写入侧（set_status），读取侧不猜。
    """
    post = frontmatter.load(Path(path))
    return str(post.metadata.get("status") or DRAFT).strip() or DRAFT


def set_status(path: str | Path, new: str, *, expect: str | None = None) -> str:
    """迁移文章状态并原子回写 frontmatter.status，返回迁移前状态。

    - expect：乐观并发护栏——与实际当前状态不符即拒绝（publish 侧固定
      expect=reviewed，防止建草稿期间被改成 archived 后仍被置 published）。
    - 重写仅触碰 status 键所在行：正文与其余 frontmatter 键字节原样保留
      （inline 列表/注释/键序/CRLF 都不动；digest --apply 的 frontmatter 整体
      dump 会重排键丢注释，满足不了本约束，故只沿用其「load→改一键→写回」
      流程，落盘改为行级改写）。
    - 合法但无变化的目标（draft→draft）：不落盘（与「缺 status 读作 draft
      不回写」同一哲学）。
    """
    if new not in STATUSES:
        raise ValueError(f"未知状态: {new!r}（合法: {'/'.join(STATUSES)}）")
    p = Path(path)
    cur = read_status(p)
    if expect is not None and cur != expect:
        raise ValueError(
            f"状态与预期不符: 期望 {expect}，实际 {cur}（可能被并发改动，请重读后重试）"
        )
    if new not in allowed_targets(cur):
        raise ValueError(
            f"非法状态迁移: {cur} → {new}。{cur} 可迁移到: {'、'.join(allowed_targets(cur))}"
            "（合法迁移: draft→reviewed、reviewed→published、任意→draft、draft/published→archived）"
        )
    if cur == new:
        return cur
    _rewrite_status_line(p, new)
    return cur


def mark_published(path: str | Path) -> None:
    """publish 成功后置 published（§四-R3：仅 reviewed 可自动入 published）。

    非 reviewed 一律拒绝——发布链路不许跳过人审闸门，错误提示引导先人工置
    reviewed；调用方（CLI/Studio publish）捕获后降级为警告，不阻塞发布成功提示。
    """
    cur = read_status(path)
    if cur != REVIEWED:
        raise ValueError(
            f"published 只能从 reviewed 迁移，当前为 {cur}；"
            "请先人工标记已审: musespiral status <article> --set reviewed"
        )
    set_status(path, PUBLISHED, expect=REVIEWED)


def body_sha1(content: str) -> str:
    """回链哈希：源正文 sha1 前 12 位（strip 两端空白后计算）。

    adapt 产物 source_sha1 的写入端与 Studio variants 探测端共用此函数
    （R3 回链契约）——算法/归一化改动必须两侧同步。
    """
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:12]


# ---- 行级改写（唯一写路径）----


def _rewrite_status_line(p: Path, new: str) -> None:
    """仅改写 status 键所在行（缺失则在 frontmatter 块末插入一行），原子落盘。

    读与写均禁用换行翻译（newline=""），CRLF 文件按原字节往返；同目录临时
    文件写完再 os.replace，中途崩溃不留半写文件。
    """
    with open(p, "r", encoding="utf-8", newline="") as f:
        lines = f.read().splitlines(keepends=True)
    payload = "".join(_plan_status_rewrite(lines, new))
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(payload)
        os.chmod(tmp, stat.S_IMODE(p.stat().st_mode))
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _plan_status_rewrite(lines: list[str], new: str) -> list[str]:
    """计算改写后的行序列（纯函数）：定位 frontmatter 块与 status 行。

    与 python-frontmatter 的读取口径对齐：首行非界定符、或有开头无闭合
    （此时 frontmatter.load 读得空 metadata、全文皆正文），都按「无
    frontmatter」处理——文件头补最小 status 块，原字节整体下移为正文。
    """

    def body(line: str) -> str:
        return line.rstrip("\r\n")

    def ending(line: str) -> str:
        return line[len(body(line)):] or "\n"  # 末行无换行符时按 \n 收口

    close = _fm_close_index(lines)
    if close is None:  # 无 frontmatter：头补最小块（分隔符行取原文行尾风格）
        delim_ending = ending(lines[0]) if lines else "\n"
        return ["---" + delim_ending, f"status: {new}\n", "---" + delim_ending, "\n"] + lines
    for i in range(1, close):
        if _FM_STATUS_RE.match(body(lines[i])):
            lines[i] = f"status: {new}" + ending(lines[i])
            return lines
    # 无 status 键：闭合界定符前插入（其余键序不动；行尾风格随闭合行）
    lines.insert(close, f"status: {new}" + ending(lines[close]))
    return lines


def _fm_close_index(lines: list[str]) -> int | None:
    """frontmatter 块闭合界定符行号；非 frontmatter 文件返回 None。"""
    if not lines or not _FM_DELIM_RE.match(lines[0].rstrip("\r\n")):
        return None
    for i in range(1, len(lines)):
        if _FM_DELIM_RE.match(lines[i].rstrip("\r\n")):
            return i
    return None  # 有 --- 无闭合：python-frontmatter 读作无 frontmatter，同口径处理
