"""一稿多态本地校验（F14）——产物字数兜底告警。

自 musespiral/adapt.py 拆出（后端架构正典 §八 Phase B）：AI 的字数约束只
存在于 prompt，落盘前在此做本地兜底（复核 low#6）。
"""
from __future__ import annotations

import re
from typing import Any

# 本地规格校验（§十四 F14 各平台形态的字数硬指标；分镜标记计入字数，取近似值）
_LOCAL_LIMITS: dict[str, dict[str, Any]] = {
    "xhs": {"content_max_chars": 1000},
    "channels": {"script_chars": (180, 260), "cover_title_max_chars": 14},
    "douyin": {"script_chars": (40, 120)},
}

_COVER_TITLE_RE = re.compile(r"^封面标题[:：]\s*(.+)$", re.MULTILINE)


def _validate(platform: str, content: str) -> list[str]:
    """对产物做本地字数校验，返回告警列表（空列表=全部合规）。

    AI 的字数约束只存在于 prompt，输出此前无本地校验直接落盘（复核
    low#6）；此处兜底告警——人工审核先看到「哪里超了」，不必逐字数。
    """
    warns: list[str] = []
    lim = _LOCAL_LIMITS.get(platform, {})
    n = len(content)
    if "content_max_chars" in lim and n > lim["content_max_chars"]:
        warns.append(f"正文 {n} 字超出小红书 {lim['content_max_chars']} 字上限")
    bounds = lim.get("script_chars")
    if bounds and not (bounds[0] <= n <= bounds[1]):
        warns.append(f"口播稿 {n} 字超出 {bounds[0]}-{bounds[1]} 字建议区间")
    if "cover_title_max_chars" in lim:
        m = _COVER_TITLE_RE.search(content)
        if m and len(m.group(1).strip()) > lim["cover_title_max_chars"]:
            warns.append(
                f"封面标题 {len(m.group(1).strip())} 字超出 {lim['cover_title_max_chars']} 字建议"
            )
    return warns
