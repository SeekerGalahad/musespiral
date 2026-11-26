"""目录规整（L2 纯，零 I/O）——序号清洗 / 去重 / 空章剔除 → bookengine 同形。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2 第三行：`与 bookengine toc 同形 {title, index, url}，前端阅读面板零改动
复用`；§1.4：`rdOpen('book', {origin:'localbook:<id>', bookUrl:'', ...})` 走同一
`$('rd-toc')` 路径，仅把 toc/chapter 请求按 origin 前缀分派。

为什么规整后要**重新编号 index** 而不是沿用原 n：阅读面板的 `data-n`（第几章
按钮）与 `rdRenderChapter(n)` 用 1..len 的连续序号取 `RD.chapters[n-1]`；目录
若被剔除（空标题/连续重复）留出空洞，点击即错位一章。`url` 里保留**原始**
`localbook:<id>#<n>`（定位真章），`index` 是**目录内的连续位次**——两者职责
不同，分开存才不互相污染。

为什么只做「连续重复」去重：分卷 TXT 常见相邻两行同标题（卷首+章首重名），
那是噪声；非相邻的重名（两章都叫「序」/「番外」）是真实内容，按标题全局去重
会吞章——目录宁可多不可少，这与 `chapters.py` 「不冒充章」的诚实性同一条纪律。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# 连续空白（含全角空格 　 / 制表）折叠为单个半角空格
_WS_RE = re.compile(r"[ \t\u3000\u00a0]+")

# 标题里混进的控制字符（编码错解残留的 NUL 等）——不可见字符会撑破行高
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# 序号清洗：章节号内部的空白（「第 一 章」「第  12  回」）压掉——排版对齐常见，
# 但目录里同一个「第X章」两种写法会让去重/排序全乱；只清洗「第…数字…单位」
# 这一段，标题后半段的空白仍按单空格保留（那是语义分隔）。
_SEQ_WS_RE = re.compile(
    r"第\s*([0-9一二三四五六七八九十百千万两零〇]+)\s*([章节回卷集部篇])"
)


def clean_title(raw: Any) -> str:
    """标题规整：去控制字符 → 空白折叠 → 序号清洗 → strip。

    序号清洗见 `_SEQ_WS_RE`（只压章节号内部空白，保留「第X章 标题」的分隔）。
    """
    text = _CTRL_RE.sub("", str(raw or ""))
    text = _WS_RE.sub(" ", text).strip()
    return _SEQ_WS_RE.sub(r"第\1\2", text)


def clean_toc(
    chapters: Iterable[dict[str, Any]],
    book_id: str = "",
    *,
    url_template: str = "localbook:{book_id}#{n}",
) -> list[dict[str, Any]]:
    """`[{n,title,start,end}]` → bookengine 同形 `[{title,index,url}]`。

    规则（顺序即优先级）：
      1. **空章剔除**：title 规整后为空 → 整条不进目录（正文仍可按 n 直取，
         只是目录里不出现——空标题在列表里就是一个无法识别的灰条）；
      2. **连续重复去重**：与上一条保留项标题逐字相同 → 丢弃（见模块头）；
      3. **重新编号**：index 从 1 连续递增；url 用原始 n 定位真章。

    `url_template` 是给测试与将来换 scheme 用的窄口（缺省即设计给的形式）；
    缺 `n` 的脏条目（非 int）直接跳过——目录的每个 url 都必须可解析回章号。
    """
    out: list[dict[str, Any]] = []
    last_title: str | None = None
    for row in chapters:
        if not isinstance(row, dict):
            continue
        n = row.get("n")
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            continue
        title = clean_title(row.get("title"))
        if not title:
            continue
        if last_title is not None and title == last_title:
            continue
        last_title = title
        out.append(
            {
                "title": title,
                "index": len(out) + 1,
                "url": url_template.format(book_id=book_id, n=n),
            }
        )
    return out


__all__ = ["clean_title", "clean_toc"]
