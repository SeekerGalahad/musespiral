"""素材标签规范化/校验（第二十一期 §1.2）——L2 纯函数，零 I/O。

triage.tags 是人工可变四键之一（与 read/starred 同类：纯人工、可逆、
不构成血统）。本模块是 **tags 面唯一契约**（正典 §1.2）：限额与规范化
规则只在此定义，D 档 F77（AI 建议标签）消费同一模块，不另立。

契约（正典 §1.2/§二-F63）：
  - 单标签 ≤24 字符（规范化后校验，不静默截断——可读错误纪律）；
  - 单素材 ≤12 个标签；
  - 规范化：小写 → 空白与非法字符（非 [a-z0-9-] 与中文区间）转连字符
    → 连续连字符收敛 → 首尾连字符剥除；幂等（已规范名再过一遍不变）；
  - 去重保序。
"""
from __future__ import annotations

import re
from typing import Any

# 限额（正典 §1.2 唯一契约；D 档 F77 同表消费）
MAX_TAGS = 12
MAX_TAG_CHARS = 24

# 合法字符：ASCII 小写字母/数字/连字符 + 中文区间（中日韩统一表意/
# 扩展A/兼容表意）；其余（含空白与标点）规范化时转连字符
_ALLOWED = re.compile(r"[a-z0-9\-\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def normalize_tag(raw: str) -> str:
    """单标签规范化（纯函数）：非法字符转连字符 → 收敛 → 剥首尾 → 小写。

    幂等：已规范名再过一遍不变（正典 §二-F63「预览即承诺」的基础）。
    规范化为空（全非法字符/空白）→ ValueError。
    """
    lowered = raw.lower()
    mapped = "".join(ch if _ALLOWED.match(ch) else "-" for ch in lowered)
    collapsed = re.sub(r"-{2,}", "-", mapped).strip("-")
    if not collapsed:
        raise ValueError(f"标签规范化后为空（不含任何合法字符）: {raw!r}")
    return collapsed


def normalize_tags(tags: Any) -> list[str]:
    """tags 值归一+校验（写窄门 triage.tags 的唯一入口）：

    非 list / 元素非 str / 超 12 个 / 单元素规范化后超 24 字符 →
    ValueError（400 可读文案透传）；合法 → 规范化+去重保序列表。
    """
    if not isinstance(tags, list):
        raise ValueError(f"triage.tags 必须是字符串列表（全量替换）: {type(tags).__name__}")
    normalized: list[str] = []
    for i, item in enumerate(tags):
        if not isinstance(item, str):
            raise ValueError(f"triage.tags[{i}] 必须是字符串: {item!r}")
        tag = normalize_tag(item)
        if len(tag) > MAX_TAG_CHARS:
            raise ValueError(f"单标签上限 {MAX_TAG_CHARS} 字符（规范化后 {len(tag)}）: {tag!r}")
        if tag not in normalized:  # 去重保序
            normalized.append(tag)
    if len(normalized) > MAX_TAGS:
        raise ValueError(f"单素材标签上限 {MAX_TAGS} 个（去重后 {len(normalized)}）")
    return normalized
