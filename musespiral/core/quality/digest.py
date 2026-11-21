"""备选摘要（F10）——digest 候选生成，人挑机器写，质量共管。

现状 digest 首字截断 54 字且机器门只查字节。本模块产出候选清单供人工挑选：
AI 已配置 → 3 条（导语式/结论式/悬念式，各 ≤54 字）；未配置 → 2 条启发式
（首个完整句修剪 / 含数字或结论词的句子截取）。frontmatter 用
python-frontmatter 读取，只取正文做候选来源。

红线：本模块只产本地结果，绝不写 check 门。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import frontmatter

from ...infra import ai
from ...infra.ai import AIError

MAX_CHARS = 54

_PROMPT = """你是公众号资深编辑。请为下面的文章写 3 条备选摘要（digest），风格各不相同：

1. 导语式：概括文章主题，稳态开场
2. 结论式：直接给出文章的核心结论或关键数字
3. 悬念式：抛出问题或反常识点，勾起点开欲

要求：
- 每条不超过 54 字（含标点），彼此不重复
- 只输出一个 JSON 数组（3 个字符串），不要解释文字，不要代码围栏

文章正文：
---
{content}
---
"""

# 句末标点（全角 + 半角）；「完整句」指以这些收尾的句子
_SENTENCE_END = "。！？!?"

# 结论词：候选二优先截取含数字或这些词的句子
_CONCLUSION_WORDS = ("总之", "综上", "结论", "关键", "核心", "本质", "建议", "注意", "最重要")


def candidates(article_path: str | Path) -> list[str]:
    """返回备选摘要候选清单。

    AI 已配置 → 3 条候选（响应解析容忍 JSON 围栏与逐行两种形态，逐条修剪
    ≤54 字）；AI 未配置或调用/解析失败 → 2 条启发式，保证始终有本地结果。
    """
    post = frontmatter.load(Path(article_path))
    content = post.content.strip()
    if ai.ai_configured("digest"):
        try:
            # F44 style 块（habits+范文现读现拼；两资产全无 → 空串不注入，
            # title/suggester/generator 同款形态——懒导入保持 domain 轻依赖）
            from ..habits import load_habits
            from ...infra.ai.habits_inject import load_style_samples, style_block

            messages: list[dict[str, str]] = []
            style_section = style_block(load_habits(), load_style_samples())
            if style_section:
                messages.append({"role": "system", "content": style_section})
            messages.append({"role": "user", "content": _PROMPT.format(content=content)})
            raw = ai.chat(messages, task="digest", temperature=0.7)
        except AIError:
            raw = ""
        items = _parse_candidates(raw)
        if items:
            return [_trim(s) for s in items][:3]
    return _heuristic_candidates(content)


def _parse_candidates(raw: str) -> list[str]:
    """解析 AI 候选：先提取 JSON 数组（容忍围栏与前后杂文字），失败则逐行解析。"""
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            items = [str(x).strip() for x in data if str(x).strip()]
            if items:
                return items
    # 逐行解析：去围栏行 / 项目符号与序号 / 包裹引号
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("```"):
            continue
        s = re.sub(r"^(?:[-*•]|\d+[.、）)])\s*", "", s)
        s = s.strip("\"'“”「」 ").strip()
        if s:
            out.append(s)
    return out


def _heuristic_candidates(content: str) -> list[str]:
    """无 AI 时的两条启发式：首个完整句修剪 + 含数字或结论词的句子截取。

    无候选句（正文没有完整句）时回退首个正文行；正文全空则返回空列表。
    """
    sentences = _prose_sentences(content)
    if not sentences:
        line = _first_prose_line(content)
        return [_trim(line)] if line else []

    out = [_trim(sentences[0])]
    rest = sentences[1:]
    marked = next(
        (s for s in rest if _has_digit(s) or any(w in s for w in _CONCLUSION_WORDS)),
        None,
    )
    if marked is None:  # 无数字/结论词命中：取下一个完整句凑足两条
        marked = next(iter(rest), None)
    if marked is not None:
        out.append(_trim(marked))
    return out


def _trim(text: str, limit: int = MAX_CHARS) -> str:
    """修剪到 ≤limit 字（超长截断以省略号收尾，省略号计入长度）。"""
    s = re.sub(r"\s+", " ", text).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _prose_lines(content: str) -> list[str]:
    """取正文行：跳过空行/标题/围栏代码块/图片/引用/表格行，并去掉行内强调记号。"""
    out: list[str] = []
    in_fence = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line or line.startswith(("#", "---", "!", ">", "|")):
            continue
        clean = line.replace("*", "").replace("`", "").strip()
        if clean:
            out.append(clean)
    return out


def _split_sentences(line: str) -> list[str]:
    """按句末标点切分一行，返回去首尾空白的非空片段。"""
    parts = re.split(r"(?<=[。！？!?])", line)
    return [p.strip() for p in parts if p.strip()]


def _prose_sentences(content: str) -> list[str]:
    """正文中的全部完整句（以句末标点收尾）；跨行不拼句。"""
    out: list[str] = []
    for line in _prose_lines(content):
        for s in _split_sentences(line):
            if s and s[-1] in _SENTENCE_END:
                out.append(s)
    return out


def _first_prose_line(content: str) -> str:
    lines = _prose_lines(content)
    return lines[0] if lines else ""


def _has_digit(s: str) -> bool:
    return bool(re.search(r"\d", s))
