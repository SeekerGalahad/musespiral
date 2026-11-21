"""官方插件·关键帧抽取（首插件族 #4，kind=process，正典 §9.1，PL-5）。

吃 key-segment 的 ranges + 原媒体路径，产出配图（frame 模式）或片段
（clip 模式）。三条纪律：

**避开转场（需求 §插件四 19）**：frame 模式 ``-ss <start+0.3>``——切片
起点常是话题切换处（转场/黑帧/字幕入场），原点抽帧大概率抽到过渡帧；
后移 0.3s 取稳态画面。range 本身短于偏移窗时退守区间中点（宁中点不越界）。

**时间戳锚（正典 §9.3，链路核心增量）**：每个 image 携带 ``ts``（实际
抽帧时刻）与 ``source_range``（来源 range 原文）——``images[k].ts →
ranges[j].start(+0.3) → segments[i].start`` 三级可回溯，成稿阶段才能落
``[00:04:12](#t=252)`` 式锚点。抽帧时刻由 ``_frame_ts`` 从 range 计算：
key-segment 随 range 附带 ``segment_windows``（覆盖段落的时间窗列表），
start+0.3 落进组内语音空洞（「嗯」类极短段与下段之间的空隙）时后移到
首个覆盖它的语音窗起点，保证 ts 落在真实语音上；无窗信息（手造 range/
旧上游）退回 start+0.3，短区间退守中点。产物路径一律字符串（workspace
内联 JSON 序列化，Path 进 dict 会掉进 mem 引用丢跨进程读面）。

**产物受管（需求 §插件四 20）**：全部写 ``ctx.artifacts_dir``
（= workspace/<node_id>/，随 run 保留——LRU 最近 ``PRUNE_KEEP`` 次，
``musespiral plrun prune`` 回收），不写用户目录——抽帧是
流水线中间产物不是交付物，落用户目录=污染。clip 模式 ``-ss/-t -c copy``
流复制不重编码（秒级完成；代价是切点对齐最近关键帧，v1 接受）。

编辑流（评审 G-3）：前端删段/从 segments 勾选加段会落
``workspace/<上游节点>/ranges.edited.json``；本插件经 ctx.upstream 接线
面定位**实际供给 ranges 的上游节点**，该目录存在编辑版即优先读（接线上
游未知时退回兄弟目录 mtime 扫描兜底）——用户勾完段重跑抽帧，吃到的就
是勾后清单。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginCancelled, PluginError

_FRAME_OFFSET_S = 0.3      # 避开转场：起点后移量（需求 §插件四 19）
_FRAME_QUALITY = ("-q:v", "2")  # mjpeg 编码质量 2（2=高质量，best 附近）
_FFMPEG_TIMEOUT_S = 300
_EDITED_RANGES_FILE = "ranges.edited.json"
_SOURCE_KEYS = ("start", "end", "label", "confidence")  # source_range 保留面


def _wired_upstream_node(ctx: Any, port_name: str) -> str | None:
    """该输入端口实际接线的上游节点 id（ctx.upstream 接线面；无接线信息的
    宿主返回 None）。节点 id 只接受安全段（不含路径分隔/点段）。"""
    upstream = getattr(ctx, "upstream", None)
    if not isinstance(upstream, dict):
        return None
    node = upstream.get(port_name)
    node = str(node) if node else ""
    if not node or "/" in node or "\\" in node or node in (".", ".."):
        return None
    return node


def _find_edited_ranges(ctx: Any) -> list[dict[str, Any]] | None:
    """读**实际接线上游节点**目录下的 ``ranges.edited.json``（存在即优先）。

    定向纪律（2026-11-20 复核：mtime 全局扫描会跨节点串味）：只在
    ``workspace/<接线上游节点>/`` 找；接线上游未知（旧引擎/单测假 ctx）→
    退回全局 mtime 扫描兜底（老行为）。损坏回退原始输入（可读日志）。
    """
    root = Path(ctx.artifacts_dir).parent
    wired = _wired_upstream_node(ctx, "ranges")
    if wired is not None:
        candidates: list[Path] = [root / wired / _EDITED_RANGES_FILE]
        if not candidates[0].is_file():
            return None  # 定向模式：接线上游没有编辑版=常态，静默回原始输入
    else:
        try:
            candidates = sorted(
                root.glob(f"*/{_EDITED_RANGES_FILE}"), key=lambda p: p.stat().st_mtime
            )
        except OSError:
            return None
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ctx.log(f"frame-grab: 编辑版 ranges 不可读（{latest.name}: {exc}），回退原始输入")
        return None
    if isinstance(data, dict):  # 宽容 {"ranges": [...]} 包装形
        data = data.get("ranges")
    if not isinstance(data, list):
        ctx.log("frame-grab: 编辑版 ranges 非列表，回退原始输入")
        return None
    origin = f"{latest.parent.name}/{latest.name}" if wired is not None else latest.name
    ctx.log(f"frame-grab: 优先读编辑版 ranges {origin}")
    return data


def _validate_ranges(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        raise PluginError("缺少输入 ranges（key-segment 的输出）")
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise PluginError("ranges 须为区间列表（[{start, end, label, ...}]）")
    for i, r in enumerate(raw):
        try:
            start, end = float(r["start"]), float(r["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError(f"ranges[{i}] 缺有效 start/end: {exc}") from exc
        if not 0 <= start < end:
            raise PluginError(f"ranges[{i}] 非法区间（须 0 ≤ start < end）: {start} ~ {end}")
    return raw


def _source_range(r: dict[str, Any]) -> dict[str, Any]:
    """时间戳锚保留面：只带结构性四键（多余键不扩散进下游契约）。"""
    out: dict[str, Any] = {}
    for key in _SOURCE_KEYS:
        if key in r:
            out[key] = r[key]
    return out


def _frame_ts(r: dict[str, Any]) -> float:
    """抽帧时刻（窗口感知锚）。

    基线=start+0.3 避转场；range 短于偏移窗退守区间中点（宁中点不越界）。
    range 带上游 ``segment_windows``（key-segment 随附的覆盖段落时间窗）
    且基线落进组内语音空洞（如「嗯」类极短段与下段之间的空隙，2026-11-20
    复核判例：ts 落空洞=锚点指向静音+画面与语义无关）→ 后移到首个覆盖
    基线的语音窗起点——ts 重新落在真实语音上，且仍是「可追溯到 segment
    时间窗」的时刻。无窗信息 → 原基线规则（向后兼容手造 range）。
    """
    start, end = float(r["start"]), float(r["end"])
    ts = start + _FRAME_OFFSET_S
    if ts >= end:
        return round((start + end) / 2.0, 3)
    windows = r.get("segment_windows")
    if isinstance(windows, list) and windows:
        in_speech = any(
            isinstance(w, (list, tuple)) and len(w) == 2
            and float(w[0]) <= ts < float(w[1])
            for w in windows
        )
        if not in_speech:
            for w in sorted(windows, key=lambda w: float(w[0])):
                if isinstance(w, (list, tuple)) and len(w) == 2 and ts < float(w[0]) < end:
                    return round(float(w[0]), 3)
    return round(ts, 3)


def _run_ffmpeg(argv: list[str]) -> None:
    """跑一次 ffmpeg；缺席/超时/非零退出/空产物 → 可读 PluginError。"""
    try:
        proc = subprocess.run(
            argv, capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=False
        )
    except FileNotFoundError as exc:
        raise PluginError(
            "ffmpeg 未找到：请先安装（brew install ffmpeg / apt install ffmpeg）"
            "——requires 面的依赖检查（/api/plugins/frame-grab/check）可见"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise PluginError(f"ffmpeg 执行异常: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise PluginError(f"ffmpeg 失败（exit {proc.returncode}）：{' | '.join(tail)}")
    out = Path(argv[-1])
    if not out.is_file() or out.stat().st_size == 0:
        raise PluginError(f"ffmpeg 未产出帧（空/缺失）: {out}")


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    media_raw = inputs.get("media_path")
    if media_raw is None or not str(media_raw).strip():
        raise PluginError("缺少输入 media_path（原媒体文件路径）")
    media = Path(str(media_raw))
    if not media.is_file():
        raise PluginError(f"媒体文件不存在: {media}")

    ranges = _validate_ranges(inputs.get("ranges"))
    edited = _find_edited_ranges(ctx)
    if edited is not None:
        ranges = _validate_ranges(edited)

    mode = str(config.get("mode") or "frame").strip().lower()
    if mode not in ("frame", "clip"):
        raise PluginError(f"config.mode 非法: {mode!r}（可选 frame / clip）")

    ctx.log(f"frame-grab: {len(ranges)} 个区间，mode={mode}，产物落 artifacts_dir")
    artifacts = Path(ctx.artifacts_dir)
    images: list[dict[str, Any]] = []
    clips: list[dict[str, Any]] = []

    for i, r in enumerate(ranges):
        if ctx.cancelled:  # 逐区间自查点（正典 §4.4）
            raise PluginCancelled("frame-grab 在区间间自查点收到取消信号")
        start, end = float(r["start"]), float(r["end"])
        src = _source_range(r)
        if mode == "frame":
            ts = _frame_ts(r)
            if abs(ts - (start + _FRAME_OFFSET_S)) > 1e-9:
                ctx.log(f"frame-grab: 区间[{i}] 抽帧时刻修正为 {ts}s（短区间中点/避开语音空洞）")
            out = artifacts / f"frame_{i + 1:02d}_{ts:.1f}s.jpg"
            _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{ts:.3f}", "-i", str(media),
                "-frames:v", "1", *_FRAME_QUALITY, str(out),
            ])
            images.append({"path": str(out), "ts": ts, "source_range": src})
            ctx.log(f"frame-grab: [{i + 1}/{len(ranges)}] 帧 @ {ts}s → {out.name}")
        else:
            duration = round(end - start, 3)
            out = artifacts / f"clip_{i + 1:02d}_{start:.1f}s.mp4"
            _run_ffmpeg([
                "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(media),
                "-t", f"{duration:.3f}", "-c", "copy", str(out),
            ])
            clips.append({
                "path": str(out), "start": round(start, 3),
                "end": round(end, 3), "source_range": src,
            })
            ctx.log(f"frame-grab: [{i + 1}/{len(ranges)}] 片段 {start}s+{duration}s → {out.name}")
        ctx.progress(i + 1, len(ranges))

    if not ranges:
        ctx.log("frame-grab: ranges 为空（上游未选出关键片段），images/clips 置空")
    return {"images": images, "clips": clips}


__all__ = ["run"]
