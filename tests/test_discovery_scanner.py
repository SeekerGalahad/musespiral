"""scanner.py 单元测试（F26）——frontmatter 手工解析/行数/章节/tags（tmp_path fixture）。"""
from __future__ import annotations

from musespiral.core.discovery import scanner

GOOD_DOC = """---
title: 源码分析-Demo
category: 13-source-analysis
created: 2026-11-01
tags: [源码分析, Python, 演示-项目, demo/demo]
---

# 源码分析：Demo (demo/demo)

> 分析日期: 2026-11-01

## 一、项目概述

一句话价值：演示扫描器行为。

## 二、架构总览

目录树。

## 三、核心模块分析

### 3.1 引擎：声明式定义

正文。

## 四、端到端数据流

数据流正文。

## 五、设计思维提炼

### 5.1 关键权衡

1. **固定四节点小图 vs 手写循环**
- **stream() 单路径：**

### 5.2 演进推断

普通加粗不在列表里 **不算条目**

## 六、可借鉴的工程实践

- **这条不在提炼小节，不收**
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_frontmatter_fields(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "源码分析-Demo.md", GOOD_DOC))
    assert meta["title"] == "源码分析-Demo"
    assert meta["tags"] == ["源码分析", "Python", "演示-项目", "demo/demo"]
    assert meta["created"] == "2026-11-01"
    assert meta["category"] == "13-source-analysis"


def test_line_count_excludes_frontmatter(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "源码分析-Demo.md", GOOD_DOC))
    # frontmatter 块共 6 行（4 键 + 上下界定符），不计正文
    assert meta["line_count"] == len(GOOD_DOC.splitlines()) - 6


def test_sections_and_headings(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "源码分析-Demo.md", GOOD_DOC))
    assert meta["sections"] == [
        "一、项目概述", "二、架构总览", "三、核心模块分析",
        "四、端到端数据流", "五、设计思维提炼", "六、可借鉴的工程实践",
    ]
    # headings 全量含 h1 与 h3
    levels = {lvl for lvl, _ in meta["headings"]}
    assert {1, 2, 3} <= levels


def test_pattern_titles_only_from_thinking_sections(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "源码分析-Demo.md", GOOD_DOC))
    # 「## 六」小节里的加粗条目不收；归一去尾冒号
    assert meta["pattern_titles"] == ["固定四节点小图 vs 手写循环", "stream() 单路径"]


def test_no_frontmatter_fallback_title_from_h1(tmp_path):
    text = "# 没有元数据的文档\n\n正文\n"
    meta = scanner.scan_file(_write(tmp_path, "plain.md", text))
    assert meta["title"] == "没有元数据的文档"
    assert meta["tags"] == []
    assert meta["created"] == ""
    assert meta["line_count"] == 3


def test_no_frontmatter_no_h1_fallback_stem(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "onlybody.md", "正文而已\n两行\n"))
    assert meta["title"] == "onlybody"


def test_unclosed_frontmatter_is_all_body(tmp_path):
    text = "---\ntitle: 断头\n正文第一行\n"
    meta = scanner.scan_file(_write(tmp_path, "broken.md", text))
    # 与 python-frontmatter 同口径：有开头无闭合 → 无 frontmatter，全文皆正文
    assert meta["title"] == "broken"  # 回退链：正文无 # → 文件 stem
    assert meta["tags"] == []
    assert meta["line_count"] == 3


def test_quoted_scalar_and_plain_tags_value(tmp_path):
    text = (
        "---\n"
        'title: "带引号标题"\n'
        "tags: 单标签\n"
        "created: '2026-11-02'\n"
        "---\n"
        "正文\n"
    )
    meta = scanner.scan_file(_write(tmp_path, "quoted.md", text))
    assert meta["title"] == "带引号标题"
    assert meta["tags"] == ["单标签"]  # 非数组值按单元素容忍
    assert meta["created"] == "2026-11-02"


def test_category_inferred_from_path_when_missing(tmp_path):
    text = "---\ntitle: 无类别\n---\n正文\n"
    src = tmp_path / "11-learning-path"
    src.mkdir()
    meta = scanner.scan_file(_write(src, "a.md", text))
    assert meta["category"] == "11-learning-path"


def test_family_card_repos_counted(tmp_path):
    text = (
        "---\n"
        "title: 模式家族-演示族\n"
        "category: 13-source-analysis\n"
        "created: 2026-11-13\n"
        "tags: [模式家族, 演示族]\n"
        "---\n"
        "# 模式家族：演示族\n"
        "\n"
        "> 复现 7 仓 · 原始命名 10 种\n"
        "\n"
        "## 族定义\n"
        "\n"
        "定义正文。\n"
    )
    meta = scanner.scan_file(_write(tmp_path, "模式家族-演示族.md", text))
    assert meta["family_repos"] == 7


def test_family_repos_none_for_normal_doc(tmp_path):
    meta = scanner.scan_file(_write(tmp_path, "源码分析-Demo.md", GOOD_DOC))
    assert meta["family_repos"] is None


def test_scan_dir_recursive_sorted_and_skips_bad_entries(tmp_path):
    root = tmp_path / "13-source-analysis"
    sub = root / "模式家族"
    sub.mkdir(parents=True)
    _write(root, "b.md", GOOD_DOC)
    _write(sub, "a.md", "---\ntitle: 模式家族-A\n---\n正文\n")
    (root / "陷阱.md").mkdir()  # 目录伪装 .md → read_text OSError → 跳过
    metas = scanner.scan_dir(root)
    titles = [m["title"] for m in metas]
    paths = [m["path"] for m in metas]
    assert paths == sorted(paths)  # 按路径排序（确定性），目录伪装项被跳过
    assert "源码分析-Demo" in titles and "模式家族-A" in titles
    assert len(metas) == 2


def test_scan_dir_non_recursive(tmp_path):
    root = tmp_path / "13-source-analysis"
    sub = root / "模式家族"
    sub.mkdir(parents=True)
    _write(root, "top.md", GOOD_DOC)
    _write(sub, "nested.md", "---\ntitle: 嵌套\n---\n正文\n")
    assert [m["title"] for m in scanner.scan_dir(root, recursive=False)] == ["源码分析-Demo"]


def test_extract_hook_value_line_priority(tmp_path):
    text = "## 一、项目概述\n\n一句话价值：这条最优先。\n\n> 引用不该赢\n"
    _, body = scanner.split_frontmatter(text)
    assert scanner.extract_hook(body) == "这条最优先。"


def test_extract_hook_blockquote_fallback(tmp_path):
    text = "## 一、项目概述\n\n> 分析日期: 2026-11-01\n\n> 钩子引用行\n"
    _, body = scanner.split_frontmatter(text)
    assert scanner.extract_hook(body) == "钩子引用行"


def test_extract_hook_first_paragraph_and_missing(tmp_path):
    _, body = scanner.split_frontmatter("## 一、项目概述\n\n首段第一行。\n")
    assert scanner.extract_hook(body) == "首段第一行。"
    _, empty = scanner.split_frontmatter("## 一、项目概述\n\n")
    assert scanner.extract_hook(empty) == ""


def test_extract_hook_stops_at_next_section(tmp_path):
    text = "## 一、项目概述\n\n## 二、架构总览\n\n一句话价值：隔壁章节的不算\n"
    _, body = scanner.split_frontmatter(text)
    assert scanner.extract_hook(body) == ""
