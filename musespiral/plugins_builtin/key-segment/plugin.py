"""官方插件·关键片段切片（首插件族 #3，kind=process，正典 §9.1，PL-5）。

吃 transcribe 的 segments，产出 frame-grab 要的 ranges（时间区间+标签+
置信度+摘录）。**双路径是本插件的关键设计**：

  AI 已配置  → ``chat_policed(task="plugin_key_segment")`` 一次调用返 JSON
               （任务键一插件一键，ai.json routing 可把本插件独立路由到
               专属档案、prompts.d/plugin_key_segment.txt 可独立覆盖提示词
               ——不与其他 AI 插件共用泛化键）；
  未配置/失败 → **降级规则切片**（段落密度/标题词/停顿长度打分）——AI 是
               增强不是依赖，不可用时必须仍能产出可用结果，而不是报错
               让用户卡在半条流水线上（降级原因写进运行日志可查）。

红线（正典 §7.3）：AI 只走 chat_policed 治理层（缓存/预算/台账自动生效），
绝不绕过它直调底层 chat——uses_ai: true 声明让平台与前端知晓本插件会调
AI（v1 引擎尚不注入独立 AI 客户端对象，治理层网关即「受管客户端」本体）。

置信纪律（沿既有冲突检测「提示不拦截」先例）：confidence < min_confidence
的片段 label 前标 ⚠️ **但不丢弃**——低质量切片常有抢救价值（换模型/人工
核对），静默删除才是错的。AI 自评 confidence 被要求诚实；规则路径的
confidence 来自可解释的加权分（密度/标题词/停顿/转写质量四因子）。

三态裁决（AI 产物裁决机制正典 2026-11-21，AI-1 批）：ranges 每项带
``status``——默认 ``draft``（**AI 建议不是结论**：可用但 UI 须标「AI 建议」
徽章，待人工采纳/弃用；流水线不因未裁决卡死，draft 下游照常可消费）；
``config.auto_adopt=true`` 时直接 ``adopted``（旧行为，供信任 AI 的用户）。
切片是「什么重要」的判断——创作者最不愿被替代的那个决定，默认给建议、
不替用户拍板。

编辑流（评审 G-2/G-3，正典 PL-3 契约）：前端在转写面板改完段落会落
``workspace/<上游节点>/segments.edited.json``（不覆盖原始 outputs.json，
保留对照）；本插件经 ctx.upstream 接线面定位**实际供给 transcript 的
上游节点**，存在编辑版即优先读——用户改掉幻听词后重跑切片，吃到的就是
改后文本（接线上游未知时退回兄弟目录 mtime 扫描兜底）。
"""
from __future__ import annotations

import bisect
import json
import re
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginError
from musespiral.infra.ai import gateway as _ai_gateway
from musespiral.infra.ai import profiles as _ai_profiles
from musespiral.infra.ai.profiles import AIError

# 任务键（profiles.TASKS 白名单成员，2026-11-20 扩键 23→24）
TASK = "plugin_key_segment"

# config 缺省（与 manifest.config_schema.default 同一事实，双写保直读可测）
_DEFAULT_MAX_SEGMENTS = 8
_DEFAULT_MIN_CONFIDENCE = 0.4
_DEFAULT_AUTO_ADOPT = False

# 三态裁决取值（全系统统一，正典 §二：draft=AI 建议/adopted=人工确认/
# discarded=人工否定——本插件只产前两态，discarded 由裁决端点写入）
_STATUS_DRAFT = "draft"
_STATUS_ADOPTED = "adopted"

# 规则路径参数（可解释启发式，改值须同步测试注释）
_PAUSE_SPLIT_S = 1.2      # 停顿 ≥ 此值视为话题边界（分组切点）
_MAX_GROUP_S = 90.0       # 单组时长上限（无停顿的密集转写也得分组）
_HEADING_PHRASES = (      # 标题词（口语讲结构性内容的高频信号）
    "重点", "关键", "总结", "注意", "首先", "其次", "最后", "接下来",
    "如何", "为什么", "步骤", "原理", "核心", "本质", "区别", "误区",
    "第一", "第二", "第三", "本章", "本节", "小结", "实战", "案例",
)
_NUMBERED_RE = re.compile(r"(?:^|[\s，。、])(?:[一二三四五六七八九十]+、|[0-9]{1,2}[.、)])")

# 权重（规则路径四因子，和为 1——confidence 归一 0~1）
_W_HEADING, _W_DENSITY, _W_PAUSE, _W_QUALITY = 0.35, 0.25, 0.20, 0.20
_DENSITY_FULL = 8.0       # 8 字/秒视为满密度（正常语速 4-5，快讲 7-8）

# 转写质量口径（与 transcribe G-1 同源：avg_logprob < -1 低置信、
# no_speech_prob > 0.6 疑似非语音）——规则路径的质量因子吃这两个信号
_LOW_LOGPROB = -1.0
_HIGH_NO_SPEECH = 0.6

# AI 路径参数
_AI_TEMPERATURE = 0.2     # 切片判定要稳不要发散
_AI_MAX_PROMPT_CHARS = 60_000  # 超长转写不做 AI 切片（提示词爆预算）→ 降级规则
_SEGMENT_TEXT_CAP = 160   # 单段文本进提示词的截断（保时间戳全量、截长文本）
_EXCERPT_CHARS = 60
_LABEL_CHARS = 24

_SYSTEM_PROMPT = (
    "你是视频内容编辑，从语音转写段落中挑出最有信息量的关键片段，供后续截取配图与成稿。\n"
    "只输出一个 JSON 数组，不要输出数组以外的任何文字。数组每项形如：\n"
    '{"start": 起始秒, "end": 结束秒, "label": "不超过16字的片段标题", '
    '"confidence": 0到1的置信度, "text_excerpt": "不超过60字的原文摘录"}\n'
    "硬性要求：\n"
    "- start/end 只能取自输入段落的时间戳（可用某段的 start 与某段的 end 拼出覆盖区间），"
    "不得自造数值；\n"
    "- 片段数量以上限为准（见用户消息），按信息价值从高到低选择，宁缺毋滥；\n"
    "- confidence 是你对「该片段值得作为关键片段」的自评置信度，必须诚实："
    "证据不足就给低分；\n"
    "- 标注了[低置信]或[疑似非语音]的段落转写质量存疑，除非别无选择不要以其为主体；\n"
    "- text_excerpt 必须摘自原文，不得改写。"
)

_EDITED_SEGMENTS_FILE = "segments.edited.json"


class _AIRangeError(RuntimeError):
    """AI 路径内部失败（响应不可解析/无有效项）——外层据此降级规则切片。"""


# ── 编辑版优先（评审 G-2：下游优先读 segments.edited.json）──────────


def _wired_upstream_node(ctx: Any, port_name: str) -> str | None:
    """该输入端口实际接线的上游节点 id（ctx.upstream 接线面；无接线信息的
    宿主返回 None）。节点 id 只接受安全段（不含路径分隔/点段）——它要拼
    workspace 子目录路径。"""
    upstream = getattr(ctx, "upstream", None)
    if not isinstance(upstream, dict):
        return None
    node = upstream.get(port_name)
    node = str(node) if node else ""
    if not node or "/" in node or "\\" in node or node in (".", ".."):
        return None
    return node


def _read_edited_file(ctx: Any, port_name: str, filename: str) -> list[dict[str, Any]] | None:
    """读**实际接线上游节点**目录下的编辑版文件（存在即优先）。

    定向纪律（2026-11-20 复核：mtime 全局扫描会跨节点串味——双机位/双
    语言多路转写时，A 的编辑会被套到 B 的时间轴上且零报错）：编辑版只在
    ``workspace/<接线上游节点>/`` 找；接线上游未知（旧引擎/单测假 ctx，
    ctx.upstream 为空面）→ 退回全局 mtime 扫描兜底（老行为，容错不收紧）。
    文件损坏/非列表 → 视为无编辑版（可读日志，回退原始输入——编辑文件是
    增强不是依赖）。
    """
    root = Path(ctx.artifacts_dir).parent
    wired = _wired_upstream_node(ctx, port_name)
    if wired is not None:
        candidates = [root / wired / filename]
        if not candidates[0].is_file():
            return None  # 定向模式：接线上游没有编辑版=常态，静默回原始输入
    else:
        try:
            candidates = sorted(
                root.glob(f"*/{filename}"), key=lambda p: p.stat().st_mtime
            )
        except OSError:
            return None
    if not candidates:
        return None
    latest = candidates[-1]
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ctx.log(f"key-segment: 编辑版转写不可读（{latest.name}: {exc}），回退原始输入")
        return None
    if isinstance(data, dict):  # 宽容 {"segments": [...]} 包装形
        data = data.get("segments")
    if not isinstance(data, list):
        ctx.log("key-segment: 编辑版转写非列表，回退原始输入")
        return None
    origin = f"{latest.parent.name}/{latest.name}" if wired is not None else latest.name
    ctx.log(f"key-segment: 优先读编辑版转写 {origin}")
    return data


def _find_edited_segments(ctx: Any) -> list[dict[str, Any]] | None:
    return _read_edited_file(ctx, "transcript", _EDITED_SEGMENTS_FILE)


# ── 规则路径（降级切片：密度/标题词/停顿长度打分）──────────────────


def _is_heading(text: str) -> bool:
    return any(p in text for p in _HEADING_PHRASES) or bool(_NUMBERED_RE.search(text))


def _seg_ok(seg: dict[str, Any]) -> bool:
    """转写质量信号合格（缺信号视为合格——非本插件产出不带信号不冤枉）。"""
    logprob = seg.get("avg_logprob")
    no_speech = seg.get("no_speech_prob")
    if isinstance(logprob, (int, float)) and float(logprob) < _LOW_LOGPROB:
        return False
    if isinstance(no_speech, (int, float)) and float(no_speech) > _HIGH_NO_SPEECH:
        return False
    return True


def _group_segments(segments: list[dict[str, Any]]) -> list[tuple[float, list[dict[str, Any]]]]:
    """按话题边界分组 → (组前停顿秒, 组)，停顿随组带出（打分因子之一）。

    三切点：停顿 ≥ _PAUSE_SPLIT_S、标题词命中、组时长超 _MAX_GROUP_S。
    """
    groups: list[tuple[float, list[dict[str, Any]]]] = []
    current: list[dict[str, Any]] = []
    prev_end: float | None = None
    group_start = 0.0
    pause_before = 0.0
    for seg in segments:
        start, end = float(seg["start"]), float(seg["end"])
        if current:
            pause = max(0.0, start - prev_end) if prev_end is not None else 0.0
            if (
                pause >= _PAUSE_SPLIT_S
                or _is_heading(str(seg.get("text") or ""))
                or end - group_start > _MAX_GROUP_S
            ):
                groups.append((pause_before, current))
                current = []
                pause_before = pause
        if not current:
            group_start = start
        current.append(seg)
        prev_end = end
    if current:
        groups.append((pause_before, current))
    return groups


def _fmt_mmss(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m:02d}:{s:02d}"


def _rule_ranges(segments: list[dict[str, Any]], max_segments: int) -> list[dict[str, Any]]:
    """确定性规则切片：分组 → 四因子打分 → 取 Top-N（按时间重排输出）。

    四因子（权重和为 1）：标题词命中（0.35）× 文本密度（0.25）× 组前停顿
    （0.20）× 转写质量合格率（0.20，吃 transcribe 的 G-1 信号）。

    每 range 附 ``segment_windows``（组内段落的 [start,end] 列表，时间升序）
    ——下游 frame-grab 抽帧时刻据此避开组内语音空洞（start+0.3 落在「嗯」
    类极短段与下一段之间的空隙时后移到语音窗起点；2026-11-20 复核判例）。
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for pause_s, group in _group_segments(segments):
        g_start = float(group[0]["start"])
        g_end = float(group[-1]["end"])
        duration = max(g_end - g_start, 0.001)
        text_all = "".join(str(s.get("text") or "") for s in group)

        hits = sum(1 for s in group if _is_heading(str(s.get("text") or "")))
        heading = min(hits, 2) / 2.0
        density = min(len(text_all) / duration / _DENSITY_FULL, 1.0)
        pause = min(pause_s / _PAUSE_SPLIT_S, 2.0) / 2.0
        ok_ratio = sum(1 for s in group if _seg_ok(s)) / len(group)

        confidence = round(
            min(_W_HEADING * heading + _W_DENSITY * density
                + _W_PAUSE * pause + _W_QUALITY * ok_ratio, 1.0),
            3,
        )

        heading_text = next(
            (str(s.get("text") or "").strip() for s in group
             if _is_heading(str(s.get("text") or "")) and str(s.get("text") or "").strip()),
            "",
        )
        label = (heading_text[:_LABEL_CHARS] or f"片段 {_fmt_mmss(g_start)}")
        excerpt = text_all.strip()[:_EXCERPT_CHARS] or str(group[0].get("text") or "")[:_EXCERPT_CHARS]
        windows = [
            (round(float(s["start"]), 3), round(float(s["end"]), 3))
            for s in sorted(group, key=lambda s: float(s["start"]))
        ]
        scored.append((confidence, {
            "start": round(g_start, 3),
            "end": round(g_end, 3),
            "label": label,
            "confidence": confidence,
            "text_excerpt": excerpt,
            "segment_windows": windows,
        }))
    scored.sort(key=lambda item: (-item[0], item[1]["start"]))
    picked = [r for _score, r in scored[:max_segments]]
    picked.sort(key=lambda r: r["start"])
    return picked


# ── AI 路径（chat_policed 一次调用返 JSON）─────────────────────────


def _build_prompt(segments: list[dict[str, Any]], max_segments: int) -> list[dict[str, str]] | None:
    """组装消息；转写过长（> _AI_MAX_PROMPT_CHARS）返回 None → 外层降级。"""
    lines: list[str] = []
    for i, seg in enumerate(segments):
        text = str(seg.get("text") or "").strip()[:_SEGMENT_TEXT_CAP]
        logprob = seg.get("avg_logprob")
        no_speech = seg.get("no_speech_prob")
        if isinstance(no_speech, (int, float)) and float(no_speech) > _HIGH_NO_SPEECH:
            flags = "[疑似非语音]"
        elif isinstance(logprob, (int, float)) and float(logprob) < _LOW_LOGPROB:
            flags = "[低置信]"
        else:
            flags = ""
        lines.append(f"{i}|{float(seg['start']):.2f}-{float(seg['end']):.2f}|{flags}{text}")
    user = (
        f"至多选 {max_segments} 个关键片段。转写段落（序号|起始秒-结束秒|文本）：\n"
        + "\n".join(lines)
    )
    system = _SYSTEM_PROMPT  # 无占位符（字面 JSON 示例含花括号，不走 str.format）
    if len(system) + len(user) > _AI_MAX_PROMPT_CHARS:
        return None
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _extract_json_array(text: str) -> list[Any]:
    """从 AI 回复里抠 JSON 数组（容忍 ```json 围栏与前后缀文字）。"""
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        i, j = text.find("["), text.rfind("]")
        if i == -1 or j <= i:
            raise _AIRangeError("回复中找不到 JSON 数组")
        candidate = text[i:j + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise _AIRangeError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, list):
        raise _AIRangeError("JSON 顶层不是数组")
    return data


def _snap_to_segment_grid(
    segments: list[dict[str, Any]], start: float, end: float
) -> tuple[float, float, list[tuple[float, float]]] | None:
    """把 AI 给的 [start, end] 收敛到**段落时间戳网格**（提示词硬性要求的
    执行面——AI 数值不可信，靠这里保不变量）。

    start → 覆盖它的段落的 start（start ≤ s 的最后一段；s 早于首段收敛到
    首段）；end → 覆盖它的段落的 end（end ≥ e 的最早段；越界收敛到末段）。
    返回 (snapped_start, snapped_end, 覆盖段落的 [start,end] 窗口列表，
    时间升序)；收敛后不成正区间 → None（调用方丢弃该项）。

    为什么 snap 而不是拒：提示词要求「取自段落时间戳」，AI 惯性自造数值
    （实测 1.37/7.9）——整项丢弃会把可用片段变成空手而归；收敛到语义上
    等价的段落边界既保住片段又保住「range.start 精确等于某 segment.start」
    的三级回溯不变量（frame-grab 的 ts 锚与成稿 [#t=秒] 都吃它）。
    """
    starts = sorted(float(s["start"]) for s in segments)
    ends = sorted(float(s["end"]) for s in segments)
    i = bisect.bisect_right(starts, start + 1e-9) - 1
    ns = starts[max(i, 0)]
    j = bisect.bisect_left(ends, end - 1e-9)
    ne = ends[min(j, len(ends) - 1)]
    if not ns < ne:
        return None
    windows = sorted(
        (round(float(s["start"]), 3), round(float(s["end"]), 3))
        for s in segments
        if float(s["end"]) > ns and float(s["start"]) < ne
    )
    return round(ns, 3), round(ne, 3), windows


def _ai_ranges(
    segments: list[dict[str, Any]], max_segments: int, ctx: Any
) -> list[dict[str, Any]]:
    messages = _build_prompt(segments, max_segments)
    if messages is None:
        ctx.log("key-segment: 转写过长超 AI 提示词上限，降级规则切片")
        raise _AIRangeError("prompt 超限")
    res = _ai_gateway.chat_policed(
        messages, task=TASK, temperature=_AI_TEMPERATURE, cache=True
    )
    text = str(res.get("text") or "")
    out: list[dict[str, Any]] = []
    dropped = 0
    for item in _extract_json_array(text):
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            dropped += 1
            continue
        if not (0 <= start < end):
            dropped += 1
            continue
        snapped = _snap_to_segment_grid(segments, start, end)
        if snapped is None:  # 收敛不成正区间（如整段落在首段之前）
            dropped += 1
            continue
        start, end, windows = snapped
        raw_conf = item.get("confidence")
        try:
            confidence = min(max(float(raw_conf), 0.0), 1.0)
        except (TypeError, ValueError):
            confidence = 0.5
        label = str(item.get("label") or "").strip()[:_LABEL_CHARS] or f"片段 {_fmt_mmss(start)}"
        excerpt = str(item.get("text_excerpt") or "").strip()[:_EXCERPT_CHARS]
        if not excerpt:  # 摘录缺失 → 从覆盖区间的段落原文回填（不得虚构）
            excerpt = "".join(
                str(s.get("text") or "") for s in segments
                if float(s["end"]) > start and float(s["start"]) < end
            ).strip()[:_EXCERPT_CHARS]
        out.append({
            "start": start,
            "end": end,
            "label": label,
            "confidence": round(confidence, 3),
            "text_excerpt": excerpt,
            "segment_windows": windows,
        })
    if not out:
        raise _AIRangeError("AI 返回零个有效片段")
    if len(out) > max_segments:
        ctx.log(f"key-segment: AI 返回 {len(out)} 片段超上限，裁断为 {max_segments}")
        out = out[:max_segments]
    if dropped:
        ctx.log(f"key-segment: 丢弃 {dropped} 个非法/无法收敛到段落网格的片段（AI 返回）")
    out.sort(key=lambda r: r["start"])
    return out


# ── 入口 ───────────────────────────────────────────────────────────


def _validate_transcript(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        raise PluginError("缺少输入 transcript（transcribe 的 segments 输出）")
    if not isinstance(raw, list) or not all(isinstance(s, dict) for s in raw):
        raise PluginError("transcript 须为段落列表（[{start, end, text, ...}]）")
    for i, seg in enumerate(raw):
        try:
            start, end = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PluginError(f"transcript[{i}] 缺有效 start/end: {exc}") from exc
        if not start < end:
            raise PluginError(f"transcript[{i}] start ≥ end（{start} ≥ {end}）")
    return raw


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    transcript = _validate_transcript(inputs.get("transcript"))

    edited = _find_edited_segments(ctx)
    if edited is not None:
        transcript = _validate_transcript(edited)

    try:
        max_segments = min(max(int(config.get("max_segments", _DEFAULT_MAX_SEGMENTS)), 1), 50)
    except (TypeError, ValueError):
        raise PluginError(f"config.max_segments 须为整数: {config.get('max_segments')!r}") from None
    try:
        min_confidence = min(max(float(config.get("min_confidence", _DEFAULT_MIN_CONFIDENCE)), 0.0), 1.0)
    except (TypeError, ValueError):
        raise PluginError(f"config.min_confidence 须为数值: {config.get('min_confidence')!r}") from None
    # 三态裁决开关：引擎 _merge_config 只填 default 不深校验——布尔性插件自验
    auto_adopt = config.get("auto_adopt", _DEFAULT_AUTO_ADOPT)
    if not isinstance(auto_adopt, bool):
        raise PluginError(f"config.auto_adopt 须为布尔: {auto_adopt!r}")

    if not transcript:
        ctx.log("key-segment: 转写为空（纯音乐/静音素材），ranges 置空")
        ctx.progress(1, 1)
        return {"ranges": []}

    ranges: list[dict[str, Any]]
    if _ai_profiles.ai_configured(TASK):
        try:
            ranges = _ai_ranges(transcript, max_segments, ctx)
            ctx.log(f"key-segment: AI 切片 {len(ranges)} 段（task={TASK}）")
        except (AIError, _AIRangeError) as exc:
            # 治理层整链失败面 = AIError（client 把网络/格式/预算全部收敛为
            # AIError 族，BudgetExceeded 亦是其子类）；_AIRangeError 是回复
            # 解析失败。两者皆降级规则切片——AI 是增强不是依赖。本插件自身
            # 的 bug（TypeError 等）不在捕获面，该失败就失败（不静默吞）。
            ctx.log(f"key-segment: AI 切片不可用（{exc}），降级规则切片")
            ranges = _rule_ranges(transcript, max_segments)
    else:
        ctx.log("key-segment: AI 未配置，走规则切片（密度/标题词/停顿打分）")
        ranges = _rule_ranges(transcript, max_segments)

    marked = 0
    for r in ranges:
        if r["confidence"] < min_confidence and not r["label"].startswith("⚠️"):
            r["label"] = f"⚠️ {r['label']}"
            marked += 1
    if marked:
        ctx.log(f"key-segment: {marked} 段低置信（< {min_confidence}）已标 ⚠️（不丢弃）")

    # 三态裁决（AI-1 批，公共尾部——AI/规则两路径同覆盖）：切片判断默认是
    # 「建议」不是「正式结果」。draft 不禁用下游（流水线不卡），状态在场，
    # 用户想审查时随手可采纳/弃用（裁决端点按 run 产物操作，本层只产态）。
    status = _STATUS_ADOPTED if auto_adopt else _STATUS_DRAFT
    for r in ranges:
        r["status"] = status
    if status == _STATUS_DRAFT:
        ctx.log(f"key-segment: AI 建议 {len(ranges)} 段（draft 态，待人工裁决）")
    else:
        ctx.log(f"key-segment: 产出 {len(ranges)} 段（auto_adopt=true，直接 adopted）")

    ctx.progress(1, 1)
    return {"ranges": ranges}


__all__ = ["TASK", "run"]
