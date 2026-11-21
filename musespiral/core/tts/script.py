"""文章 → 朗读脚本（G-T5，L2 纯）。

设计正典=《musespiral-调研吸收功能设计-2026-11-18》§6.1「正稿→音频，听稿校对/
通勤灵感」。朗读的对象是**给人听的正文**，不是渲染 HTML：解析器给出的块里
夹着 `<a>`/`<strong>` 之类的内联标签与 HTML 实体，直接喂 TTS 会把标签名念
出来。

取舍（写在这里是为了让后来者知道这是决定而非遗漏）：
  - **代码块不读**：变量名与运算符念出来是噪声，听稿校对的价值在叙述段落；
  - **表格不读**：单元格脱离表头失去语义，念出来比跳过更难听；
  - **图片/分隔线不读**：无文字内容；
  - 标题/段落/列表项/引用**读**，按块序以换行分隔（换行让 TTS 自然断句）。

零 I/O、零网络、零三方依赖（架构门规则 3 的域层纪律；HTML 去标签用标准库
`html.unescape` + 正则，不引 BeautifulSoup 之类的解析器）。
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any

# 任意 HTML 标签（朗读面只需剥标签，不建 DOM）
_TAG_RE = re.compile(r"<[^>]+>")
# 行内连续空白（去标签后 HTML 源码里的换行/缩进没有语义）
_WS_RE = re.compile(r"[ \t\r\n\u00a0]+")

# 进朗读脚本的块类型（其余块——code/table/image/hr——按模块头取舍跳过）
_SPOKEN_BLOCKS = frozenset({"heading", "paragraph", "list", "quote"})


_MAX_MARKUP_ROUNDS = 3


def strip_markup(text: str) -> str:
    """HTML 片段 → 纯文本：剥标签 ⇄ 反转义实体（≤3 轮，到不动点）+ 折叠空白。

    多轮是必要的而非防御性冗余：mistune 的 HTMLRenderer 默认 `escape=True`，
    原文里的行内 HTML（`<strong>`/`<a href>`）先被转义成 `&lt;strong&gt;` 落进
    块文本，原文里的 `&amp;` 则被转义成 `&amp;amp;`——单轮「先剥后解」在两类
    输入上各失败一次（实测：`<strong>` 解回来、`&amp;` 残留）。逐轮「剥标签
    → 反转义」直到输出不再变化，两类都收敛；轮数有上限防实体套娃（`&amp;amp;
    amp;amp;…`）无限循环。
    """
    plain = text or ""
    for _ in range(_MAX_MARKUP_ROUNDS):
        nxt = unescape(_TAG_RE.sub("", plain))
        if nxt == plain:
            break
        plain = nxt
    plain = _WS_RE.sub(" ", plain)
    return plain.strip()


def speech_text(article: Any) -> str:
    """ParsedArticle → 朗读脚本（frontmatter 标题在前，块正文在后）。

    空脚本（全篇只有代码/表格/图片）返回空串——调用方据此报可读错误，
    不把空串送去远端（那会得到一条零字节音频）。
    """
    parts: list[str] = []
    title = getattr(article, "metadata", {}).get("title")
    if isinstance(title, str):
        cleaned = strip_markup(title)
        if cleaned:
            parts.append(cleaned)
    for block in getattr(article, "blocks", []):
        kind = block.get("type")
        if kind not in _SPOKEN_BLOCKS:
            continue
        if kind == "list":
            for item in block.get("items") or []:
                line = strip_markup(item)
                if line:
                    parts.append(line)
            continue
        line = strip_markup(block.get("text") or "")
        if line:
            parts.append(line)
    return "\n".join(parts)
