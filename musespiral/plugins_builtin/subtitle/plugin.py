"""subtitle：字幕文件 → segments（跳过语音识别，与 transcribe 输出同构）。

**为什么这个插件价值极高**（格式生态分析 2026-11-20 §3.5）：
教学视频若带人工校对过的字幕（.srt/.vtt），解析它比跑 ASR **快约 100 倍**，
且**准确率为 100%**（人工字幕没有「Attention→Accention」这类同音字误识）。
输出结构与 transcribe 完全一致（segments = [{start,end,text,avg_logprob,no_speech_prob}]），
下游 key-segment / frame-grab / article-writer **零改动**即可消费。

**两条路径的分工**：
  subtitle ──segments──┐
                       ├─→ key-segment → frame-grab → article-writer → material-sink
  transcribe ─segments─┘
两者是**同一位置的可互换生产者**——有字幕走 subtitle，没字幕走 transcribe。

**质量信号的处理（关键设计）**：transcribe 的每段带 avg_logprob/no_speech_prob
（ASR 置信度）。字幕是人写的，**没有「识别置信度」这个概念**——因此本插件写入
1.0 / 0.0（= 完全可信 / 确定是语音），语义是「人工校对过，无需低置信告警」。
这不是造假：两个字段的含义是「ASR 层面对这段的把握」，人工字幕天然是满分。
下游的低置信告警逻辑因此不会对字幕链产生误报。

**支持格式**：
  - .srt（SubRip，最常见；支持 <i>/<b>/<font> 标记剥除、多行文本合并、BOM）
  - .vtt（WebVTT；支持 cue settings 剥离、NOTE/STYLE 块跳过）
不支持的格式（.ass/.ssa 等富样式字幕）→ 可读错误并指明原因，不静默半解析。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class SubtitleError(Exception):
    """字幕解析失败（可读消息；引擎记该节点 failed）。"""


class _Cancelled(Exception):
    """取消信号（与引擎 PluginCancelled 同语义，本模块不依赖上游类型）。"""


# 支持的扩展名（小写，含点）
_ALLOWED_SUFFIXES: tuple[str, ...] = (".srt", ".vtt")

# <i>/<b>/<u>/<font ...> ... </font> 等标记（字幕常见；剥除后才是纯文本）
_TAG_RE = re.compile(r"<[^>]+>")

# SRT 时间轴：00:00:01,234 --> 00:00:04,567（逗号或点都接受；小时可缺省）
_SRT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{1,3})"
)

# VTT 时间轴额外允许 cue settings（align:start position:10% 等）在 --> 之后
_VTT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\.(\d{3})\s*-->\s*"
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\.(\d{3})"
)


def _to_seconds(h: str | None, m: str, s: str, frac: str) -> float:
    """时间分量 → 秒。frac 按位数归一（毫秒 3 位 / 厘秒 2 位 / 分秒 1 位）。"""
    hours = int(h) if h else 0
    frac_val = int(frac) / (10 ** len(frac))
    return hours * 3600 + int(m) * 60 + int(s) + frac_val


def _clean_text(raw: str, *, strip_tags: bool) -> str:
    """块内多行合并为一行（字幕换行是排版不是语义），可选剥标记。"""
    text = raw.strip()
    if strip_tags:
        text = _TAG_RE.sub("", text)
    # 多行 → 单行（中文用空串接、日文/英文保持空格更安全，这里统一空格再压）
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    joined = " ".join(lines)
    # 压缩连续空白（保留单个空格）
    return re.sub(r"\s+", " ", joined).strip()


def _parse_srt(text: str, *, strip_tags: bool) -> list[dict[str, Any]]:
    """SubRip：序号行 + 时间行 + 文本块（块间空行分隔）。"""
    segments: list[dict[str, Any]] = []
    # 统一换行 + 去 BOM
    body = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    for block in re.split(r"\n\s*\n", body):
        lines = [ln for ln in block.split("\n")]
        if not lines:
            continue
        time_idx = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                time_idx = i
                break
        if time_idx is None:
            continue  # 无时间行的块（纯序号残留/说明行）跳过
        m = _SRT_TIME_RE.search(lines[time_idx])
        if not m:
            continue
        g = m.groups()
        start = _to_seconds(g[0], g[1], g[2], g[3])
        end = _to_seconds(g[4], g[5], g[6], g[7])
        if end <= start:
            continue  # 零长/倒序段跳过（坏字幕常见）
        raw_text = "\n".join(lines[time_idx + 1:])
        txt = _clean_text(raw_text, strip_tags=strip_tags)
        if not txt:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": txt})
    return segments


def _parse_vtt(text: str, *, strip_tags: bool) -> list[dict[str, Any]]:
    """WebVTT：跳过 WEBVTT 头 / NOTE / STYLE / REGION 块；cue 可带 settings。"""
    segments: list[dict[str, Any]] = []
    body = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    for block in re.split(r"\n\s*\n", body):
        lines = block.split("\n")
        if not lines:
            continue
        head = lines[0].strip()
        # 头部/注释/样式块整块跳过
        if head.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        time_idx = None
        for i, ln in enumerate(lines):
            if "-->" in ln:
                time_idx = i
                break
        if time_idx is None:
            continue
        m = _VTT_TIME_RE.search(lines[time_idx])
        if not m:
            continue
        g = m.groups()
        start = _to_seconds(g[0], g[1], g[2], g[3])
        end = _to_seconds(g[4], g[5], g[6], g[7])
        if end <= start:
            continue
        raw_text = "\n".join(lines[time_idx + 1:])
        txt = _clean_text(raw_text, strip_tags=strip_tags)
        if not txt:
            continue
        segments.append({"start": round(start, 3), "end": round(end, 3), "text": txt})
    return segments


def _merge_adjacent(segments: list[dict[str, Any]], merge_gap_ms: float) -> list[dict[str, Any]]:
    """合并间隔很小的相邻段（字幕常一句拆成 2-3 个 cue，合并后语义完整）。

    merge_gap_ms=0（默认）不合并——保持与字幕原时序一一对应，便于回溯。
    """
    if merge_gap_ms <= 0 or len(segments) <= 1:
        return segments
    gap_s = merge_gap_ms / 1000.0
    out: list[dict[str, Any]] = [dict(segments[0])]
    for seg in segments[1:]:
        prev = out[-1]
        if seg["start"] - prev["end"] <= gap_s:
            prev["end"] = seg["end"]
            prev["text"] = (prev["text"] + " " + seg["text"]).strip()
        else:
            out.append(dict(seg))
    return out


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    sub_raw = inputs.get("subtitle_path")
    if sub_raw is None or not str(sub_raw).strip():
        raise SubtitleError("缺少输入 subtitle_path（.srt/.vtt 字幕文件路径）")
    sub_path = Path(str(sub_raw))
    if not sub_path.is_file():
        raise SubtitleError(f"字幕文件不存在: {sub_path}")

    suffix = sub_path.suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise SubtitleError(
            f"不支持的字幕格式 {suffix!r}（支持 {' / '.join(_ALLOWED_SUFFIXES)}）。"
            "富样式字幕（.ass/.ssa）请先用 ffmpeg 转换："
            f"`ffmpeg -i {sub_path.name} out.srt`"
        )

    strip_tags = bool(config.get("strip_tags", True))
    merge_gap_ms = float(config.get("merge_short_ms", 0) or 0)

    try:
        raw_text = sub_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise SubtitleError(f"字幕文件读取失败: {exc}") from exc

    ctx.log(f"subtitle: 解析 {sub_path.name}（{len(raw_text)} 字符，format={suffix.lstrip('.')}）")
    if ctx.cancelled:
        raise _Cancelled("字幕解析前收到取消信号")

    if suffix == ".vtt":
        segments = _parse_vtt(raw_text, strip_tags=strip_tags)
    else:
        segments = _parse_srt(raw_text, strip_tags=strip_tags)

    if not segments:
        raise SubtitleError(
            f"字幕解析出 0 段（{sub_path.name}）——文件可能是空字幕、编码异常，"
            "或时间轴格式不符合 SRT/VTT 规范"
        )

    segments = _merge_adjacent(segments, merge_gap_ms)
    if ctx.cancelled:
        raise _Cancelled("字幕解析后收到取消信号")

    # 质量信号：人工字幕 = ASR 层面天然满分（见模块头「质量信号的处理」）
    for seg in segments:
        seg["avg_logprob"] = 1.0
        seg["no_speech_prob"] = 0.0

    duration = segments[-1]["end"]
    ctx.log(
        f"subtitle: {len(segments)} 段 / {duration:.3f}s"
        + (f"（已合并 {merge_gap_ms:.0f}ms 内相邻段）" if merge_gap_ms > 0 else "")
    )

    # **透传 media_path**（本插件在链中的位置等价于 transcribe，见模块头分工图）：
    # 字幕链的典型形态是「视频 + 配套字幕」——视频文件在，只是不经过 ASR。
    # 下游 frame-grab 需要 media_path 才能抽帧，若不透传则
    # validate_pipeline_contract 会判「必需输入端口未被供给」而拒整条链
    # （2026-11-20 实测：subtitle→key-segment→frame-grab 链因此断裂）。
    # 字幕与媒体可以不同源（如字幕来自别处），故 media_path 是可选输入：
    # 给了就透传，没给则**恒产出 None**（2026-11-23 扫尾裁决——「声明即产出」
    # 是 manifest outputs 的设计原则（正典 §4.2：outputs 不使用 required），
    # 引擎按全键校验，键缺席=OutputContractError 判失败；产出 None 让下游
    # 自己可读降级（frame-grab 守卫对 None/空串报「缺少输入 media_path」）。
    out: dict[str, Any] = {
        "transcript": segments,
        "duration_s": round(duration, 3),
        "source_kind": "subtitle" + suffix,
        "media_path": None,
    }
    media_raw = inputs.get("media_path")
    if media_raw is not None and str(media_raw).strip():
        media = Path(str(media_raw))
        if not media.is_file():
            raise SubtitleError(f"media_path 指向的文件不存在: {media}")
        out["media_path"] = str(media)
        ctx.log(f"subtitle: 透传 media_path → {media.name}（下游可抽帧）")
    else:
        ctx.log("subtitle: 未提供 media_path——输出 None，下游只能到成稿，无法抽帧")

    return out
