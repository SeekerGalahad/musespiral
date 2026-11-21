"""骨架生成器（F29）——组装含 frontmatter + source 溯源 + 章节来源注释的骨架全文。

L2 Domain 纯逻辑：输入元数据与选题建议，输出 Markdown 全文字符串（零 I/O、
零时间戳——同输入恒同输出，可完全单测）。落盘（O_EXCL 拒绝覆盖）归
services/discover_service.py。

无 AI 配置形态：骨架只含结构 + 注释提示（正典 F29——内容填充由人工或
后续 rewrite 完成），每个章节下方 HTML 注释标注「来源章节 + 提取规则」，
rewrite 时 AI 可理解上下文。
"""
from __future__ import annotations

import re

DEFAULT_THEME = "lapis"
DEFAULT_STATUS = "draft"
# 章节注释里的提取规则提示（F29 骨架内容提取逻辑 1-3 的文字化）
_RULE_OVERVIEW = "提取核心段落改写为通俗语言"
_RULE_CORE = "首段+关键代码块，保留结构待 rewrite 润色"
_RULE_TRADEOFF = "提取作者选了什么/放弃了什么"
_RULE_TRANSFER = "提取「若我做同类系统会照抄/会改」的决策"


def _frontmatter_tags(meta: dict) -> list[str]:
    """骨架 tags：[内容类型, 主题名] + 源 tags 中最多 2 个可读标签。

    排除分类前缀 tag（源码分析/模式家族/学习路径）与 owner/repo 形态
    （含 "/"——溯源归 frontmatter.source，不重复进 tags）。
    """
    title = str(meta.get("title") or "")
    if title.startswith("模式家族-"):
        kind, name = "模式拆解", re.sub(r"^模式家族-", "", title, count=1)
    elif str(meta.get("category") or "") == "11-learning-path":
        kind, name = "学习路径", re.sub(r"^\d+-", "", title, count=1)
    else:
        kind, name = "技术解读", re.sub(r"^源码分析-", "", title, count=1) or title
    extras = [
        t
        for t in (meta.get("tags") or [])
        if t not in ("源码分析", "模式家族", "学习路径") and "/" not in t
    ][:2]
    return [kind, name, *extras]


def _h1_from_hint(hint: str) -> str:
    """正文主标题：选题角度的问句前半（「——」前），无破折号则整句。"""
    return hint.split("——", 1)[0].strip() or hint


def _find_section_text_hint(meta: dict, *keywords: str) -> str:
    """按关键词找首个命中的二级章节标题（注释里引用其原名），无则 ""。"""
    for s in meta.get("sections") or []:
        if any(k in s for k in keywords):
            return s
    return ""


def build_skeleton(meta: dict, suggestion: dict, source_label: str, *, hook: str = "") -> str:
    """组装骨架全文。

    - meta：scanner.scan_file 输出
    - suggestion：suggester 建议（series/skeleton_hint 必有，series_no 可选）
    - source_label：源文档相对/绝对路径（frontmatter.source 溯源字段）
    - hook：一句话钩子（scanner.extract_hook 提取；缺省留待补占位）

    源码分析走 F29 四章节模板；学习路径/族卡走同构通用模板（章节注释
    同样携带源章节名）。frontmatter 七键齐备：title/cover/theme/status/
    tags/source/series。
    """
    hint = str(suggestion.get("skeleton_hint") or meta.get("title") or "")
    series = str(suggestion.get("series") or "知识库精选")
    title = str(meta.get("title") or "")
    overview = _find_section_text_hint(meta, "项目概述", "概述") or (
        (meta.get("sections") or [""])[0] if meta.get("sections") else ""
    )
    hook_line = hook.strip() if hook and hook.strip() else "（待补：一句话钩子——从源文档概述提炼或 AI 生成）"

    if title.startswith("模式家族-"):
        name = re.sub(r"^模式家族-", "", title, count=1)
        chapters = [
            (f"一、{name} 是什么模式？", _find_section_text_hint(meta, "族定义") or "族定义", _RULE_OVERVIEW),
            ("二、不同项目里的同一副面孔", _find_section_text_hint(meta, "别名对照", "别名") or "别名对照", _RULE_OVERVIEW),
            ("三、谁在用、用出了什么效果", _find_section_text_hint(meta, "成员仓") or "成员仓对照", _RULE_CORE),
            ("四、给你的启示", _find_section_text_hint(meta, "成员仓") or "成员仓对照", _RULE_TRANSFER),
        ]
    elif str(meta.get("category") or "") == "11-learning-path":
        tech = re.sub(r"^\d+-", "", title, count=1)
        chapters = [
            (f"一、{tech} 是什么？", overview, _RULE_OVERVIEW),
            ("二、学习路线拆解", _find_section_text_hint(meta, "路径", "路线", "学习") or overview, _RULE_CORE),
            ("三、常见坑与建议", _find_section_text_hint(meta, "坑", "建议", "注意") or overview, _RULE_TRADEOFF),
            ("四、给你的启示", overview, _RULE_TRANSFER),
        ]
    else:
        # 源码分析（F29 原模板）与其他长文共用四问结构
        name = re.sub(r"^源码分析-", "", title, count=1) or title
        core = _find_section_text_hint(meta, "核心模块") or _find_section_text_hint(meta, "架构总览") or "核心模块分析"
        thinking = _find_section_text_hint(meta, "设计思维提炼") or "设计思维提炼"
        chapters = [
            (f"一、{name} 是什么？", overview, _RULE_OVERVIEW),
            ("二、核心秘密：" + _core_short(core), core, _RULE_CORE),
            ("三、它解决了什么问题？", thinking + "的「关键权衡」", _RULE_TRADEOFF),
            ("四、给你的启示", thinking + "的「可迁移决策」", _RULE_TRANSFER),
        ]

    tags = _frontmatter_tags(meta)
    lines: list[str] = [
        "---",
        f"title: {hint}",
        "cover: ./cover.png  # 需人工补",
        f"theme: {DEFAULT_THEME}",
        f"status: {DEFAULT_STATUS}",
        f"tags: [{', '.join(tags)}]",
        f"source: {source_label}",
        f"series: {series}",
        "---",
        "",
        f"# {_h1_from_hint(hint)}",
        "",
        f"> {hook_line}",
        "",
    ]
    for head, src_section, rule in chapters:
        lines.append(f"## {head}")
        lines.append(f"<!-- 源文档 §「{src_section}」：{rule} -->")
        lines.append("")
    lines.append("## 参考资料")
    lines.append(f"<!-- 源码分析原文：{source_label} -->")
    lines.append("")
    return "\n".join(lines)


def _core_short(section_title: str) -> str:
    """核心章节标题压短：去编号前缀与括注（「三、核心模块分析（Agent 运行时）」→「核心模块分析」）。"""
    t = re.sub(r"^[一二三四五六七八九十\d]+[、.．\s]*", "", section_title)
    t = re.sub(r"[（(].*?[)）]", "", t).strip("：: ")
    return t or section_title


def slugify(text: str, max_len: int = 60) -> str:
    """骨架文件名 slug：非法字符/空白折叠为 "-"，去首尾连字符，限长。

    CJK 原样保留（macOS/仓库文件名均兼容）；截断在字符边界进行。
    """
    s = re.sub(r'[/\\:*?"<>|#^\[\]\s，。：；！？·+]+', "-", str(text)).strip("-–—")
    s = re.sub(r"-{2,}", "-", s)
    return (s[:max_len].rstrip("-–—")) or "未命名"
