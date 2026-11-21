"""官方插件·图文稿成稿（首插件族 #5，kind=process，正典 §9.1，PL-6）。

吃 transcribe 的 segments + frame-grab 的 images，产出**带时间戳锚点的
结构化 markdown**。三条纪律：

**时间戳锚是核心产出形态（正典 §9.3，本链路的核心增量）**：不是纯
markdown——每个关键段以 ``[00:04:12](#t=252)`` 锚定，读者看到一段论述
能点回视频原处。锚链 ``segments[i].start → article 锚点`` 在两条路径上
都不丢：AI 给的锚点秒数**强制收敛到段落起点网格**（bisect 最近起点，
tie 取更早；AI 自造数值像 key-segment 的 snap 一样被拉回），降级模板
直接取段落起点——``article 里每个 #t=NNN 都能追溯到一个 segment.start``
是机器可验证的不变量（anchor_map 存精确浮点起点，链接秒数=取整显示）。
正文自由文本（AI 段落/标题/转写直引/标题入参）里夹带的 ``(#t=N)`` 锚
一律剥除（``_strip_forged_anchors``：链接形保文字剥链、裸 href 删除）
——md 中 #t= 锚**只**出自锚点行，anchor_map 是唯一权威（2026-11-20
复核判例：AI text 自造 [00:16:39](#t=999) 会进 md 不进 anchor_map）。

**双组织模式（评审 §3.2，作者视角）**：教学视频常「概念 A 第 3 分钟引入、
第 25 分钟深化」，按时间写出来文章会断裂。config.mode：chronological
（默认，按时间顺序保留叙事流，每段一锚）/ thematic（AI 按主题重组，
同一段可带多锚）。**降级只支持 chronological**（模板拼装是确定性代码，
无 AI 做不了主题聚类）——mode=thematic 而 AI 不可用时降级 chronological
并在运行日志写明，frontmatter.organize_mode 记录实际生效的模式。

**双路径（AI 是增强不是依赖，沿 key-segment 先例）**：AI 已配置 →
``chat_policed(task="plugin_article_writer")`` 返结构化段落 JSON
（一插件一任务键：routing 独立路由 / prompts.d 独立覆盖；生成类恒不
缓存）；未配置/失败 → 模板化拼装（按 ranges 顺序引原文+插图），绝不因
AI 不可用卡死流水线。红线（正典 §7.3）：只走 chat_policed 治理层。

**三态裁决（AI 产物裁决机制正典 2026-11-21，AI-1 批）**：成稿默认
``draft``——**AI 建议稿不是正式结果**，可用但 UI 须标「AI 建议」徽章，待
人工采纳/弃用（流水线不因未裁决卡死）；``config.auto_adopt=true`` 直接
``adopted``（旧行为，信任 AI 全自动）。``outputs.article_status`` 同步携带
该态（下游免解包 article 即可判），``article.status`` 字段级承载（契约裁决
A：status 是元数据不是类型差异，不新增包裹类型）。

产物受管（沿 frame-grab 先例）：md 落 ``ctx.artifacts_dir/<slug>.md``、
帧图复制进 ``ctx.artifacts_dir/images/``（正文以相对路径引用——产物目录
自包含可搬移），frontmatter 带 source_video/video_duration/anchor_map/
organize_mode。media_path/duration_s 是可选输入：六节点链里引擎按类型
自动接线（←local-file.file / ←transcribe.duration_s 同名就近），单测
假 ctx 不带也不失败（frontmatter 相应留空/退 max(segment.end)）。
"""
from __future__ import annotations

import bisect
import json
import re
import shutil
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginCancelled, PluginError
from musespiral.infra.ai import gateway as _ai_gateway
from musespiral.infra.ai import profiles as _ai_profiles
from musespiral.infra.ai.profiles import AIError

# 任务键（profiles.TASKS 白名单成员，2026-11-20 扩键 24→25）
TASK = "plugin_article_writer"

_MODES = ("chronological", "thematic")
_DEFAULT_MODE = "chronological"
_DEFAULT_AUTO_ADOPT = False

# 三态裁决取值（全系统统一，正典 §二；本插件只产前两态，discarded 由裁决端点写）
_STATUS_DRAFT = "draft"
_STATUS_ADOPTED = "adopted"

# AI 路径参数（生成类：温度略高于切片的稳态 0.2，恒不缓存 §八-3）
_AI_TEMPERATURE = 0.4
_AI_MAX_PROMPT_CHARS = 60_000  # 超长转写不做 AI 成稿（提示词爆预算）→ 降级模板
_SEGMENT_TEXT_CAP = 400        # 单段文本进提示词的截断（保时间戳全量）
_MAX_PARAGRAPHS = 60           # 段落数上限（防失控长文）
_MAX_ANCHORS_PER_PARA = 8      # 单段锚点上限（thematic 极端合并截断）

# 降级模板参数
_DEGRADE_WINDOW_S = 90.0       # 无 ranges 时按时间窗分组
_HEADING_CHARS = 40
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")
_ANCHOR_HREF_RE = re.compile(r"\(#t=(\d+)\)")
# 正文夹带的完整锚链接形 [文字](#t=N)——剥链保文字（复核 2026-11-20：
# AI text 自造锚会进 md 却不进 anchor_map，击穿回溯不变量）
_ANCHOR_LINK_RE = re.compile(r"\[([^\]]*)\]\(#t=\d+\)")

_SYSTEM_PROMPT = (
    "你是视频内容编辑，把带时间戳的语音转写整理成一篇可发布的图文稿。\n"
    "只输出一个 JSON 数组，不要输出数组以外的任何文字。数组每项是一个段落：\n"
    '{"heading": "段落标题，不超过40字", "text": "段落正文，用流畅书面语基于转写原文整理", '
    '"anchors": [锚点秒数...]}\n'
    "硬性要求：\n"
    "- anchors 里每个数值只能取自输入段落清单的起始秒（start 列），不得自造数值；\n"
    "- 每个段落至少一个锚点：该段核心论述开始处的转写段落起点；\n"
    "- text 只整理转写中出现的内容，不得虚构数据、结论或转写外的知识；专有名词保持原样；\n"
    "- 标注[低置信]或[疑似非语音]的段落转写质量存疑，引用其内容时用保守措辞。\n"
    "组织方式见用户消息（chronological=按视频时间顺序，每段一个锚点；"
    "thematic=按主题重组，同一概念在视频不同时间出现可合并为一段并给多个锚点）。"
)


class _AIArticleError(RuntimeError):
    """AI 路径内部失败（响应不可解析/零有效段落）——外层据此降级模板。"""


# ── 输入校验与锚点网格（不变量的执行面）────────────────────────────


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


def _validate_images(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(i, dict) for i in raw):
        raise PluginError("images 须为帧列表（[{path, ts, ...}]）")
    return raw


def _validate_ranges(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(r, dict) for r in raw):
        raise PluginError("ranges 须为区间列表（[{start, end, label, ...}]）")
    return raw


def _snap_to_start(starts: list[float], value: float) -> float:
    """任给秒数收敛到**段落起点网格**（最近起点；tie 取更早）。

    AI 给的锚点秒数不可信（惯性给中段时刻）——但「锚点=某 segment.start」
    是回溯不变量，这里统一拉回；越界值自然收敛到首/末段起点。
    """
    i = bisect.bisect_right(starts, value)
    left = starts[i - 1] if i > 0 else starts[0]
    right = starts[i] if i < len(starts) else left
    if i == 0:
        return left
    if i >= len(starts):
        return right
    return left if value - left <= right - value else right


def _fmt_hms(seconds: float) -> str:
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# 句读断点（标题优先在此截断，避免「屏幕上最显眼的是这篇论」这类半句标题）
_HEADING_BREAK = "。！？；…，、,.!?;:"


def _heading_from_text(text: str, limit: int = _HEADING_CHARS) -> str:
    """从正文首句生成标题：优先在句读处断，超长才硬截并加省略号。

    2026-11-20 真素材实测判例：降级路径原用 range 的 ``label``（正文前 32 字
    硬截）当标题，产出「## 1. 大家好,我们从第一页开始,屏幕上最显眼的是这篇论」
    ——半句断开、不可读。标题必须自己从正文里按语义断，不能复用截断的摘要。
    """
    body = text.strip()
    if not body:
        return ""
    # 第一句：在首个句读断点处断（含标点本身）
    for i, ch in enumerate(body[:limit]):
        if ch in _HEADING_BREAK and i >= 4:  # 太短的「大家好，」不成标题
            return body[: i + 1].strip()
    # 无合适句读 → 在 limit 内的最后一个空格处断（中文可能没有空格）
    head = body[:limit]
    cut = head.rfind(" ")
    if cut >= 8:
        return head[:cut].strip()
    return head.strip() + ("…" if len(body) > limit else "")


def _strip_forged_anchors(text: str) -> str:
    """剥除自由文本里夹带的 (#t=N) 锚（完整链接保文字剥链，残余裸 href 删除）。

    不变量防线（2026-11-20 复核判例）：「md 里每个 #t=NNN 都能回溯到
    segment.start」要求 md 中全部 #t= 锚**只**来自 _assemble_md 生成的
    锚点行（anchor_map 唯一权威）。AI 段落正文/标题与转写原文直引都是
    自由文本——里面自造的 ``[00:16:39](#t=999)`` 会进 md 却不进
    anchor_map，回溯链就此断裂。这里统一剥除：链接形保留内文文字只去
    链，残余裸 ``(#t=N)``（无方括号包裹）直接删。
    """
    return _ANCHOR_HREF_RE.sub("", _ANCHOR_LINK_RE.sub(r"\1", text))


def _seg_span(transcript: list[dict[str, Any]], start: float) -> tuple[float, float]:
    """锚点段落的语音窗（图片就近分配的距离基准）。"""
    for seg in transcript:
        if float(seg["start"]) == start:
            return float(seg["start"]), float(seg["end"])
    return start, start


# ── 段落内部形态：{heading, text, anchors(精确起点), span(语音窗)} ──


def _build_prompt(
    transcript: list[dict[str, Any]], ranges: list[dict[str, Any]],
    mode: str, title: str, feedback: str | None = None,
) -> list[dict[str, str]] | None:
    """组装消息；转写过长（> _AI_MAX_PROMPT_CHARS）返回 None → 降级模板。

    feedback（AI-3 重生成批 2026-11-21，可选）：自然语言改进要求拼在
    **user 消息尾部**（「用户改进要求：{feedback}」——refine_service 与
    key-segment 同款落位：指令永远在素材正文之后）。
    """
    lines: list[str] = []
    for i, seg in enumerate(transcript):
        text = str(seg.get("text") or "").strip()[:_SEGMENT_TEXT_CAP]
        logprob = seg.get("avg_logprob")
        no_speech = seg.get("no_speech_prob")
        if isinstance(no_speech, (int, float)) and float(no_speech) > 0.6:
            flags = "[疑似非语音]"
        elif isinstance(logprob, (int, float)) and float(logprob) < -1.0:
            flags = "[低置信]"
        else:
            flags = ""
        lines.append(f"{i}|{float(seg['start']):.2f}-{float(seg['end']):.2f}|{flags}{text}")
    head = f"视频标题：{title.strip()}\n" if title.strip() else ""
    if mode == "thematic":
        organization = (
            "组织方式：thematic——按主题重组，同一概念在视频不同时间出现"
            "（如第 3 分钟引入、第 25 分钟深化）合并为一段，该段 anchors 给出"
            "每个出现处的转写段落起点（多个）；段落顺序按主题逻辑排。"
        )
    else:
        organization = (
            "组织方式：chronological——按视频时间顺序组织成文（保留叙事流），"
            "段落顺序与时间轴一致，每段恰一个锚点（该段起始的转写段落起点）。"
        )
    range_note = ""
    if ranges:
        labels = "；".join(
            f"{float(r['start']):.0f}s-{float(r['end']):.0f}s {str(r.get('label') or '').strip()}"
            for r in ranges[:20]
        )
        range_note = f"\n上游选出的关键片段（可作结构参考，锚点仍须取转写段落起点）：{labels}"
    user = (
        f"{head}{organization}{range_note}\n"
        f"转写段落（序号|起始秒-结束秒|文本）：\n" + "\n".join(lines)
    )
    if feedback and feedback.strip():
        user = f"{user}\n\n用户改进要求：{feedback.strip()}"
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
            raise _AIArticleError("回复中找不到 JSON 数组")
        candidate = text[i:j + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise _AIArticleError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, list):
        raise _AIArticleError("JSON 顶层不是数组")
    return data


def _ai_paragraphs(
    transcript: list[dict[str, Any]], ranges: list[dict[str, Any]],
    mode: str, title: str, ctx: Any, feedback: str | None = None,
) -> list[dict[str, Any]]:
    """AI 成稿：一次调用返段落 JSON → 锚点收敛到段落起点网格。"""
    messages = _build_prompt(transcript, ranges, mode, title, feedback)
    if messages is None:
        ctx.log("article-writer: 转写过长超 AI 提示词上限，降级模板拼装")
        raise _AIArticleError("prompt 超限")
    res = _ai_gateway.chat_policed(
        messages, task=TASK, temperature=_AI_TEMPERATURE, cache=False,
        run_id=(getattr(ctx, "run_id", "") or None),
    )
    starts = sorted(float(s["start"]) for s in transcript)
    out: list[dict[str, Any]] = []
    dropped = 0
    for item in _extract_json_array(str(res.get("text") or "")):
        if not isinstance(item, dict):
            dropped += 1
            continue
        text = str(item.get("text") or "").strip()
        heading = str(item.get("heading") or "").strip()[:_HEADING_CHARS]
        raw_anchors = item.get("anchors")
        anchors: list[float] = []
        if isinstance(raw_anchors, list):
            for v in raw_anchors:
                try:
                    anchors.append(_snap_to_start(starts, float(v)))
                except (TypeError, ValueError):
                    continue
        anchors = sorted(dict.fromkeys(anchors))[:_MAX_ANCHORS_PER_PARA]
        if not text or not anchors:  # 无正文或锚点全非法 → 不可回溯的段落直接弃
            dropped += 1
            continue
        spans = [_seg_span(transcript, a) for a in anchors]
        out.append({
            "heading": heading,
            "text": text,
            "anchors": anchors,
            "span": (min(s for s, _ in spans), max(e for _, e in spans)),
        })
    if not out:
        raise _AIArticleError("AI 返回零个有效段落")
    if len(out) > _MAX_PARAGRAPHS:
        ctx.log(f"article-writer: AI 返回 {len(out)} 段超上限，裁断为 {_MAX_PARAGRAPHS}")
        out = out[:_MAX_PARAGRAPHS]
    if dropped:
        ctx.log(f"article-writer: 丢弃 {dropped} 个无正文/锚点无法收敛的段落（AI 返回）")
    if mode == "chronological":  # 时间顺序模式强制段落按时间排（AI 乱序拉回）
        out.sort(key=lambda p: p["span"][0])
    return out


def _degrade_paragraphs(
    transcript: list[dict[str, Any]], ranges: list[dict[str, Any]], ctx: Any
) -> list[dict[str, Any]]:
    """降级模板：按 ranges 顺序引原文（无 ranges 按时间窗分组）；恒 chronological。

    只做确定性拼装：段落=范围/时间窗，正文=覆盖区间内转写原文直引
    （「每段引原文」），锚点=起点收敛到段落网格——时间戳锚与 AI 路径
    同一不变量，成稿质量让位于可回溯性。
    """
    starts = sorted(float(s["start"]) for s in transcript)
    groups: list[tuple[float, float, str]] = []  # (start, end, heading)
    if ranges:
        for r in sorted(ranges, key=lambda r: float(r["start"])):
            groups.append((
                float(r["start"]), float(r["end"]),
                str(r.get("label") or "").strip()[:_HEADING_CHARS],
            ))
    else:
        cur_start: float | None = None
        cur_end = 0.0
        for seg in transcript:
            s, e = float(seg["start"]), float(seg["end"])
            if cur_start is None:
                cur_start, cur_end = s, e
            elif s - cur_start >= _DEGRADE_WINDOW_S:
                groups.append((cur_start, cur_end, ""))
                cur_start, cur_end = s, e
            else:
                cur_end = max(cur_end, e)
        if cur_start is not None:
            groups.append((cur_start, cur_end, ""))
    out: list[dict[str, Any]] = []
    for g_start, g_end, label in groups:
        # **保留转写段落边界**（2026-11-20 真素材判例）：原用 "".join() 把整段
        # 转写拼成一个 300+ 字的巨块（实测第 4 段「上一页我们学会了…翻到第七页」
        # 一字不换行，不可读）。转写 segment 本身就是「一句话/一个意群」的边界，
        # 用它分段最忠实原音——每段之间空行，markdown 渲染成独立段落。
        covered = [
            s for s in transcript
            if float(s["end"]) > g_start and float(s["start"]) < g_end
        ]
        body_lines = [str(s.get("text") or "").strip() for s in covered]
        body = "\n\n".join(ln for ln in body_lines if ln).strip()
        if not body:  # 覆盖区间无转写文本（如 AI ranges 幻觉区）→ 弃段不虚构
            continue
        anchor = _snap_to_start(starts, g_start)
        # 标题优先级（2026-11-20 真素材判例校正，两轮）：
        #   ① label 若**不是正文前缀**（即上游提炼过的短语，如「列式存储」）→ 用它
        #   ② label 恰是正文前缀（= 从正文硬截的片段）→ 从正文按句读重生成
        #   ③ 都不可用 → 时间戳标题
        # 为什么用「是否正文前缀」判：长度判据不可靠——实测 label 被截到 32 字
        # （= _HEADING_CHARS-8），既没顶到 40 上限也不带省略号，长度判断不出它是
        # 半句（「…屏幕上最显眼的是这篇论」）。前缀判据是确定的：截断产物必然
        # 是正文的开头子串，而提炼短语不会。
        label_is_prefix = bool(label) and body.replace("\n", " ").startswith(label.replace(" ", ""))
        heading = (
            (_heading_from_text(body) or label)
            if (not label or label_is_prefix)
            else label
        )
        if not heading:
            heading = f"{_fmt_hms(anchor)} 起"
        out.append({
            "heading": heading, "text": body, "anchors": [anchor],
            "span": (g_start, g_end),
        })
    return out


# ── 图片分配与 markdown 组装 ───────────────────────────────────────


def _assign_images(
    paragraphs: list[dict[str, Any]], images: list[dict[str, Any]], ctx: Any
) -> dict[int, list[dict[str, Any]]]:
    """每帧归给「锚点语音窗距 ts 最近」的段落（tie 取更早段）。

    关键帧按 ts 插入对应段落之后（正典 §9.3）——ts 与段落锚点的语音窗
    距离是确定性判据；无段落（空稿）时帧全部落空（日志留痕）。
    """
    assigned: dict[int, list[dict[str, Any]]] = {}
    for img in images:
        path = str(img.get("path") or "").strip()
        try:
            ts = float(img.get("ts"))
        except (TypeError, ValueError):
            ctx.log(f"article-writer: 帧缺有效 ts 跳过: {path!r}")
            continue
        best, best_dist = -1, None
        for idx, para in enumerate(paragraphs):
            s, e = para["span"]
            dist = max(s - ts, ts - e, 0.0)
            if best_dist is None or dist < best_dist:
                best, best_dist = idx, dist
        if best < 0:
            ctx.log("article-writer: 无可用段落，帧未插入")
            return assigned
        assigned.setdefault(best, []).append({"path": path, "ts": ts})
    return assigned


def _slugify(title: str) -> str:
    slug = _SLUG_RE.sub("-", title.strip()).strip("-")
    return slug[:80] or "article"


def _frontmatter(fields: dict[str, Any], anchor_map: dict[str, list[float]]) -> str:
    """极简 YAML frontmatter（字符串走 JSON 双引号形——合法 YAML 标量）。"""
    lines = ["---"]
    for key in ("title", "source_video", "organize_mode"):
        lines.append(f"{key}: {json.dumps(str(fields.get(key) or ''), ensure_ascii=False)}")
    lines.append(f"video_duration: {float(fields.get('video_duration') or 0.0):g}")
    lines.append("anchor_map:")
    for key in sorted(anchor_map, key=lambda k: int(k[1:])):
        vals = ", ".join(f"{v:g}" for v in anchor_map[key])
        lines.append(f"  {key}: [{vals}]")
    lines.append("---")
    return "\n".join(lines)


def _assemble_md(
    fields: dict[str, Any],
    paragraphs: list[dict[str, Any]],
    image_plan: dict[int, list[dict[str, Any]]],
    rel_images: dict[str, str],
    source_ranges: dict[float, dict[str, Any]],
) -> tuple[str, dict[str, list[float]]]:
    """frontmatter + 正文（锚行 + 正文 + 帧图/图注）；返回 (md, anchor_map)。"""
    anchor_map: dict[str, list[float]] = {}
    body: list[str] = []
    for p_idx, para in enumerate(paragraphs):  # 0 基（image_plan 键同基，勿错位）
        idx = p_idx + 1
        key = f"p{idx}"
        anchor_map[key] = para["anchors"]
        body.append(f"## {idx}. {_strip_forged_anchors(para['heading'])}".rstrip())
        anchor_links = " ".join(
            f"[{_fmt_hms(a)}](#t={int(round(a))})" for a in para["anchors"]
        )
        body.append(f"{anchor_links} {_strip_forged_anchors(para['text'])}".rstrip())
        for img in image_plan.get(p_idx, []):  # 同 0 基键（复核：曾用 idx 错位）
            rel = rel_images.get(img["path"])
            if not rel:
                continue
            body.append(f"![]({rel})")  # 正典 §9.3 形态：裸引用（时间戳在图注）
            src = source_ranges.get(img["ts"]) or {}
            s, e = src.get("start"), src.get("end")
            caption = f"图：{_fmt_hms(img['ts'])} 处关键帧"
            if isinstance(s, (int, float)) and isinstance(e, (int, float)):
                caption += f"（来源片段 {_fmt_hms(float(s))}–{_fmt_hms(float(e))}）"
            body.append(f"*{caption}*")
        body.append("")
    md = _frontmatter(fields, anchor_map) + "\n\n" + "\n".join(body).rstrip() + "\n"
    return md, anchor_map


# ── 入口 ───────────────────────────────────────────────────────────


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    transcript = _validate_transcript(inputs.get("transcript"))
    images = _validate_images(inputs.get("images"))
    ranges = _validate_ranges(inputs.get("ranges"))
    # title 进 frontmatter（md 的一部分）——同样剥除夹带锚，保「md 内全部
    # #t= 锚都出自 anchor_map」不变量
    title = _strip_forged_anchors(str(inputs.get("title") or "").strip()).strip()
    media = str(inputs.get("media_path") or "").strip()
    try:
        wired_duration = float(inputs.get("duration_s") or 0.0)
    except (TypeError, ValueError):
        wired_duration = 0.0

    mode = str(config.get("mode") or _DEFAULT_MODE).strip().lower()
    if mode not in _MODES:
        raise PluginError(f"config.mode 非法: {mode!r}（可选 {' / '.join(_MODES)}）")
    # 三态裁决开关：引擎 _merge_config 只填 default 不深校验——布尔性插件自验
    auto_adopt = config.get("auto_adopt", _DEFAULT_AUTO_ADOPT)
    if not isinstance(auto_adopt, bool):
        raise PluginError(f"config.auto_adopt 须为布尔: {auto_adopt!r}")
    # feedback（AI-3 重生成批）：引擎 regenerate_node 注入的自然语言改进
    # 要求——只进 AI 路径的 prompt 尾部；非字符串拒（宁失败不静默丢弃）
    feedback = config.get("feedback")
    if feedback is not None and not isinstance(feedback, str):
        raise PluginError(f"config.feedback 须为字符串: {feedback!r}")

    actual_mode = mode
    paragraphs: list[dict[str, Any]]
    if not transcript:
        # 空转写=真实结果（key-segment 先例）：不进 AI（空锚网格无 snap 可言），
        # 产出仅含 frontmatter 的空稿——流水线继续走，sink 照常落库
        ctx.log("article-writer: 转写为空，产出仅含 frontmatter 的空稿（锚图空）")
        paragraphs = []
    elif _ai_profiles.ai_configured(TASK):
        try:
            paragraphs = _ai_paragraphs(transcript, ranges, mode, title, ctx, feedback=feedback)
            ctx.log(
                f"article-writer: AI 成稿 {len(paragraphs)} 段（task={TASK}, mode={mode}）"
            )
        except (AIError, _AIArticleError) as exc:
            # 治理层整链失败面 = AIError 族；_AIArticleError 是回复解析失败。
            # 两者皆降级模板——AI 是增强不是依赖。降级只支持 chronological。
            actual_mode = "chronological"
            ctx.log(f"article-writer: AI 成稿不可用（{exc}），降级模板拼装（chronological）")
            if feedback:
                ctx.log("article-writer: 改进要求只在 AI 路径生效，本次降级模板未消费")
            paragraphs = _degrade_paragraphs(transcript, ranges, ctx)
    else:
        if mode == "thematic":
            actual_mode = "chronological"
            ctx.log("article-writer: AI 未配置，thematic 降级为 chronological 模板拼装")
        else:
            ctx.log("article-writer: AI 未配置，走模板拼装（ranges 顺序引原文）")
        if feedback:
            ctx.log("article-writer: 改进要求只在 AI 路径生效，本次模板拼装未消费")
        paragraphs = _degrade_paragraphs(transcript, ranges, ctx)

    # 图片复制进受管 artifacts（产物目录自包含）+ 引用相对路径表
    artifacts = Path(ctx.artifacts_dir)
    rel_images: dict[str, str] = {}
    if images:
        img_dir = artifacts / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        for img in images:
            src = Path(str(img.get("path") or "").strip())
            if not src.is_file():
                ctx.log(f"article-writer: 帧文件缺失跳过: {src}")
                continue
            dst = img_dir / src.name
            if not dst.exists():  # 同名帧（同 ts 重抽）零重写
                shutil.copyfile(src, dst)
            rel_images[src.as_posix()] = f"images/{src.name}"
            rel_images[str(src)] = f"images/{src.name}"

    source_ranges: dict[float, dict[str, Any]] = {}
    for img in images:
        try:
            source_ranges[float(img.get("ts"))] = dict(img.get("source_range") or {})
        except (TypeError, ValueError):
            continue

    image_plan = _assign_images(paragraphs, images, ctx)
    duration = wired_duration if wired_duration > 0 else (
        max((float(s["end"]) for s in transcript), default=0.0)
    )
    fields = {
        "title": title or "视频图文稿",
        "source_video": media,
        "organize_mode": actual_mode,
        "video_duration": round(duration, 3),
    }
    md, anchor_map = _assemble_md(fields, paragraphs, image_plan, rel_images, source_ranges)

    slug = _slugify(title)
    out_path = artifacts / f"{slug}.md"
    if ctx.cancelled:  # 落盘前自查点（长 AI 调用后）
        raise PluginCancelled("article-writer 在落盘前自查点收到取消信号")
    out_path.write_text(md, encoding="utf-8")
    total_anchors = sum(len(v) for v in anchor_map.values())
    ctx.log(
        f"article-writer: 稿件落盘 {out_path.name}（{len(paragraphs)} 段 / "
        f"{total_anchors} 锚 / mode={actual_mode}）"
    )
    # 三态裁决（AI-1 批）：成稿默认是「建议」不是「正式结果」——draft 不禁用
    # 下游（流水线不卡），状态在场，用户想审查时随手可裁决。
    status = _STATUS_ADOPTED if auto_adopt else _STATUS_DRAFT
    if status == _STATUS_DRAFT:
        ctx.log("article-writer: AI 建议成稿（draft 态，待人工裁决）")
    else:
        ctx.log("article-writer: 成稿直接 adopted（auto_adopt=true）")
    ctx.progress(1, 1)
    article = {
        **fields,
        "path": str(out_path),
        "slug": slug,
        "anchor_map": anchor_map,
        "paragraphs": len(paragraphs),
        "anchors": total_anchors,
        "status": status,
    }
    # article_status：顶层直判面（下游免解包 article）；manifest.outputs 不
    # 新增端口——status 是元数据不是类型差异（契约裁决 A），引擎对额外输出
    # 键自然放行（req 13），不爆炸契约矩阵。
    return {"article": article, "anchor_map": anchor_map, "article_status": status}


__all__ = ["TASK", "run"]
