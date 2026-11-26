"""本地 TXT 书籍领域层（B1 本地书籍线，L2 纯，2026-11-26）。

设计正典=`docs/项目方案/musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-
2026-11-26.md` §一。五模块**零 I/O、零网络、零第三方依赖**（与 core/booksource
同一条纯度纪律）：

  - charset：编码探测（BOM → UTF-8 严格 → GB18030 → Big5），带置信度；
  - chapters：章节正则族（中文第X章/节/回/卷 + Chapter N + CHAPTER）→ 字符偏移；
  - toc：目录规整（序号清洗/去重/空章剔除）→ bookengine 同形 {title,index,url}；
  - reader：按偏移取章正文（cap 同 CHAPTER_CAP=200k）；
  - rules：内置三规则 + 自定义规则目录加载（本模块只解析 JSON 文本，目录
    **读取**归 services 层——纯度纪律的落点）。

为什么 L2 纯：编码/切章/目录清洗全都是纯文本工程，纯函数才能用确定性单测
锁住「GB18030 大文件探测正确」「无结构文本如实回退」这类诚实性承诺；副作用
（文件读取、书架落盘）全部留在 services/localbook_service 一层。

红线（设计 §五）：本地 TXT **只读不复制整书**——本层只产出字符偏移，正文
按偏移现取（reader.slice_chapter），永不在库内落副本。
"""
from __future__ import annotations

from .chapters import CHAP_RE, parse_chapters, parse_with_rule, split_fixed
from .charset import detect_bytes
from .reader import slice_chapter
from .rules import (
    BUILTIN_RULES,
    DEFAULT_RULE_ID,
    RuleError,
    all_rules,
    get_rule,
    load_rules_from_texts,
    parse_rule,
)
from .toc import clean_toc

__all__ = [
    "BUILTIN_RULES",
    "CHAP_RE",
    "DEFAULT_RULE_ID",
    "RuleError",
    "all_rules",
    "clean_toc",
    "detect_bytes",
    "get_rule",
    "load_rules_from_texts",
    "parse_chapters",
    "parse_rule",
    "parse_with_rule",
    "slice_chapter",
    "split_fixed",
]
