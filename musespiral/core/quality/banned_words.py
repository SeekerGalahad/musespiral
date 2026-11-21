"""违禁词基础表与逐行扫描（F33A）——恒 WARN 不 FAIL，只标记不否决。

词表 = 内置基础表（绝对化用语 / 广告法高危词，~30 词起步）∪ 用户扩展
~/.musespiral/banned_words.txt（UTF-8 一行一词，# 开头注释行跳过；文件缺失/
损坏时静默只用内置表）。

扫描口径（设计 §二 F33A「正文逐行扫描，代码块内跳过」）：围栏代码块
（``` 或 ~~~ 开闭，同符号开闭——代码不算宣传文案）内跳过；标题行不豁免
（# 开头行照扫——绝对化用语最高发位恰是标题）；命中返回 (词, 行号) 列表，
同一行同一词只报一次；词表按长度降序匹配并遮蔽已命中片段——「全网第一」
不会同时再报「第一」。

红线：纯 stdlib + config.home_dir，零内部依赖、零网络（不接云端词库）。
"""
from __future__ import annotations

from ... import config

USER_FILE = "banned_words.txt"

# 内置基础表（起步表，用户可经 ~/.musespiral/banned_words.txt 扩展；只增不改语义）
BUILTIN_WORDS: tuple[str, ...] = (
    # 绝对化用语（广告法「最高级 / 唯一性」类；「第一」单字过宽易误伤
    # 「第一步/第一道防线」，故只收复合形态）
    "最好", "最佳", "最优", "最强", "最高级", "史上最",
    "唯一", "顶级", "顶尖", "极致", "完美", "绝对", "独家", "首个", "首选",
    "万能", "全网第一", "销量第一", "行业第一", "第一品牌",
    # 广告法高危（疗效 / 收益承诺类）
    "100%", "NO.1", "永久", "彻底", "根治", "治愈", "包治",
    "秒杀", "稳赚", "保本", "零风险", "无效退款", "立竿见影", "祖传秘方",
)


def load_words(user_file=None) -> list[str]:
    """合并词表：内置 ∪ 用户文件（按长度降序，供扫描时长词优先）。"""
    path = user_file if user_file is not None else config.home_dir() / USER_FILE
    words = set(BUILTIN_WORDS)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # 缺文件/不可读 → 只用内置表
        lines = []
    except UnicodeDecodeError:
        lines = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#"):
            words.add(s)
    return sorted(words, key=lambda w: (len(w), w), reverse=True)


def scan(text: str, words: list[str] | None = None) -> list[tuple[str, int]]:
    """逐行扫描正文：返回 (词, 行号) 命中清单（行号为正文行号，1 起）。

    围栏代码块内跳过（``` 或 ~~~ 围栏，须同符号开闭——~~~ 块内的 ``` 行
    是内容不是围栏，反之亦然）；标题行（# 开头）不豁免，照常扫描；同一行
    同一词只报一次。
    """
    if words is None:
        words = load_words()
    hits: list[tuple[str, int]] = []
    fence: str | None = None  # 当前围栏符号（"```"/"~~~"）；None = 不在围栏内
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if fence is None and (line.startswith("```") or line.startswith("~~~")):
            fence = "```" if line.startswith("```") else "~~~"
            continue
        if fence is not None and line.startswith(fence):
            fence = None
            continue
        if fence is not None:
            continue
        rest = raw
        for w in words:
            if w in rest:
                hits.append((w, lineno))
                rest = rest.replace(w, "\x00" * len(w))
    return hits
