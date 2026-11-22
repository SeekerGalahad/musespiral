"""官方插件·时长路由（kind=control，插件 v2 DSL·switch 实战示例）。

**这是 switch 控制流的第一只内置实战插件**——验证「DSL 存在」之外的
「DSL 能用」：switch 节点插件形式（``{"plugin": "duration-switch",
"branch": {...}}``）由 control 插件的 ``route`` 输出选分支（引擎契约：
kind=control ⇒ outputs 必含 string 型 route，core/plugins.manifest
安装期把关；route 落不进 branch 可用路由名 → 运行期可读失败，引擎侧）。

真实场景（视频转文字链的分叉点）：
  短视频（≤5min）→ route=short → 快速路径（跳过切片抽帧，全文直接成稿）；
  长视频（>5min）→ route=long  → 完整流水线（key-segment 切片→抽帧→成稿）。

时长口径：取 transcript 各段 ``end`` 的**最大值**（最后一段的结束秒）——
不依赖上游显式给 duration_s（subtitle/transcribe 都有但契约上不强制），
只吃 manifest 声明的 transcript 一个输入，路由判定自包含。空转写（纯
音乐/静音素材）时长记 0 → short（快速路径，与 article-writer「空转写
产出空稿」的宽容语义衔接）。段落缺 end / 形状不对 → PluginError 可读
失败（路由插件给假路由比失败更危险——选错分支跑错流水线）。

阈值：config.threshold_s（缺省 300s，与 manifest.config_schema.default
双写保直读可测）。引擎 _merge_config 只填 default 不深校验——数值性
自验（非数值拒，宁失败不静默取缺省）。
"""
from __future__ import annotations

from typing import Any

from musespiral.core.plugins.context import PluginError

# 路由名（引擎按该值选 branch；branch 键名须与此一致）
ROUTE_LONG = "long"
ROUTE_SHORT = "short"

# config 缺省（与 manifest.config_schema.default 同一事实，双写保直读可测）
_DEFAULT_THRESHOLD_S = 300.0


def _duration_from_transcript(raw: Any) -> float:
    """transcript（segments）→ 视频总时长（秒）：各段 end 的最大值。

    空列表 → 0.0（快速路径）；非列表/段非 dict/缺有效 end → PluginError
    可读失败——路由依据不造假。
    """
    if not isinstance(raw, list) or not all(isinstance(s, dict) for s in raw):
        raise PluginError("transcript 须为段落列表（[{start, end, text, ...}]）")
    duration = 0.0
    for i, seg in enumerate(raw):
        try:
            end = float(seg["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError(f"transcript[{i}] 缺有效 end: {exc}") from None
        if end > duration:
            duration = end
    return duration


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    duration = _duration_from_transcript(inputs.get("transcript"))
    threshold_raw = config.get("threshold_s", _DEFAULT_THRESHOLD_S)
    try:
        threshold = float(threshold_raw)
    except (TypeError, ValueError):
        raise PluginError(
            f"config.threshold_s 须为数值: {threshold_raw!r}"
        ) from None
    route = ROUTE_LONG if duration > threshold else ROUTE_SHORT
    ctx.log(
        f"duration-switch: 时长 {duration:.1f}s / 阈值 {threshold:g}s → route={route}"
    )
    return {"route": route}


__all__ = ["ROUTE_LONG", "ROUTE_SHORT", "run"]
