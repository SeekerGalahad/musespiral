"""官方插件·本地媒体扫描源（kind=source，本地媒体线 2026-11-20）。

流水线之外独立的一站：在用户**显式指定**的 roots 白名单目录里发现音视频
文件，产出 markdown 清单表（路径|大小|时长|格式，大小降序）。与 local-file
的分工（设计 §2.1）：local-file 接单文件（四道门校验），本插件管「发现」
——目录遍历、隐私边界、批量清单三件独立的事，所以是新插件而非扩参。
v1 契约不动（file/path 是单值）：只产**清单**，用户从清单点选一个进
local-file——「先看再选」的克制，不做批量接线。

🔴 安全边界（本插件的核心约束，扫描器最危险的失败是「卡死」和「越界」）：
  1. 只接受 roots 显式白名单；空 roots 与 `/` 根直接 PluginError——绝不
     变成全盘扫描器；
  2. 硬编码排除目录（实测含大量无关/隐私媒体）：node_modules、.git、
     Library/Caches、Library/Application Support、Library/Containers
     （微信/QQ 媒体目录）。排除按**绝对路径分量**匹配——root 落在排除区
     之内（如 roots=~/Library 时 Containers 仍是双分量可见）或正好是排除
     区本身（如 root=微信容器目录）都拦得住：前者遍历中逐层排除，后者
     整树跳过并留说明。这是隐私红线（复核判例 2026-11-20：相对分量匹配
     会让 roots=~/Library 无提示带出微信私人媒体），不提供显式点名豁免；
     敏感 root（命中 Library/Containers 或 Documents）另有隐私警告说明
     为何扫不出东西；
  3. 符号链接一律不跟随（防环）；权限拒绝静默跳过并计数（不崩）；
  4. 遍历硬限额 MAX_ENTRIES=50000 条目，超限立即停止并在清单里说明
     （防全盘扫描卡死）；
  5. pattern 只能在白名单后缀内**收窄**（.txt 等直接可读拒绝）——pattern
     是过滤器不是后门。

时长探测（ffprobe）是尽力面：只对**发现的**文件做、线程并发 ≤4、单文件
3s 超时——大目录逐个串行 ffprobe 会卡死（实测 repository/ 数千文件）。
缺席/超时/非零退出/输出不可解析一律留 None，不炸扫描（同 local-file
「失败留空」纪律）。requires 里的 ffprobe 只进 /check 依赖报告。

依赖纪律：零第三方 Python 依赖（全部标准库）。
"""
from __future__ import annotations

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginCancelled, PluginError

# ── 后缀白名单（与 local-file 完全一致：视频 5 + 音频 4，大小写不敏感）──
ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".wav", ".mp3", ".m4a", ".flac",
)
DEFAULT_PATTERN = "mp4,mov,mkv,avi,webm,wav,mp3,m4a,flac"

# ── 遍历与探测限额 ──────────────────────────────────────────────────
MAX_ENTRIES = 200000       # 目录+命中媒体文件限额（不含无关文件）。2026-11-20 真机判例：
# 原 50000 对 repository/ 这类数千子目录的巨型仓偏紧（depth=10 时 40836 已近顶），
# 提到 200000——无关文件不计数后，真正耗时的是目录遍历与 ffprobe，条数不是瓶颈。
MAX_DEPTH_DEFAULT = 3      # 默认下探层数（root 下第 N 层目录）
MIN_SIZE_MB_DEFAULT = 1.0  # 默认大小下限（滤缩略图/碎音频）
MAX_WORKERS = 4            # ffprobe 并发上限
PROBE_TIMEOUT_S = 3        # 单文件 ffprobe 超时（秒）

# ── 硬编码排除目录（按**绝对路径**分量序列匹配，任意深度都拦得住——
#    含 root 自身（整树跳过）与 root 之下（逐层排除）两种位形；不用相对
#    分量：root=~/Library 时 Containers 的相对分量只剩单分量，双片段
#    ("Library","Containers") 永不匹配 → 隐私绕过，复核判例 2026-11-20）──
EXCLUDED_DIR_FRAGMENTS: tuple[tuple[str, ...], ...] = (
    ("node_modules",),
    (".git",),
    ("Library", "Caches"),
    ("Library", "Application Support"),
    ("Library", "Containers"),
)

# 敏感目录（root 命中时 ctx.log 隐私警告——扫但提醒，用户知情）
SENSITIVE_DIR_FRAGMENTS: tuple[tuple[str, ...], ...] = (
    ("Library", "Containers"),
    ("Documents",),
)


def _fragments_in(parts: tuple[str, ...], frag: tuple[str, ...]) -> bool:
    """frag 是否作为连续分量序列出现在 parts 的任意位置。"""
    m = len(frag)
    return any(parts[i:i + m] == frag for i in range(len(parts) - m + 1))


def _parse_roots(raw: Any) -> list[Path]:
    """roots 归一 → 已展开的目录路径列表。三道门：非空 / 不是 / 根 / 存在。

    接受逗号分隔字符串或 JSON 数组字符串（设计 §2.1 两形态）；每项做
    expanduser（~/ 支持常见）+ 绝对化。空 roots、`/` 根、不存在或非目录
    一律 PluginError——扫描器的入口校验必须可读且从严，静默跳过会掩盖
    拼写错误。
    """
    if raw is None or not str(raw).strip():
        raise PluginError("缺少输入 roots（逗号分隔的目录白名单）")
    text = str(raw).strip()
    if text.startswith("["):
        try:
            items = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PluginError(f"roots JSON 数组解析失败: {exc}") from exc
        if not isinstance(items, list):
            raise PluginError("roots JSON 须为数组")
    else:
        items = text.split(",")
    roots: list[Path] = []
    for seg in items:
        seg = seg.strip()
        if not str(seg).strip():
            raise PluginError("roots 含空项（检查逗号/空串）")
        p = Path(os.path.abspath(os.path.expanduser(str(seg.strip()))))
        if p == p.parent:  # 唯一 parent 是自身的就是 / 根
            raise PluginError(f"拒绝扫描根目录 {p}——本插件只接受显式子目录白名单，不做全盘扫描")
        if not p.is_dir():
            raise PluginError(f"root 不存在或不是目录: {p}")
        roots.append(p)
    if not roots:
        raise PluginError("roots 归一后为空")
    return roots


def _parse_pattern(raw: Any) -> tuple[str, ...]:
    """pattern（逗号分隔后缀）归一 → 白名单内的后缀元组。

    容忍 `*.mp4` / `.mp4` / `MP4` 三种写法（去 * 去 . 小写）；**只能在
    ALLOWED_SUFFIXES 内收窄**，出白名单的项直接 PluginError（pattern 是
    过滤器不是后门——静默忽略会得到与用户预期不符的扫描面）。空/缺省 →
    全量 9 种。
    """
    if raw is None or not str(raw).strip():
        return tuple(ALLOWED_SUFFIXES)
    wanted: list[str] = []
    for seg in str(raw).split(","):
        seg = seg.strip().lstrip("*").lower()
        if not seg:
            continue
        suffix = seg if seg.startswith(".") else f".{seg}"
        if suffix not in ALLOWED_SUFFIXES:
            raise PluginError(
                f"pattern 后缀不在白名单 {''.join(ALLOWED_SUFFIXES)}: {seg!r}"
                "（pattern 只能收窄，不能扩大扫描面）"
            )
        if suffix not in wanted:
            wanted.append(suffix)
    if not wanted:
        raise PluginError("pattern 归一后为空（检查逗号/写法）")
    return tuple(wanted)


def scan(
    roots: list[Path],
    max_depth: int,
    min_size_mb: float,
    suffixes: tuple[str, ...],
    ctx: Any,
) -> dict[str, Any]:
    """遍历发现 + 并发探测 → 结构化结果（run 与探针脚本共用）。

    返回 {"files": [{path,size_bytes,duration_s,format}...按大小降序],
    "total_size_bytes", "entries_visited", "truncated", "denied_dirs",
    "probed_ok"}。
    """
    min_bytes = max(0.0, float(min_size_mb)) * 1024 * 1024
    root_parts = {r: r.parts for r in roots}
    for r in roots:  # 隐私警告：root 命中敏感目录段（扫但提醒，用户知情）
        if any(_fragments_in(root_parts[r], f) for f in SENSITIVE_DIR_FRAGMENTS):
            ctx.log(f"local-scan: ⚠️ 隐私提示——root {r} 位于敏感目录"
                    f"（Library/Containers 或 Documents 等可能含微信/QQ 私人媒体），请确认扫描意图")

    found: list[dict[str, Any]] = []
    entries = 0
    truncated = False
    denied = 0

    for root in roots:
        # 隐私红线第一道：root 自身落在排除区内（如 root=微信容器目录、
        # root=某仓的 node_modules）→ 整树跳过并留说明。绝对分量匹配对
        # root 也生效，显式点名不豁免（设计 §六「硬编码排除」，2026-11-20
        # 复核判例：相对匹配可被 root 选择静默绕过）。
        if any(_fragments_in(root.parts, f) for f in EXCLUDED_DIR_FRAGMENTS):
            ctx.log(f"local-scan: root {root} 位于硬编码排除目录区"
                    f"（node_modules/.git/Library Caches|Application Support|Containers），整树跳过")
            continue
        # 层语义：root 本身层 0，root/<a> 层 1……层 ≤ max_depth 的目录才下探。
        # 排除匹配用条目的**绝对分量**（plugin.py EXCLUDED_DIR_FRAGMENTS 注释
        # 判例）——root=~/Library 时其下 Containers/Caches 仍带双分量可见，
        # 逐层排除照常生效。
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            directory, level = stack.pop()
            try:
                it = os.scandir(directory)
            except OSError:
                denied += 1  # 权限拒绝静默跳过并计数（不崩）
                continue
            with it:
                for entry in it:
                    # **配额只算「目录 + 命中的媒体文件」**，不算无关文件
                    # （2026-11-20 真机判例：原实现对每个 dirent 都计数，
                    # repository/ 下数千个 .py/.md/.json 几秒内吃满 50000 配额，
                    # 结果 count=0 一个媒体文件都没找到——限额反而酿成漏扫）。
                    # 无关文件既不耗时也不占内存（scandir 流式），没有限量的必要。
                    if ctx.cancelled:
                        raise PluginCancelled("local-scan: 用户取消")
                    if entry.is_symlink():
                        continue  # 符号链接一律不跟随（防环）
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            entries += 1
                            if entries > MAX_ENTRIES:
                                truncated = True
                                stack.clear()  # 立即停止全部遍历（防全盘扫描卡死）
                                break
                            if level < max_depth and not any(
                                _fragments_in(Path(entry.path).parts, f)
                                for f in EXCLUDED_DIR_FRAGMENTS
                            ):
                                stack.append((Path(entry.path), level + 1))
                        elif entry.is_file(follow_symlinks=False):
                            if entry.name.lower().endswith(suffixes):
                                size = entry.stat(follow_symlinks=False).st_size
                                if size >= min_bytes:
                                    # 配额检查先于入列（与上面目录分支同形：越限的
                                    # 第 MAX_ENTRIES+1 条不进结果——「恰扫满即停」，
                                    # 硬要求 13 判例：先 append 再查会带出超限条）
                                    entries += 1
                                    if entries > MAX_ENTRIES:
                                        truncated = True
                                        stack.clear()
                                        break
                                    found.append({"path": entry.path, "size_bytes": size})
                    except OSError:
                        denied += 1  # stat/is_dir 失败同样静默计数

    found.sort(key=lambda f: (-f["size_bytes"], f["path"]))  # 大文件=长视频优先

    # ffprobe 只对发现的文件做；并发 ≤4、单文件 3s——大目录串行会卡死
    durations = _probe_many([Path(f["path"]) for f in found], ctx)
    probed_ok = 0
    for f, dur in zip(found, durations):
        f["duration_s"] = dur
        if dur is not None:
            probed_ok += 1

    return {
        "files": found,
        "total_size_bytes": sum(f["size_bytes"] for f in found),
        "entries_visited": entries,
        "truncated": truncated,
        "denied_dirs": denied,
        "probed_ok": probed_ok,
    }


def _probe_many(files: list[Path], ctx: Any) -> list[float | None]:
    """并发 ffprobe（≤4 线程、单文件 3s 超时）→ 与输入同序的时长列表。

    dict 按 key 插入序迭代（=文件序），result() 按序收果回填下标。
    """
    if not files:
        return []
    durations: list[float | None] = [None] * len(files)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(files))) as pool:
        futures = {pool.submit(_probe_one, f): i for i, f in enumerate(files)}
        for done, fut in enumerate(futures, 1):
            durations[futures[fut]] = fut.result()
            if done % 20 == 0 or done == len(files):
                ctx.progress(done, len(files))
    return durations


def _probe_one(media: Path) -> float | None:
    """单文件 ffprobe 时长（秒）+ 容器格式尽力探测；任何失败 → (None, None)。

    与 local-file 同纪律：探测是辅助面，失败留空绝不炸节点。单文件硬
    超时 3s（损坏/网络挂载文件会卡住 ffprobe，3s 封顶）。
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(media)],
            capture_output=True, timeout=PROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return round(float(proc.stdout.decode("utf-8", "replace").strip()), 3)
    except ValueError:
        return None


def _fmt_size(n: float) -> str:
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{int(n)} B"


def _md_table(files: list[dict[str, Any]]) -> str:
    """清单表：| # | 路径 | 大小 | 时长 | 格式 |（已按大小降序）。"""
    rows = ["| # | 路径 | 大小 | 时长 | 格式 |", "|---|---|---|---|---|"]
    for i, f in enumerate(files, 1):
        dur = f["duration_s"]
        rows.append(
            f"| {i} | `{f['path']}` | {_fmt_size(f['size_bytes'])} | "
            f"{f'{dur:.1f} s' if dur is not None else '未知'} | {f['format']} |"
        )
    return "\n".join(rows)


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    roots = _parse_roots(inputs.get("roots"))
    max_depth = int(inputs.get("max_depth") or MAX_DEPTH_DEFAULT)
    if max_depth < 1:
        raise PluginError(f"max_depth 须 ≥1: {max_depth}")
    min_size_mb = float(inputs.get("min_size_mb") if inputs.get("min_size_mb") is not None
                        else MIN_SIZE_MB_DEFAULT)
    suffixes = _parse_pattern(inputs.get("pattern"))
    ctx.log(f"local-scan: roots={[str(r) for r in roots]} max_depth={max_depth} "
            f"min_size_mb={min_size_mb} pattern={'+'.join(suffixes)}")

    result = scan(roots, max_depth, min_size_mb, suffixes, ctx)
    files = [{**f, "format": Path(f["path"]).suffix.lstrip(".").lower()} for f in result["files"]]
    ctx.log(
        f"local-scan: 命中 {len(files)} 个（共 {_fmt_size(result['total_size_bytes'])}），"
        f"ffprobe 成功 {result['probed_ok']}/{len(files)}，"
        f"遍历 {result['entries_visited']} 条目"
        + ("（⚠️ 超过 50000 上限提前停止，结果不完整）" if result["truncated"] else "")
        + (f"，权限拒绝 {result['denied_dirs']} 个目录已跳过" if result["denied_dirs"] else "")
    )
    ctx.progress(1, 1)

    table = _md_table(files)
    summary_lines = [
        f"- roots: {', '.join(f'`{r}`' for r in roots)}",
        f"- 过滤: max_depth={max_depth}，min_size_mb={min_size_mb}，"
        f"后缀={'+'.join(s.lstrip('.') for s in suffixes)}",
        f"- 命中 {len(files)} 个文件，合计 {_fmt_size(result['total_size_bytes'])}",
        f"- ffprobe 时长探测成功 {result['probed_ok']}/{len(files)}",
        f"- 遍历条目 {result['entries_visited']}"
        + ("，**超过 50000 上限提前停止——结果不完整，请收窄 roots 或调低 max_depth**"
           if result["truncated"] else ""),
    ]
    if result["denied_dirs"]:
        summary_lines.append(f"- 权限拒绝静默跳过的目录数: {result['denied_dirs']}")
    manifest_md = "# 本地媒体扫描清单\n\n" + "\n".join(summary_lines) + "\n\n" + table

    # v1 契约三端口：files=清单表、count=命中数、manifest=带摘要的完整清单
    # （file/path 是单值，批量接线留 v2——用户从清单点选一个进 local-file）
    return {"files": table, "count": len(files), "manifest": manifest_md}


__all__ = ["ALLOWED_SUFFIXES", "DEFAULT_PATTERN", "MAX_ENTRIES", "scan", "run"]
