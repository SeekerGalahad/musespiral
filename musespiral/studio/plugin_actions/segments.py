"""plugin_actions · segments 域——E-4 转写编辑 + 裁段音频。

ARCH-2 ③（拆自 studio/plugin_actions.py 单文件 950 行，行为零变更）。
前端转写编辑面板（plugin-run.js：低置信告警/逐段编辑/听原声/ranges 增删）
的三端点。编辑版文件 = key-segment/frame-grab 既有的 ``segments.edited.json``
/ ``ranges.edited.json`` 消费面（评审 G-2「编辑版优先」）——POST 落盘位置
就是这两个插件的读取位置，改完重跑即生效。

  - GET  /api/pipelines/runs/<run_id>/segments：转写段落+切片 ranges（编辑版
    优先——E-4，PL-6 批接驳）；
  - POST /api/pipelines/runs/segments（body run_id；ROUTES 片段 1 条）：
    编辑版落盘（E-4；PL-3 先例：动态段 POST 改精确路径 + body run_id）；
  - GET  /api/pipelines/runs/<run_id>/audio?start=&end=：裁段音频 wav 字节流
    （E-4；从源媒体现场裁——音轨 tmpdir 运行后已清理）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from ..security import _HttpError, _StudioContext
from .run_read import (
    _effective_edited,
    _find_output_node,
    _node_output_docs,
    _run_dir_checked,
)


def _pipeline_run_segments_payload(ctx: _StudioContext, run_id: str) -> dict[str, Any]:
    """GET /api/pipelines/runs/<run_id>/segments：转写段落 + 切片 ranges。

    编辑版优先——已编辑过的 run 返回编辑版（前端续编辑基线，与下游插件
    重跑时读到的同一份）；无转写产物（链无 transcribe / 失败早停）404。
    """
    run_dir = _run_dir_checked(run_id)
    found = _find_output_node(run_dir, "transcript")
    if found is None:
        raise _HttpError(404, f"该运行无转写产物（segments）: {run_id}")
    seg_node, segments = found
    segments = _effective_edited(seg_node, "segments.edited.json", "segments", segments)
    ranges: list[dict[str, Any]] = []
    found_r = _find_output_node(run_dir, "ranges")
    if found_r is not None:
        rng_node, ranges = found_r
        ranges = _effective_edited(rng_node, "ranges.edited.json", "ranges", ranges)
    return {"run_id": run_id, "segments": segments, "ranges": ranges}


def _validate_time_pair(item: Any, label: str) -> tuple[float, float]:
    if not isinstance(item, dict):
        raise _HttpError(400, f"{label} 须为对象")
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError):
        raise _HttpError(400, f"{label} 缺有效 start/end") from None
    if not start < end:
        raise _HttpError(400, f"{label} start ≥ end（{start} ≥ {end}）")
    return start, end


def _validate_segments_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise _HttpError(400, "segments 须为非空段落列表（[{start, end, text}]）")
    for i, seg in enumerate(raw):
        _validate_time_pair(seg, f"segments[{i}]")
        if not isinstance(seg.get("text"), str):
            raise _HttpError(400, f"segments[{i}].text 须为字符串")
    return raw


def _validate_ranges_payload(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise _HttpError(400, "ranges 须为区间列表（[{start, end, label?}]）")
    for i, rng in enumerate(raw):
        _validate_time_pair(rng, f"ranges[{i}]")
    return raw


def _action_pipeline_run_segments_save(
    ctx: _StudioContext, data: dict[str, Any]
) -> dict[str, Any]:
    """POST /api/pipelines/runs/segments（body run_id）：编辑版落盘
    segments.edited.json / ranges.edited.json——写进**产出该端口**的节点
    目录（key-segment/frame-grab 的消费位置），原始 outputs.json 不覆盖。
    """
    run_id = str(data.get("run_id") or "").strip()
    run_dir = _run_dir_checked(run_id)
    found = _find_output_node(run_dir, "transcript")
    if found is None:
        raise _HttpError(400, f"该运行无转写产物节点，无处落 segments.edited: {run_id}")
    seg_node, _ = found
    segments = _validate_segments_payload(data.get("segments"))
    (seg_node / "segments.edited.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    saved = ["segments.edited.json"]
    raw_ranges = data.get("ranges")
    if raw_ranges is not None:
        found_r = _find_output_node(run_dir, "ranges")
        if found_r is None:
            raise _HttpError(
                400, "该运行无 ranges 产出节点，无处落 ranges.edited（链上需有切片节点）"
            )
        rng_node, _ = found_r
        ranges = _validate_ranges_payload(raw_ranges)
        (rng_node / "ranges.edited.json").write_text(
            json.dumps(ranges, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        saved.append("ranges.edited.json")
    return {
        "ok": True,
        "run_id": run_id,
        "saved": saved,
        "segments": len(segments),
        "ranges": len(raw_ranges or []),
    }


_AUDIO_MAX_CLIP_S = 120.0  # 裁段上限（听段落原声用，非整轨下载面）
_AUDIO_TIMEOUT_S = 120


def _find_media_path(run_dir: Path) -> Path | None:
    """源媒体定位：workspace 各节点 outputs.json 的 refs 里第一个
    kind=path 且文件存在的引用（local-file 的「原路径不复制」产出）。"""
    for node_dir, doc in _node_output_docs(run_dir):
        refs = doc.get("refs")
        if not isinstance(refs, dict):
            continue
        for ref in refs.values():
            if not (isinstance(ref, dict) and ref.get("kind") == "path"):
                continue
            cand = Path(str(ref.get("file") or ""))
            if cand.is_file():
                return cand
    return None


def _pipeline_run_audio_response(
    ctx: _StudioContext, run_id: str, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET /api/pipelines/runs/<run_id>/audio?start=&end=：裁段音频 wav。

    转写抽的音轨在 ctx.tmpdir（运行结束即清理）——不追 run 内缓存，从源
    媒体现场裁 16kHz mono wav（transcribe 抽轨同规格，听感一致）。源媒体
    是用户本机文件（local-file path 引用）：被移动/删除 → 404 可读。
    """
    run_dir = _run_dir_checked(run_id)
    media = _find_media_path(run_dir)
    if media is None:
        raise _HttpError(
            404, f"该运行无可裁音频的源媒体（local-file 引用缺失或文件已移动）: {run_id}"
        )
    try:
        start = float((query.get("start") or [""])[0])
        end = float((query.get("end") or [""])[0])
    except ValueError:
        raise _HttpError(400, "start/end 须为秒数（?start=1.5&end=6.2）") from None
    if start < 0 or end <= start:
        raise _HttpError(400, f"须 0 ≤ start < end: start={start}, end={end}")
    if end - start > _AUDIO_MAX_CLIP_S:
        raise _HttpError(400, f"裁段过长（上限 {_AUDIO_MAX_CLIP_S:g}s）: {end - start:g}s")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise _HttpError(500, "ffmpeg 不可用：请先安装（brew install ffmpeg / apt install ffmpeg）")
    fd, tmp_name = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    wav_path = Path(tmp_name)
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error",
             "-ss", f"{start:g}", "-t", f"{end - start:g}", "-i", str(media),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)],
            capture_output=True, timeout=_AUDIO_TIMEOUT_S, check=False,
        )
        if proc.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size == 0:
            tail = proc.stderr.decode("utf-8", "replace")[-300:]
            raise _HttpError(500, f"裁段音频生成失败: {tail}")
        return 200, "audio/wav", wav_path.read_bytes()
    except subprocess.TimeoutExpired as exc:
        raise _HttpError(500, f"裁段音频生成超时（>{_AUDIO_TIMEOUT_S}s）") from exc
    finally:
        wav_path.unlink(missing_ok=True)


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    # E-4 转写编辑保存（PL-6 批；body run_id——PL-3「动态段 POST 改精确
    # 路径」先例，server 动作分派零改动）
    "/api/pipelines/runs/segments": _action_pipeline_run_segments_save,
}
