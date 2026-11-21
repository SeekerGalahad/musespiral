"""AI 置信度自评解析（N-4，正典=《musespiral-优化重构与后续开发总体方案-
2026-11-19.md》§二 N-4「提炼时 AI 附带置信度」）——L2 纯函数，零 I/O。

两件东西：
  - CONFIDENCE_LOW_THRESHOLD：低置信门（0.5）——「⚠ 低置信」徽与
    GET /api/materials?confidence=low 筛选共用同一阈值（单一权威）；
  - parse_ai_confidence(text)：AI 回复 → (剥除置信度行后的正文, 置信度|None)。

**解析容错是契约**（总体方案 N-4 原文「解析失败则留空不阻断面」）：AI 没按
协议输出该行、输出了但值非法，一律返回 (原文原样, None)——置信度是自评
增强信息，解析失败绝不阻断面（提炼/综合产物照常落库）。识别协议：正文
**最后一个非空行**形如 ``CONFIDENCE: 0.8`` / ``置信度：0.8``（全半角冒号、
前后空白宽容；值 0-1 小数；>1 且 ≤100 按百分数折算——``85%``/``85`` 是
模型常见回退形态，折算比丢弃保留更多信息；其余一律 None 且**不剥行**——
没读懂的内容不删）。
"""
from __future__ import annotations

import re

# 低置信门（三态徽「⚠ 低置信」与 confidence=low 筛选同源）
CONFIDENCE_LOW_THRESHOLD = 0.5

# 置信度标记行：行首 CONFIDENCE / 置信度 + 全半角冒号 + 数值（允许 % 尾巴）。
# 只在「最后一个非空行」上做整行匹配——mid-line 提及不算（协议是末行单独标注）。
_MARKER_RE = re.compile(
    r"^(?:CONFIDENCE|置信度)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*%?\s*$",
    re.IGNORECASE,
)


def parse_ai_confidence(text: str) -> tuple[str, float | None]:
    """AI 回复 → (剥除置信度行后的正文, 置信度|None)。

    容错矩阵（全部 → (原文原样, None)）：空/非串入参、无末非空行、末行不
    匹配标记、数值 <0、∈(1,2)、>100、``float`` 失败。**百分数回退只认
    ≥2 的值**（``85``/``85%`` → 0.85——模型常见回退形态，折算比丢弃保留
    更多信息；(1,2) 开区间值如 1.5 更可能是「忘归一化」的越界自评，按
    无效处理而非折成 1.5% 的伪低分）。剥行只剥该行（前置空行不动）。
    """
    if not isinstance(text, str):
        return text, None  # type: ignore[unreachable] — 容错契约：非串原样透传
    lines = text.splitlines()
    idx = len(lines) - 1
    while idx >= 0 and not lines[idx].strip():
        idx -= 1  # 尾部空行跳过（不剥——只剥标记行本身）
    if idx < 0:
        return text, None
    match = _MARKER_RE.match(lines[idx].strip())
    if match is None:
        return text, None
    try:
        value = float(match.group(1))
    except ValueError:  # pragma: no cover — 正则已保证可解析，防御性兜底
        return text, None
    if value < 0 or value > 100 or 1 < value < 2:
        return text, None
    if value > 1:
        value = value / 100.0  # 百分数回退形态（85/85% → 0.85；100 → 1.0）
    out = "\n".join(lines[:idx] + lines[idx + 1 :])
    return out, value
