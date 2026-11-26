"""章节切分（L2 纯，零 I/O）——中文/英文章节标题正则族 + 定长回退。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2 第二行：`第[一二三四五六七八九十百千0-9]+[章节回卷集部篇]`、`Chapter \\d+`、
`CHAPTER`、自定义规则；`命中率 <3 章 → 按固定字数切块（如 5000 字/块）并如实
标注「未识别章节结构」`。

为什么逐行匹配而不是 `re.MULTILINE + ^...$`：`\\s` 在 Python 里含 `\\n`，
`^\\s*第…` 会把匹配起点拉回上一行空白、`start` 偏移就错了——而 start/end
是本线唯一的数据（正文按偏移现取、永不复制），偏移错一格全书都错。逐行
切分 + 行首偏移累加是唯一不易错的形态。

为什么标题行还额外过滤（长度上限 / 句读结尾）：「第三章讲的是他如何离开」
这种散文句以「第三章」开头是真实存在的（尤其读书笔记），只按前缀匹配会把
正文切成假章节。设计给的 `.{0,40}` 上限是同一个意图，这里再加「句读结尾
不算标题」一条——两层都是启发式，失败只影响目录质量，不影响文件安全。
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

# 章节号字符集：阿拉伯数字 + 中文数字（含「两/零/〇」——网文里都有）
_CN_NUM = r"[0-9一二三四五六七八九十百千万两零〇]"

# 规则族（按顺序尝试；同一行只取首个命中的模式，靠 line start 去重）
# 1) 中文通用：第X章/节/回/卷/集/部/篇 + 可选标题（≤40 字）
CN_PATTERN = rf"^第\s*{_CN_NUM}+\s*[章节回卷集部篇](\s*.{{0,40}})?$"
# 2) 古典回目：第X回 + 空白 + 回目（对仗标题必须存在——`cn-classic` 的判别面）
CN_CLASSIC_PATTERN = rf"^第\s*{_CN_NUM}+\s*回[ 　\t]+\S.{{0,40}}$"
# 3) 英文 Chapter N [+ title]
CHAPTER_EN_PATTERN = r"^chapter\s+\d{1,6}\b.{0,40}$"
# 4) 全大写 CHAPTER [N|罗马数字]（早期排版书常见，与 3 分开以免大小写走样）
CHAPTER_UPPER_PATTERN = r"^CHAPTER\s+(?:\d{1,6}|[IVXLCDM]{1,8})\b.{0,40}$"

CHAP_RE: tuple[str, ...] = (
    CN_PATTERN,
    CN_CLASSIC_PATTERN,
    CHAPTER_EN_PATTERN,
    CHAPTER_UPPER_PATTERN,
)

# 标题行总长上限（含「第X章」前缀；设计给正文部分 ≤40，前缀留 20 余量）
MAX_TITLE_CHARS = 60
# 句读结尾——标题不以这些收尾（散文句的判别信号，见模块头）
PROSE_ENDINGS: tuple[str, ...] = ("。", "，", "！", "？", "；", "：", "…", "、")
# 未识别结构时的定长块（设计 §1.2：5000 字/块）
DEFAULT_CHUNK_CHARS = 5000

_COMPILED: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in CHAP_RE)


def _iter_lines(text: str) -> Iterable[tuple[str, int]]:
    """逐行产出 `(行文本, 行首字符偏移)`——keepends 切分保证偏移与原串对齐。"""
    offset = 0
    for line in text.splitlines(keepends=True):
        yield line.rstrip("\r\n"), offset
        offset += len(line)
    # splitlines 对「以换行结尾」的文本不会产出末尾空行，偏移无需补偿


def _heading_match(stripped: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    """单行是否章节标题（长度/句读两道启发式先于正则，省重复匹配）。"""
    if not stripped or len(stripped) > MAX_TITLE_CHARS:
        return False
    if stripped.endswith(PROSE_ENDINGS):
        return False
    return any(p.match(stripped) is not None for p in patterns)


def compile_rule_regex(pattern: str | None) -> tuple[re.Pattern[str], ...]:
    """规则里的 `chapter_regex` → 编译后元组（None/空串 = 无结构规则）。"""
    if not pattern or not str(pattern).strip():
        return ()
    return (re.compile(str(pattern), re.IGNORECASE),)


def parse_chapters(
    text: str,
    patterns: Sequence[re.Pattern[str]] | None = None,
    *,
    min_chapters: int = 3,
) -> list[dict[str, Any]]:
    """切章节 → `[{n, title, start, end}]`（**字符偏移**，end 为下一章起点）。

    参数：
      - `patterns`：编译好的正则元组；缺省=CHAP_RE 四模式族（内置通用识别）。
        自定义规则传 `compile_rule_regex(rule["chapter_regex"])`。
      - `min_chapters`：命中数下限（设计缺省 3）——不足即返回 `[]`，由上层
        回退 `split_fixed` 并**如实标注「未识别章节结构」**（设计 §1.2 的
        诚实性承诺：宁可承认没认出来，也不把 2 个假标题当目录）。

    返回的 `n` 是**出现序（1 起）**而不是标题里的序号：真实 TXT 里跳号、
    重复号、卷与章混排都会出现，按标题解析数字会让 n 乱序/重号；出现序恒
    单调，前端目录与 `localbook:<id>#<n>` 伪 URL 都靠它定位。

    末章 `end` = 全文长度（含尾部空白——正文渲染时由调用方 strip）。
    第 1 章之前若有前言（卷首语），不单独成章：`start` 从首个标题行开始，
    前言留在 `preface_chars = chapters[0]["start"]` 供上层展示。
    """
    pats = tuple(patterns) if patterns is not None else _COMPILED
    hits: list[tuple[int, str]] = []
    for line, start in _iter_lines(text):
        stripped = line.strip()
        if _heading_match(stripped, pats):
            hits.append((start, stripped))
    if len(hits) < max(1, int(min_chapters)):
        return []
    chapters: list[dict[str, Any]] = []
    for i, (start, title) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        # D2 修复（2026-11-26）：start 跳过标题行本身——读章正文不带「第N章
        # XXX」首行（阅读 app 惯例：标题独立于正文渲染，rd 渲染已自带章名）。
        # title_end = 标题行 + 行尾（CRLF/LF）；start 超 end 时（末章标题后
        # 零内容）回退标题行起点，slice 得空正文。
        title_end = start + len(title)
        while title_end < len(text) and text[title_end] in "\r\n":
            title_end += 1
        body_start = title_end if title_end <= end else start
        chapters.append({"n": i + 1, "title": title, "start": body_start, "end": end})
    return chapters


def split_fixed(
    text: str, *, chars_per_block: int = DEFAULT_CHUNK_CHARS, title_prefix: str = "块"
) -> list[dict[str, Any]]:
    """无章节结构时的定长切块（设计 §1.2 的回退路径）→ 同 shape。

    标题用 `第 N 块`（`title_prefix` 可换），上层同时写 `structure:"fixed"`
    与「未识别章节结构」标注——**块不是章，不冒充章**。
    空文本 → `[]`；不足一块的残文仍成一块（内容不丢优先）。
    """
    size = max(1, int(chars_per_block))
    chapters: list[dict[str, Any]] = []
    if not text:
        return chapters
    n = 1
    offset = 0
    total = len(text)
    while offset < total:
        end = min(total, offset + size)
        chapters.append(
            {"n": n, "title": f"第 {n} {title_prefix}", "start": offset, "end": end}
        )
        n += 1
        offset = end
    return chapters


def parse_with_rule(
    text: str, rule: dict[str, Any]
) -> tuple[list[dict[str, Any]], str, int]:
    """按规则 profile 切章 → `(chapters, structure, hits)`。

    `structure` ∈ `{"chapters", "fixed"}`：`chapters`=规则正则命中够数（真章）；
    `fixed`=未识别章节结构 → 定长块（上层须如实标注——设计 §1.2 的诚实性承诺）。
    `hits` 是正则**原始命中数**（含未达 min_chapters 的短命中）——服务层写进
    meta 供「规则调优」观察（命中 2 章走回退时，用户能看到为什么）。

    split 模式（`plain-split`）与「正则命中不足」走同一回退路径，但 `hits`
    分别是 0 与实测数——两者在 meta 里可区分，不混成一句「没识别」。
    """
    pattern = str(rule.get("chapter_regex") or "")
    if str(rule.get("mode")) == "split" or not pattern.strip():
        chunks = split_fixed(
            text, chars_per_block=int(rule.get("chunk_chars") or DEFAULT_CHUNK_CHARS)
        )
        return chunks, "fixed", 0
    patterns = compile_rule_regex(pattern)
    min_chapters = max(1, int(rule.get("min_chapters") or 3))
    # 先按 min_chapters=1 数原始命中，再看够不够——两次数同一份规则，不重复实现
    raw_hits = parse_chapters(text, patterns, min_chapters=1)
    if len(raw_hits) >= min_chapters:
        return raw_hits, "chapters", len(raw_hits)
    chunks = split_fixed(
        text, chars_per_block=int(rule.get("chunk_chars") or DEFAULT_CHUNK_CHARS)
    )
    return chunks, "fixed", len(raw_hits)


__all__ = [
    "CHAP_RE",
    "CN_CLASSIC_PATTERN",
    "CN_PATTERN",
    "CHAPTER_EN_PATTERN",
    "CHAPTER_UPPER_PATTERN",
    "DEFAULT_CHUNK_CHARS",
    "MAX_TITLE_CHARS",
    "PROSE_ENDINGS",
    "compile_rule_regex",
    "parse_chapters",
    "parse_with_rule",
    "split_fixed",
]
