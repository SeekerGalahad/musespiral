"""选题推荐器（F28）——规则生成 series/series_no/reason/skeleton_hint，AI 增强可选。

L2 Domain：规则路径纯函数零 I/O；AI 增强走 infra.ai 统一 chat 入口（可选、
懒加载——未调用 ai_enhance 时本模块不触碰 infra，纯路径可完全单测）。

规则（F28 表，确定性）：
  源码分析 → 「为什么 [项目名] 这么 [特性]？——[核心技术] 深度解析」
  模式家族 → 「N 个开源项目怎么解决同一个问题：[模式名]」
  学习路径 → 「[技术名] 入门指南：从零到一」

series_no 在批内按评分降序分配（同系列从 existing_counts 起连续编号），
existing_counts 是 service 从 discovered.json 统计的各系列已荐数——
suggester 本身不读状态，纯函数可测。
"""
from __future__ import annotations

import re
from typing import Callable

# ── 系列命名（F26 系列标签）──────────────────────────────────────────
SERIES_SOURCE_ANALYSIS = "开源项目深度解读"
SERIES_FAMILY = "跨仓模式拆解"
SERIES_LEARNING = "学习路径入门"
SERIES_DEFAULT = "知识库精选"

# ── 特性推断词表（F28：特性从 tags/标题推断——快速/流行/优雅/可靠）────
_TRAIT_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("高性能", "快速", "性能", "低延迟", "高并发", "并发", "fast"), "快"),
    (("安全", "可靠", "稳定", "容错", "secure"), "可靠"),
    (("优雅", "简洁", "简单", "轻量", "易用"), "优雅"),
]

# 设计思维四问关键词（AGENTS §3 四问；h3 标题内逐个探测）
_FOUR_QUESTIONS = ("关键权衡", "演进推断", "同域对照", "可迁移决策")

_NUM_PREFIX_RE = re.compile(r"^\d+(?:\.\d+)?[、.．\s]*")


def _subsections(meta: dict) -> list[str]:
    """全部 h3+ 标题（四问小节是「### 5.1 关键权衡」形态）。"""
    return [t for lvl, t in (meta.get("headings") or []) if lvl >= 3]


def _section_subtitles(meta: dict, keyword: str) -> list[str]:
    """首个含 keyword 的二级章节之下、下一个二级章节之前的小节标题。"""
    headings = meta.get("headings") or []
    out: list[str] = []
    in_sec = False
    for lvl, title in headings:
        if lvl <= 2:
            in_sec = keyword in title
            continue
        if in_sec:
            out.append(title)
    return out


def is_family_card(meta: dict) -> bool:
    """模式家族族卡（title 前缀「模式家族-」——族卡生成器固定形态）。"""
    return str(meta.get("title") or "").startswith("模式家族-")


def series_for(meta: dict) -> str:
    """系列名规则：族卡优先（category 同为 13-source-analysis，须先分叉）。"""
    if is_family_card(meta):
        return SERIES_FAMILY
    category = str(meta.get("category") or "")
    if category == "13-source-analysis":
        return SERIES_SOURCE_ANALYSIS
    if category == "11-learning-path":
        return SERIES_LEARNING
    return SERIES_DEFAULT


def _project_name(meta: dict) -> str:
    """源码分析项目名：title 去「源码分析-」前缀（无前缀原样）。"""
    title = str(meta.get("title") or "")
    return re.sub(r"^源码分析-", "", title, count=1) or title


def _trait(meta: dict) -> str:
    """特性推断：tags+title 命中词表 → 快/可靠/优雅；缺省「流行」。"""
    haystack = str(meta.get("title") or "") + " " + " ".join(meta.get("tags") or [])
    for keys, trait in _TRAIT_KEYWORDS:
        if any(k in haystack for k in keys):
            return trait
    return "流行"


def _core_tech(meta: dict) -> str:
    """核心技术：首个「核心模块分析」章节的首个小节标题（去编号前缀）。

    回退链：核心模块小节 → 核心模块章节标题（去序号与括注）→ 架构总览
    章节标题 → 「<项目名> 核心设计」。
    """
    subs = _section_subtitles(meta, "核心模块")
    if subs:
        return _NUM_PREFIX_RE.sub("", subs[0]).strip() or subs[0]
    for keyword in ("核心模块", "架构总览"):
        for s in meta.get("sections") or []:
            if keyword in s:
                t = re.sub(r"^[一二三四五六七八九十\d]+[、.．\s]*", "", s)
                t = re.sub(r"[（(].*?[)）]", "", t).strip("：: ")
                if t:
                    return t
    return f"{_project_name(meta)} 核心设计"


def skeleton_hint_for(meta: dict) -> str:
    """F28 规则生成选题角度（三形态）。"""
    if is_family_card(meta):
        name = re.sub(r"^模式家族-", "", str(meta.get("title") or ""), count=1)
        n = meta.get("family_repos")
        count = f"{n} " if isinstance(n, int) and n > 0 else "多"
        return f"{count}个开源项目怎么解决同一个问题：{name}"
    category = str(meta.get("category") or "")
    if category == "11-learning-path":
        tech = re.sub(r"^\d+-", "", str(meta.get("title") or ""), count=1)
        if "入门指南" in tech:
            return f"{tech}：从零到一"
        return f"{tech} 入门指南：从零到一"
    # 源码分析 / 其他长文
    name = _project_name(meta)
    return f"为什么 {name} 这么{_trait(meta)}？——{_core_tech(meta)} 深度解析"


def reason_for(meta: dict, scored: dict) -> str:
    """推荐理由：从评分命中的事实拼装（无事实时给出保底描述）。"""
    parts: list[str] = []
    lines = int(meta.get("line_count") or 0)
    if lines >= 100:
        parts.append(f"{lines} 行深度内容")
    sections = meta.get("sections") or []
    subs = _subsections(meta)
    q_hits = sum(1 for q in _FOUR_QUESTIONS if any(q in s for s in subs))
    if q_hits >= len(_FOUR_QUESTIONS):
        parts.append("四问齐全")
    elif any("设计思维提炼" in s for s in sections):
        parts.append("含设计思维提炼章节")
    if any("端到端数据流" in s for s in sections):
        parts.append("含端到端数据流")
    matched = scored.get("matched") or {}
    if matched.get("owner_repo"):
        parts.append(f"可追溯 {matched['owner_repo']}")
    fams = matched.get("families") or []
    if fams:
        parts.append("模式家族成员（" + "、".join(fams[:2]) + "）")
    tags = meta.get("tags") or []
    if len(tags) >= 4:
        parts.append(f"tags {len(tags)} 项")
    if not parts:
        parts.append("知识库候选")
    return "，".join(parts)


def suggest_batch(
    items: list[dict],
    existing_counts: dict[str, int] | None = None,
) -> list[dict]:
    """批内为每个 (meta, scored) 项生成 series/series_no/reason/skeleton_hint。

    items：[{"meta": {...}, "score": int, ...scored}]（service 组装的评分项）；
    返回与输入等长同序的建议列表 [{"series", "series_no", "reason",
    "skeleton_hint"}]。series_no 分配规则：整批按 (-score, path) 排序后
    逐个入列，同系列从 existing_counts.get(series, 0)+1 连续编号——
    评分并列按 path 字典序，全链确定性。
    """
    counts = dict(existing_counts or {})
    assigns: dict[int, dict] = {}
    order = sorted(
        range(len(items)),
        key=lambda i: (-int(items[i].get("score") or 0), str((items[i].get("meta") or {}).get("path") or "")),
    )
    for i in order:
        meta = items[i].get("meta") or {}
        scored = items[i]
        series = series_for(meta)
        counts[series] = counts.get(series, 0) + 1
        assigns[i] = {
            "series": series,
            "series_no": counts[series],
            "reason": reason_for(meta, scored),
            "skeleton_hint": skeleton_hint_for(meta),
        }
    return [assigns[i] for i in range(len(items))]


# ── AI 增强（可选）────────────────────────────────────────────────────


def ai_enhance(
    items: list[dict],
    top_n: int = 5,
    *,
    chat: Callable[..., str] | None = None,
) -> int:
    """对 top-N 候选调用 AI 生成 ai_title/ai_intro（F28 --ai），返回增强数。

    items：最终候选 dict（含 title/skeleton_hint/path/score，service 已按
    分数降序）；chat 缺省懒加载 infra.ai 统一入口（测试注入 mock）。
    单项失败（AIError/响应空）跳过不中断——建议列表始终完整返回，
    只是该项不带 ai_* 字段。
    """
    from ...infra.ai import AIError  # 可选依赖，懒加载（纯规则路径不触发）
    from ...infra.ai.habits_inject import load_style_samples, style_block
    from ..habits import load_habits

    if chat is None:
        from ...infra.ai import chat as _chat

        chat = _chat
    # F44 style 块：追加在任务指令之后（system prompt 内「你是资深技术公众号编辑。」
    # 之后的「写作风格要求（个人风格档案）」一节）；两资产全无 → 空串不追加
    system = "你是资深技术公众号编辑。"
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        system = f"{system}\n\n{style_section}"
    enhanced = 0
    for item in items[:top_n]:
        prompt = (
            "基于这篇技术分析的核心发现，生成一个吸引人的公众号文章标题和 100 字导语。"
            f"源文档标题：{item.get('title')}\n"
            f"已有选题角度参考：{item.get('skeleton_hint')}\n"
            "输出两行：第一行为标题（不加引号），第二行为导语。"
        )
        try:
            reply = chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ]
            )
        except AIError:
            continue
        lines = [ln.strip() for ln in str(reply or "").splitlines() if ln.strip()]
        if len(lines) >= 2:
            item["ai_title"] = lines[0]
            item["ai_intro"] = lines[1]
            enhanced += 1
    return enhanced
