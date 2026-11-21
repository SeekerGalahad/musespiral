"""假插件·语音转写（夹具）：逐行切分 → segments（带 start/end 时间戳）。

真实插件会跑 whisper/ffmpeg；夹具只用文本行数与固定段长造确定性输出，
让整条链路在无外部依赖、无网络的条件下可复现。
"""
from __future__ import annotations

import json
from pathlib import Path

from musespiral.core.plugins.context import PluginCancelled, PluginError


def run(inputs: dict, config: dict, ctx: object) -> dict:
    media = Path(str(inputs["media_path"]))
    language = str(inputs.get("language") or "auto")
    seg_seconds = float(config.get("seg_seconds", 2.5))
    if not media.is_file():
        raise PluginError(f"音轨不存在: {media}")

    lines = [
        ln.strip()
        for ln in media.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    segments: list[dict] = []
    for i, line in enumerate(lines):
        if ctx.cancelled:  # 轮询式取消自查点（正典 §4.4）
            raise PluginCancelled("转写在段间自查点收到取消信号")
        start = round(i * seg_seconds, 2)
        segments.append({"start": start, "end": round(start + seg_seconds, 2), "text": line})
        ctx.progress(i + 1, len(lines))

    # artifacts_dir 面：把完整转写落节点目录（与返回值互为校验）
    out = ctx.artifacts_dir / "transcript.json"
    out.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.log(f"transcribe: {len(segments)} 段（language={language}）→ {out.name}")
    return {"transcript": segments, "duration_s": round(len(segments) * seg_seconds, 2)}
