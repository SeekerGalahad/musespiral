"""官方插件·逐段精炼（kind=process，插件 v2 DSL·loop 实战示例）。

**这是 loop 控制流的第一只内置实战 body 工人**——loop 节点是引擎原生
（services/pipeline/plan：loop 不接受 plugin 键），本插件是 body 子链里
对**单个段落**做工的 process 插件，实战 DSL 形::

    {"id": "loop1", "type": "loop", "loop_over": "src.transcript",
     "body": [{"id": "refine", "plugin": "refine-segments",
               "inputs": {"segment": {"$item": true}}}]}

引擎逐项注入当前段（``$item``），逐项执行 body，末节点输出端口镜像为
loop 的输出端口、值=逐项合并列表——合并后即合法 segments（start/end
原样保留），可直接喂 article-writer / key-segment，下游零改动。

真实场景：口语转写逐段精炼（一段一段处理，不是全篇一次）——口语的
嗯/啊/然后然后、口误重复、转写别字逐段清理为通顺书面文字。

**AI 路径与降级（本插件的关键设计，沿 key-segment/article-writer 双路径
先例）**：AI 已配置 → ``chat_policed(task="plugin_refine_segment")`` 一次
调用返精炼文字（一插件一任务键：routing/prompts.d 独立调优）；AI 未配置
或调用失败 → **原段透传**（text 不动，``refined: false``）——精炼是增强
不是依赖，AI 缺位绝不让 loop 逐项失败、更不炸流水线。治理红线（正典
§7.3）：只走 chat_policed 治理层（缓存/预算/台账自动生效），绝不绕过。

端口类型口径（v1 类型系统粗粒度的诚实说明）：输入 ``segment`` 用
ext/segment（单项段落，ext 命名空间插件自带 schema——§3.1 约定；$item
注入不走流水线供给校验）；输出 ``refined_segments`` 声明 **segments**
型，因为 loop 的输出端口是 body 末节点输出的镜像、值=逐项合并的**列表**
——单项工人与合并列表共用端口类型是 loop 语义的自然结果（与测试夹具
str-refine 输出 string、合并值为 string 列表同口径）。段输出 dict 带
``refined`` 布尔标记（true=AI 精炼过 / false=原段透传），下游与人工
复核都能分辨哪些段动过。
"""
from __future__ import annotations

from typing import Any

from musespiral.core.plugins.context import PluginError
from musespiral.infra.ai import gateway as _ai_gateway
from musespiral.infra.ai import profiles as _ai_profiles
from musespiral.infra.ai.profiles import AIError

# 任务键（profiles.TASKS 白名单成员；一插件一键，不共用泛化键）
TASK = "plugin_refine_segment"

# AI 路径参数
_AI_TEMPERATURE = 0.2  # 校对要稳不要发散

_SYSTEM_PROMPT = (
    "你是视频转写校对编辑。把口语转写的一段话精炼成通顺的书面文字：\n"
    "去掉口头禅（嗯、啊、那个、然后然后）、重复与无意义的填充词，修正明显的转写别字；\n"
    "保留原意与专有名词，不增删事实信息，不加评论。\n"
    "只输出精炼后的文字本身，不要输出任何解释或引号。"
)


def _validate_segment(raw: Any) -> tuple[float, float, str]:
    """单个段落 dict → (start, end, text)；形状不对 → PluginError 可读失败。

    start<end 与 article-writer/key-segment 同款校验（路由/合并不造假：
    坏段透传输出会让下游成稿带上非法段，宁在工人处红）。
    """
    if not isinstance(raw, dict):
        raise PluginError(f"segment 须为单段 dict（{{start, end, text}}），实为 {type(raw).__name__}")
    try:
        start, end = float(raw["start"]), float(raw["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PluginError(f"segment 缺有效 start/end: {exc}") from None
    if not start < end:
        raise PluginError(f"segment start ≥ end（{start} ≥ {end}）")
    return start, end, str(raw.get("text") or "")


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    start, end, text = _validate_segment(inputs.get("segment"))
    seg = dict(inputs["segment"])  # 浅拷贝透传：avg_logprob 等上游质量信号原样保留
    seg["start"], seg["end"] = start, end

    refined = False
    if not text.strip():
        ctx.log("refine-segments: 空段，原样透传")
    elif not _ai_profiles.ai_configured(TASK):
        ctx.log("refine-segments: AI 未配置，原段透传（refined=false）")
    else:
        try:
            res = _ai_gateway.chat_policed(
                [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
                task=TASK,
                temperature=_AI_TEMPERATURE,
                cache=True,  # 同段重跑零成本（确定性校对变换，非生成类）
            )
            new_text = str(res.get("text") or "").strip()
            if not new_text:
                raise AIError("AI 返回空文本")
            seg["text"] = new_text
            refined = True
        except AIError as exc:
            # 治理层整链失败面 = AIError 族（网络/格式/预算全收敛于此）；
            # 精炼是增强不是依赖——降级原段透传，不让 loop 单项失败。
            ctx.log(f"refine-segments: AI 精炼不可用（{exc}），原段透传")
    seg["refined"] = refined
    ctx.progress(1, 1)
    return {"refined_segments": seg}


__all__ = ["TASK", "run"]
