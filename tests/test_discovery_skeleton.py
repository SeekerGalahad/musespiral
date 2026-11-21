"""skeleton.py 单元测试（F29）——结构完整性/frontmatter 字段/溯源/章节注释/slug。"""
from __future__ import annotations

import re

from musespiral.core.discovery import skeleton

META = {
    "path": "/kb/源码分析-FastDB.md",
    "title": "源码分析-FastDB",
    "tags": ["源码分析", "Python", "高性能-数据库", "demo/fastdb"],
    "created": "2026-11-01",
    "category": "13-source-analysis",
    "line_count": 260,
    "headings": [
        (1, "源码分析：FastDB"),
        (2, "一、项目概述"),
        (2, "三、核心模块分析（存储引擎）"),
        (3, "3.1 MergeTree 引擎：列式压缩"),
        (2, "四、端到端数据流"),
        (2, "五、设计思维提炼"),
    ],
    "sections": ["一、项目概述", "三、核心模块分析（存储引擎）", "四、端到端数据流", "五、设计思维提炼"],
    "pattern_titles": [],
    "family_repos": None,
}
SUGGESTION = {
    "series": "开源项目深度解读",
    "series_no": 1,
    "reason": "260 行深度内容",
    "skeleton_hint": "为什么 FastDB 这么快？——MergeTree 存储引擎 深度解析",
}


def _fm(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "骨架必须有 frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def test_frontmatter_seven_keys():
    text = skeleton.build_skeleton(META, SUGGESTION, "docs/…/源码分析-FastDB.md")
    fm = _fm(text)
    assert fm["title"] == SUGGESTION["skeleton_hint"]
    assert fm["cover"].startswith("./cover.png")
    assert fm["theme"] == "lapis"
    assert fm["status"] == "draft"
    assert fm["series"] == "开源项目深度解读"
    # tags：技术解读 + 项目名 + 最多 2 个可读源 tag（排除 源码分析 前缀与 owner/repo）
    assert fm["tags"] == "[技术解读, FastDB, Python, 高性能-数据库]"
    assert fm["source"] == "docs/…/源码分析-FastDB.md"


def test_h1_is_hint_head_and_hook_line():
    text = skeleton.build_skeleton(META, SUGGESTION, "src", hook="一句话钩子")
    assert "# 为什么 FastDB 这么快？" in text
    assert "> 一句话钩子" in text


def test_hook_placeholder_when_missing():
    text = skeleton.build_skeleton(META, SUGGESTION, "src", hook="  ")
    assert "> （待补：一句话钩子" in text


def test_chapter_source_annotations():
    text = skeleton.build_skeleton(META, SUGGESTION, "docs/x/源码分析-FastDB.md")
    comments = re.findall(r"<!-- (.+?) -->", text)
    # 四章节注释 + 参考资料，各含「源文档 §」溯源；权衡/启示指向设计思维提炼小节
    assert len(comments) == 5
    assert all(c.startswith("源文档 §") or c.startswith("源码分析原文") for c in comments)
    assert any("一、项目概述" in c for c in comments)
    assert any("三、核心模块分析（存储引擎）" in c for c in comments)
    assert any("「关键权衡」" in c for c in comments)
    assert any("「可迁移决策」" in c for c in comments)
    # 参考资料携带原文路径
    assert comments[-1] == "源码分析原文：docs/x/源码分析-FastDB.md"


def test_four_chapter_structure():
    text = skeleton.build_skeleton(META, SUGGESTION, "src")
    heads = re.findall(r"^## (.+)$", text, re.M)
    assert heads[0].startswith("一、FastDB 是什么？")
    assert heads[1].startswith("二、核心秘密：核心模块分析")
    assert heads[2] == "三、它解决了什么问题？"
    assert heads[3] == "四、给你的启示"
    assert heads[4] == "参考资料"


def test_family_card_template():
    card = {
        **META,
        "title": "模式家族-稳定 ABI 边界墙",
        "tags": ["模式家族", "稳定-ABI-边界墙"],
        "sections": ["族定义", "别名对照（同一机制的不同叫法）", "成员仓对照"],
    }
    sug = {"series": "跨仓模式拆解", "skeleton_hint": "7 个开源项目怎么解决同一个问题：稳定 ABI 边界墙"}
    text = skeleton.build_skeleton(card, sug, "docs/x/模式家族-稳定.md")
    fm = _fm(text)
    assert fm["tags"] == "[模式拆解, 稳定 ABI 边界墙, 稳定-ABI-边界墙]"
    heads = re.findall(r"^## (.+)$", text, re.M)
    assert heads[0] == "一、稳定 ABI 边界墙 是什么模式？"
    assert any("族定义" in c for c in re.findall(r"<!-- (.+?) -->", text))


def test_learning_path_template():
    lp = {**META, "title": "01-AI开发入门指南", "category": "11-learning-path", "tags": ["入门", "AI"]}
    sug = {"series": "学习路径入门", "skeleton_hint": "AI开发入门指南：从零到一"}
    text = skeleton.build_skeleton(lp, sug, "docs/lp/01.md")
    assert _fm(text)["tags"] == "[学习路径, AI开发入门指南, 入门, AI]"
    assert "## 一、AI开发入门指南 是什么？" in text


def test_fallback_sections_when_meta_barren():
    barren = {"title": "裸文档", "tags": [], "category": "", "sections": [], "headings": []}
    text = skeleton.build_skeleton(barren, {"series": "知识库精选", "skeleton_hint": "裸文档 解读"}, "x")
    # 无章节可引时注释仍成型（空章节名占位不崩）
    assert len(re.findall(r"<!-- ", text)) == 5
    assert "## 参考资料" in text


# ── slugify ───────────────────────────────────────────────────────────


def test_slugify_replaces_illegal_and_collapses():
    assert skeleton.slugify("为什么 A/B 这么快？——核心:引擎 深度解析") == (
        "为什么-A-B-这么快-——核心-引擎-深度解析"
    )


def test_slugify_truncates_and_strips_dashes():
    s = skeleton.slugify("标题" * 50, max_len=10)
    assert len(s) <= 10 and not s.endswith("-")


def test_slugify_empty_falls_back():
    assert skeleton.slugify("？？？") == "未命名"
