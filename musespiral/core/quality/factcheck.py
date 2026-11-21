"""AI 事实自检（F9）——改写稿「增量事实」清单，AI 只标记不否决。

对付 AI 改写的事实漂移：不验证全文，只验证增量——改写稿里有、原笔记里
无依据的具体断言/数字/引文。人只核对这份短清单，而不是重读全文。

红线：本模块只产本地结果（清单返回给 CLI 打印/嵌入 review 页），
绝不进 check 门、不阻塞 publish。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import frontmatter

from ...infra import ai

_PROMPT = """你是严谨的事实核查员。下面是同一份内容的两个版本：原始笔记与据其改写的公众号文章。

任务：找出改写文章中的「增量事实」——文章里出现、但原始笔记中找不到依据的具体断言、数字、引文、时间、人名、案例。同义转述与风格改写不算。

要求：
- 只输出一个 JSON 数组，不要任何解释文字，不要 Markdown 代码围栏
- 每个元素形如 {{"claim": "文章中的具体断言", "where": "大致段落定位"}}
- 没有发现问题就输出 []

原始笔记：
---
{note}
---

改写文章：
---
{article}
---
"""


class FactcheckError(RuntimeError):
    """事实自检异常基类。"""


class FactcheckNotConfigured(FactcheckError):
    """AI 未配置——调用方应明确提示并以非零码退出，不静默跳过。"""


class FactcheckUnavailable(FactcheckError):
    """自检不可用（网络失败/响应非法 JSON）——调用方降级提示，不崩溃。"""


def check_facts(note_path: str | Path, article_path: str | Path) -> list[dict[str, str]]:
    """对比原笔记与改写稿，返回无原文依据的增量事实清单。

    二次 AI 调用（复用 ai.load_ai_config），输入原笔记全文 + 改写稿全文，
    要求只输出 JSON 数组 [{"claim", "where"}]。AI 未配置抛
    FactcheckNotConfigured；响应体提取 JSON 失败（含网络/非 200/格式异常）
    抛 FactcheckUnavailable，由 CLI 层降级为「自检不可用」提示。
    """
    if not ai.ai_configured("check_facts"):
        raise FactcheckNotConfigured(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )

    note_body = frontmatter.load(Path(note_path)).content.strip()
    article_body = frontmatter.load(Path(article_path)).content.strip()
    try:
        raw = ai.chat(
            [{"role": "user", "content": _PROMPT.format(note=note_body, article=article_body)}],
            task="check_facts", temperature=0.1,
        )
    except ai.AIError as e:
        raise FactcheckUnavailable(f"自检不可用：{e}") from e
    return _parse_flags(raw)


def _parse_flags(raw: str) -> list[dict[str, str]]:
    """从响应正文提取 JSON 数组（容忍代码围栏与前后杂文字），归一为 claim/where 字典。"""
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise FactcheckUnavailable("自检不可用：AI 响应中未找到 JSON 数组")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise FactcheckUnavailable(f"自检不可用：AI 响应非法 JSON: {e}") from e
    if not isinstance(data, list):
        raise FactcheckUnavailable("自检不可用：AI 响应不是 JSON 数组")
    flags: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim", "")).strip()
        if not claim:
            continue
        flags.append({"claim": claim, "where": str(item.get("where", "")).strip()})
    return flags
