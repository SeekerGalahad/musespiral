"""假插件·关键段识别（夹具）：关键词命中 → ranges（带 label/confidence）。

正典 §13 的降级路径在真实插件里是「无 AI 时按标题词/停顿规则切片」——
夹具正是这条确定性路径的可执行样本，让引擎的契约链路可在零 AI 环境验证。
"""
from __future__ import annotations

from musespiral.core.plugins.context import PluginError


def run(inputs: dict, config: dict, ctx: object) -> dict:
    segments = inputs["segments"]
    if not isinstance(segments, list) or not segments:
        raise PluginError("segments 为空或非列表")
    keywords = [str(k) for k in (config.get("keywords") or [])]

    ranges: list[dict] = []
    hits = 0
    for i, seg in enumerate(segments):
        text = str(seg.get("text", ""))
        matched = [k for k in keywords if k and k in text]
        if not matched:
            continue
        hits += 1
        ranges.append(
            {
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "label": "/".join(matched),
                # 规则命中给 0.6——置信度低的标 ⚠️ 不丢弃（正典 §9.1 纪律）
                "confidence": 0.6,
            }
        )
        ctx.log(f"key-segment: 第 {i} 段命中 {matched}")
    if not ranges:  # 零命中不判失败：下游可空处理（不制造假失败）
        ctx.log("key-segment: 零关键词命中，ranges 为空")
    score = round(hits / max(len(segments), 1), 3)
    return {"ranges": ranges, "score": score}
