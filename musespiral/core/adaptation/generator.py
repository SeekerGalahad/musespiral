"""一稿多态生成器（F14）——generate 主链 + AI 调用 + 无 AI 启发式降级。

自 musespiral/adapt.py 拆出（后端架构正典 §八 Phase B）：平台规格在
specs.py（PLATFORM_SPECS），本地字数校验在 validator.py（_validate）；
AI 经 infra.ai.chat 统一入口（Domain→Infra 向下依赖，正典 §2.2 规则1；
不经顶层 musespiral/ai.py 过渡垫片，垫片 Phase C 拆除不受影响）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import frontmatter

from ..habits import load_habits
from ...infra.ai.habits_inject import load_style_samples, style_block
from ...infra.ai import ai_configured, chat
from ..article import body_sha1
from .specs import PLATFORM_SPECS
from .validator import _validate

# 小红书正文字数上限（含话题行），启发式按实际拼接长度（含空行分隔符）逐行预算
_XHS_MAX_CHARS = 1000

# 启发式要点行的 emoji 轮换
_POINT_EMOJI = ("✅", "💡", "🔥", "📌", "⭐️")

# 启发式小红书的固定互动引导句
_INTERACTION = "你有不同的做法吗？评论区聊聊 😉 也欢迎收藏慢慢看～"

# 小节标题行（## ~ ######，不含文档主标题 #）
_HEADING_RE = re.compile(r"^#{2,6}\s+(.+?)\s*$")

# 句末标点（全角 + 半角），用于切句
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")

# 小红书话题行：话题：#标签1 #标签2
_TOPIC_LINE_RE = re.compile(r"^\s*话题\s*[:：]\s*(.+?)\s*$")


class AdaptNeedsAI(RuntimeError):
    """口播稿平台（channels/douyin）在 AI 未配置时无可靠启发式降级。"""


def generate(
    article_path: str | Path,
    platform: str,
    out_path: str | Path | None = None,
) -> Path:
    """把文章改写为指定平台的文字变体，写盘并返回输出路径。

    输出默认 <stem>.<platform>.md（与源文件同目录），存在即拒绝覆盖；
    可用 out_path 显式指定。AI 已配置走 PLATFORM_SPECS 提示词（小红书的
    话题行解析进 frontmatter tags），未配置时 xhs/zhihu 启发式降级、
    channels/douyin 抛 AdaptNeedsAI。
    """
    spec = PLATFORM_SPECS.get(platform)
    if spec is None:
        raise ValueError(f"未知平台: {platform}（可选: {'/'.join(PLATFORM_SPECS)}）")
    src = Path(article_path)
    dst = Path(out_path) if out_path is not None else src.with_name(f"{src.stem}.{platform}.md")
    if dst.exists():
        raise FileExistsError(f"目标文件已存在，拒绝覆盖: {dst}")

    post = frontmatter.load(src)
    src_tags = _source_tags(post)
    if ai_configured("adapt"):
        content = _chat(platform, post.content.strip())
        # 小红书：话题行留在正文（便于整篇粘贴），同时解析进 frontmatter tags
        tags = (_parse_topic_tags(content) or src_tags) if platform == "xhs" else src_tags
    elif spec["heuristic"]:
        content = _heuristic_dispatch(post, platform, src_tags)
        tags = src_tags
    else:
        raise AdaptNeedsAI(
            f"{platform}（{spec['label']}）口播稿没有可靠的启发式降级，需先配置 AI。"
            "请先运行: musespiral ai config --base-url <URL> --api-key <KEY> --model <MODEL>"
        )

    title = str(post.metadata.get("title") or src.stem)
    warnings = _validate(platform, content)
    # R3 回链：产物记源文件名 + 源正文哈希——源改名后探测端经 source 仍命中，
    # 源正文变更可经 sha1 漂移识别（旧产物无这两键不受影响）
    new_post = frontmatter.Post(
        content,
        title=title,
        platform=platform,
        tags=tags,
        source=src.name,
        source_sha1=body_sha1(post.content),
    )
    if warnings:
        # 只告警不拒绝：产物仍落盘走人审，警告同时写进 frontmatter 供 CLI
        # 提示与后续 pack 消费（第五期复核 low 修复：prompt 约束之外的本地兜底）
        new_post.metadata["adapt_warnings"] = warnings
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(frontmatter.dumps(new_post, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dst


def _chat(platform: str, body: str) -> str:
    """平台改写调用（F25 起经 infra.ai.chat 统一入口，task=adapt 走任务路由）。"""
    messages: list[dict[str, str]] = []
    # F44 style 块（habits+范文现读现拼；两资产全无 → 空串不注入）
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        messages.append({"role": "system", "content": style_section})
    messages.append({"role": "user", "content": PLATFORM_SPECS[platform]["prompt"].format(body=body)})
    return _strip_code_fence(chat(messages, task="adapt", temperature=0.6))


def _strip_code_fence(text: str) -> str:
    """去掉 AI 回复外层整体包裹的代码围栏（如有）。"""
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return text


def _source_tags(post: frontmatter.Post) -> list[str]:
    """源文章 frontmatter tags 规整为字符串列表（去 # 前缀与空白）。"""
    raw = post.metadata.get("tags", [])
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).strip().lstrip("#") for t in raw if str(t).strip()]


def _parse_topic_tags(content: str) -> list[str]:
    """解析小红书正文中的「话题：#a #b」行为标签列表（≤6 个）；无该行返回空列表。"""
    for line in content.splitlines():
        m = _TOPIC_LINE_RE.match(line)
        if m:
            tags = [t.lstrip("#").strip() for t in m.group(1).split()]
            return [t for t in tags if t][:6]
    return []


def _sections(content: str) -> list[tuple[str, list[str]]]:
    """按 ##/### 小标题切段（跳过代码围栏），返回 (标题文本, 节内正文行) 列表。"""
    out: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    in_fence = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING_RE.match(line)
        if m:
            if current is not None:
                out.append(current)
            current = (m.group(1), [])
        elif current is not None:
            s = line.strip()
            if s and not s.startswith(("#", "!", ">", "|", "---")):
                current[1].append(s)
    if current is not None:
        out.append(current)
    return out


def _lead_text(content: str) -> str:
    """首个小标题之前的正文（跳过文档主标题/图片/引用等，行拼接）。"""
    out: list[str] = []
    for raw in content.splitlines():
        s = raw.strip()
        if _HEADING_RE.match(s):
            break
        if s and not s.startswith(("#", "!", ">", "|", "---", "```")):
            out.append(s)
    return "".join(out)


def _first_sentences(text: str, n: int) -> list[str]:
    """取前 n 个完整句（按句末标点切分；无标点时整段作一句）。"""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return parts[:n]


def _first_sentence(text: str) -> str:
    return "".join(_first_sentences(text, 1))


def _heuristic_xhs(post: frontmatter.Post, tags: list[str]) -> str:
    """无 AI 的小红书降级：首句做钩子 + 小标题组要点拼装 + 互动引导 + 话题行。

    预算按实际拼接长度（含空行分隔符）逐行校验：要点行装不下即停，话题行
    标签逐个试装、装不下即弃，保证全文（含话题行）不超过 1000 字。
    """
    hook = _first_sentence(_lead_text(post.content)) or str(post.metadata.get("title") or "")
    if len(hook) > 80:  # 与要点同宽截断，钩子过长时不挤占互动句与话题行预算
        hook = hook[:79] + "…"
    lines: list[str] = []
    if hook:
        lines.append(f"{hook} 🔥")
    for i, (heading, paras) in enumerate(_sections(post.content)):
        point = _first_sentence("".join(paras)) or heading
        if len(point) > 80:
            point = point[:79] + "…"
        line = f"{_POINT_EMOJI[i % len(_POINT_EMOJI)]} {heading}：{point}"
        # 以「已保留行 + 该要点 + 固定互动句」的实际拼接长度做预算，装不下即停
        if len("\n\n".join([*lines, line, _INTERACTION])) > _XHS_MAX_CHARS:
            break
        lines.append(line)
    lines.append(_INTERACTION)
    if tags:
        # 话题行最后装填：标签逐个试装（≤6 个），装不下即弃，严守全文 1000 字
        kept: list[str] = []
        for t in tags[:6]:
            topic = "话题：" + " ".join(f"#{x}" for x in [*kept, t])
            if len("\n\n".join([*lines, "", topic])) > _XHS_MAX_CHARS:
                break
            kept.append(t)
        if kept:
            lines += ["", "话题：" + " ".join(f"#{x}" for x in kept)]
    return "\n\n".join(lines)


def _heuristic_zhihu(post: frontmatter.Post) -> str:
    """无 AI 的知乎降级：先说结论 + 小标题组要点拼装（每节取前两句）+ 结尾讨论问题。"""
    title = str(post.metadata.get("title") or "这个话题")
    lead = _first_sentence(_lead_text(post.content)) or title
    parts = [f"先说结论：{lead}", ""]
    for heading, paras in _sections(post.content)[:8]:
        parts.append(f"## {heading}")
        points = _first_sentences("".join(paras), 2)
        if points:
            parts.append("".join(points))
        parts.append("")
    parts.append(f"讨论：你在「{title}」上踩过哪些坑，最后是怎么解决的？")
    return "\n".join(parts).strip()


def _heuristic_dispatch(
    post: frontmatter.Post, platform: str, tags: list[str]
) -> str:
    """启发式降级分派（specs.heuristic=True 的平台各归各的形态实现）。"""
    if platform == "xhs":
        return _heuristic_xhs(post, tags)
    if platform == "juejin":
        return _heuristic_juejin(post)
    if platform == "bilibili":
        return _heuristic_bilibili(post)
    return _heuristic_zhihu(post)


# 公众号话术行（启发式降级统一剔除：跨平台搬运不再带「点击关注」类残句）
_MP_CTA_KEYS = ("点击关注", "在看", "关注公众号", "点赞收藏")

# B站专栏小标题 emoji 轮换（「适度节制」：隔段点缀一个）
_BILI_EMOJI = ("✨", "💡", "🍵", "🎯")


def _heuristic_juejin(post: frontmatter.Post) -> str:
    """无 AI 的掘金降级：技术长文轻改——保留全部代码块与小节结构（逐行原样
    保留，含围栏内内容），只换导语、剔公众号话术行（F42 §二-2.2 变体规则）。"""
    lead = _first_sentence(_lead_text(post.content)) or str(
        post.metadata.get("title") or ""
    )
    out = [f"导语：{lead}", ""]
    in_fence = False
    for raw in post.content.splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(raw)
            continue
        if not in_fence:
            s = line.strip()
            if s.startswith("# "):
                continue  # 文档 H1 主标题不重复（导语已点题）；## 小节保留
            if s and any(k in s for k in _MP_CTA_KEYS):
                continue  # 公众号话术行剔除
        out.append(raw)
    return "\n".join(out).strip()


def _heuristic_bilibili(post: frontmatter.Post) -> str:
    """无 AI 的B站降级：亲和化——口语导语 + 小标题组要点（每节前两句）+
    emoji 隔段节制点缀 + 结尾互动引导（F42 §二-2.2 变体规则）。"""
    title = str(post.metadata.get("title") or "这个话题")
    lead = _first_sentence(_lead_text(post.content)) or title
    parts = [f"聊聊{title}～ {lead} 😊", ""]
    for i, (heading, paras) in enumerate(_sections(post.content)[:8]):
        emoji = _BILI_EMOJI[(i // 2) % len(_BILI_EMOJI)] if i % 2 == 0 else ""
        suffix = f" {emoji}" if emoji else ""
        parts.append(f"## {heading}{suffix}".rstrip())
        points = _first_sentences("".join(paras), 2)
        if points:
            parts.append("".join(points))
        parts.append("")
    parts.append("你有类似的经历吗？评论区聊聊～")
    return "\n".join(parts).strip()
