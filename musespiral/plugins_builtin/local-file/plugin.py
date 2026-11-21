"""官方插件·本地媒体文件源（首插件族 #1，kind=source，正典 §9.1）。

流水线的第一站：把用户给的一个本地音视频路径**校验并接住**，交给下游
transcribe / frame-grab。四道校验门（需求 §插件一 3）：存在 → 常规文件 →
可读 → 后缀白名单——任一不过即 PluginError（可读失败，引擎记该节点 failed）。

**时长探测是尽力面不是硬依赖**（需求 §插件一 2「失败留空」）：duration_s
经 ffprobe 尽力探测，ffprobe 缺席/失败/输出不可解析一律留 None——source
节点不因一个辅助元数据失败而炸。requires 里的 ffprobe 只进 /check 依赖
报告（前端可见），不在执行路径上强制。

media_kind（可选输入）：运行级标注（video/audio/auto 之类自由文本），v1
仅留痕进日志（下游未消费，不发明语义）。

依赖纪律：本插件零第三方 Python 依赖（全部标准库）；ffprobe 是外部命令，
调用期 subprocess 探测，缺席不炸插件加载。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginError

# 媒体后缀白名单（需求 §插件一 3）：视频 5 + 音频 4；大小写不敏感（.MP4 合法）
ALLOWED_SUFFIXES: tuple[str, ...] = (
    ".mp4", ".mov", ".mkv", ".avi", ".webm",
    ".wav", ".mp3", ".m4a", ".flac",
)

_FFPROBE_TIMEOUT_S = 10


def _probe_duration(media: Path) -> float | None:
    """ffprobe 时长（秒）——best-effort：缺席/超时/非零退出/输出不可解析
    一律 None（失败留空），绝不让辅助探测炸掉 source 节点。"""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(media)],
            capture_output=True, timeout=_FFPROBE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return round(float(proc.stdout.decode("utf-8", "replace").strip()), 3)
    except ValueError:
        return None


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    raw = inputs.get("path")
    if raw is None or not str(raw).strip():
        raise PluginError("缺少输入 path（本地媒体文件路径）")
    path = Path(str(raw))
    ctx.log(f"local-file: 校验 {path}")

    if not path.exists():
        raise PluginError(f"文件不存在: {path}")
    if not path.is_file():
        raise PluginError(f"不是常规文件（目录/设备等）: {path}")
    if not os.access(path, os.R_OK):
        raise PluginError(f"文件不可读（权限不足）: {path}")
    suffix = path.suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise PluginError(
            f"后缀不在白名单 {''.join(ALLOWED_SUFFIXES)}: {suffix!r}"
            f"（支持视频 {''.join(ALLOWED_SUFFIXES[:5])} / 音频 {''.join(ALLOWED_SUFFIXES[5:])}）"
        )

    size = path.stat().st_size
    duration = _probe_duration(path)
    if duration is None:
        ctx.log("local-file: ffprobe 时长探测不可用，duration_s 留空（不失败）")
    media_kind = str(inputs.get("media_kind") or "").strip()
    ctx.log(f"local-file: 通过（{size} 字节，duration_s={duration}，media_kind={media_kind or '未标注'}）")
    ctx.progress(1, 1)
    # 返回 Path → 引擎存 path 引用（原路径不复制：媒体可能 GB 级，正典 §5.2）
    return {"file": path, "size": size, "duration_s": duration}


__all__ = ["ALLOWED_SUFFIXES", "run"]
