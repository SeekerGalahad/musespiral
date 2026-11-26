"""按偏移取章正文（L2 纯，零 I/O）——引用型读取，永不复制整书。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2 第四行：`按偏移取章正文（cap 200k 同 CHAPTER_CAP）`；§五 红线：
`本地 TXT 只读不复制整书（引用型）`。

**cap 语义与 `studio/book_actions.CHAPTER_CAP` 逐字一致**（200_000 字符）：
网络书与本地书在阅读面板是同一个渲染器，截断口径分叉会变成「同样长的章节
在网络书上看得到尾巴、本地书看不到」——同一数字常量在两处必须相等，本模块
注释与 `test_localbook_core.py` 各锁一次。

返回值形态也对齐 `GET /api/book/chapter`（`{title, content, chars, truncated}`）
——前端 `rdRenderChapter` 的分派只换请求地址，不换解析逻辑。
"""
from __future__ import annotations

from typing import Any

# 单章正文上限（与 studio/book_actions.CHAPTER_CAP 同值：200_000 字符。
# 防御面=单章异常巨大（整书无换行的「一章」）不撑爆前端渲染）
CHAPTER_CAP = 200_000


def slice_chapter(
    text: str,
    start: int,
    end: int,
    cap: int = CHAPTER_CAP,
) -> dict[str, Any]:
    """按字符偏移切一章 → `{title:"", content, chars, truncated, orig_chars}`。

    参数：
      - `text`：整书文本（调用方已按探测出的编码解码完成）；
      - `start`/`end`：`chapters.parse_chapters` 给出的字符偏移（end 不含）；
      - `cap`：截断上限，超长即截断并置 `truncated=True`。

    边界口径（比「夹到 [0,len]」多说一步）：
      - 反向区间（start > end）与负 start 一律夹紧——脏元数据（手改 meta.json）
        不该产生 500，切出来空章 + `truncated=False` 是可读降级；
      - **内容 strip 两端空白**（章节之间的空行属于分隔，不属于正文），因此
        `chars` 是 strip 后的字符数；`orig_chars` 是原始切片长度（截断前），
        两个数都给——这是「没少给你东西」的可核验面。
    """
    total = len(text)
    lo = max(0, min(int(start), total))
    hi = max(lo, min(int(end), total))
    raw = text[lo:hi]
    orig_chars = len(raw)
    body = raw.strip()
    truncated = False
    limit = int(cap)
    if limit > 0 and len(body) > limit:
        body = body[:limit]
        truncated = True
    return {
        "title": "",
        "content": body,
        "chars": len(body),
        "orig_chars": orig_chars,
        "truncated": truncated,
    }


__all__ = ["CHAPTER_CAP", "slice_chapter"]
