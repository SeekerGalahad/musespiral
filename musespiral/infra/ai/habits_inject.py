"""风格块注入件（第十七期 F44 语义扩展，正典 §2.3 infra/ai/habits_inject.py）。

v1（habits_prompt）：改稿习惯 → 系统提示词段，鸭子签名、零 I/O——本函数
逐字节保留。F44 扩展为 style 块：habits 摘要（≤12 条）+ style_samples.md
范文选段（≤3 段），恒定边界标记 <!--style-->…<!--/style--> 包裹，作为
「写作风格要求（个人风格档案）」一节注入六个生成任务的 system prompt。

组装纪律（设计 §三 Q1/Q2）：注入件本身零 I/O 纯文本拼装；两资产由调用方
每次组装现读（core.habits.load_habits + load_style_samples）——infra 不
import 上层模块（正典规则 4），config 仅在 load_style_samples 内懒加载
（profiles.py 同款先例）。两资产全无 → 空串，调用方据此跳过注入不报错。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

# 风格资产文件名（~/.musespiral/ 下，纯本地：永不入 git、永不上云）
STYLE_SAMPLES_FILE = "style_samples.md"

# style 块恒定边界标记（不可见于输出；测试断言与未来按任务开关的锚点）
STYLE_OPEN = "<!--style-->"
STYLE_CLOSE = "<!--/style-->"

# style 块节标题（设计 §一：「写作风格要求（个人风格档案）」一节）
STYLE_HEADER = "写作风格要求（个人风格档案）"

# habits 摘要条数上限（超限截断，取前 12 条）
HABITS_MAX = 12

# 范文选段注入上限（设计 §〇：≤3 段，控 token）
SAMPLES_MAX = 3

# 段落切分：空行分段（与 core.habits._paragraphs 同源语义，本地复用不 import）
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


def habits_prompt(habits: Iterable[object]) -> str:
    """拼为「用户改稿习惯」提示词段；空列表返回空串（调用方据此跳过注入）。"""
    habits = list(habits)
    if not habits:
        return ""
    lines = ["用户改稿习惯（从历史改稿 diff 中学习，改写时请遵循）："]
    lines.extend(f"- {h.rule}：{h.example}" for h in habits)
    return "\n".join(lines)


def load_style_samples(path: str | Path | None = None) -> str:
    """读取范文资产全文（~/.musespiral/style_samples.md）；缺失/读失败返回空串。

    只读不写；宽容解析与 load_habits 同语义（文件人工可编辑）。
    """
    if path is None:
        from ... import config  # 懒加载（本模块纯函数保持零内部依赖）

        path = config.home_dir() / STYLE_SAMPLES_FILE
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def sample_paragraphs(samples: str, limit: int = SAMPLES_MAX) -> list[str]:
    """范文选段：空行分段后取前 limit 个非注释段。

    整段 HTML 注释（<!-- … -->）跳过——模板说明/维护注释不进提示词，
    未填写的模板因此零注入。
    """
    paras: list[str] = []
    for para in _PARA_SPLIT_RE.split(samples.strip()):
        p = para.strip()
        if not p or (p.startswith("<!--") and p.endswith("-->")):
            continue
        paras.append(p)
    return paras[:limit]


def _samples_section(samples: str) -> str:
    """范文选段子节；无有效段返回空串。"""
    paras = sample_paragraphs(samples)
    if not paras:
        return ""
    head = "范文选段（用户定稿样例，模仿其语气与节奏，不要照抄其中的具体内容）："
    body = "\n\n".join(f"【范文 {i}】{p}" for i, p in enumerate(paras, 1))
    return head + "\n" + body


def style_block(habits: Iterable[object], samples: str) -> str:
    """组装 style 块：habits 摘要（≤12 条）+ 范文选段（≤3 段）。

    恒定边界标记包裹；两资产全无（无 habits 且范文无有效段）返回空串，
    调用方据此不注入不报错。
    """
    habit_part = habits_prompt(list(habits)[:HABITS_MAX])
    sample_part = _samples_section(samples)
    if not habit_part and not sample_part:
        return ""
    lines = [STYLE_OPEN, STYLE_HEADER]
    if habit_part:
        lines += ["", habit_part]
    if sample_part:
        lines += ["", sample_part]
    lines.append(STYLE_CLOSE)
    return "\n".join(lines)
