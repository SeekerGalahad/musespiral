"""章节文本清洗（R3.5，L2 纯函数零 I/O）——设计 §2.1 ①「净化替换
（B 档 replaceRegex 语义简化版）+ 去噪（广告/版权页/乱码行）」。

与 legado 的两条对应关系（前置分析 §6.4-6 / §八-⑤）：

  - 源级 replaceRegex → `extra_rules`（(pattern, replacement) 正则对列表，
    由调用方按书源规则透传——服务端不解析 legado 规则 DSL，只执行已编译
    的替换对；「## 正则」形态由调用方先行拆分）；
  - ContentProcessor 净化替换 → `DEFAULT_AD_PATTERNS`（内置广告行正则表，
    站点通用噪声——「天才一秒记住」「本站最新网址」等阅读站水印）。

清洗序（固定，确定性）：extra_rules → 广告行剔除 → 乱码行剔除 →
空白归一。乱码行判定 = 行内 U+FFFD（解码失败的兜底字符）或 C0/C1 控制
字符占比过高的行。空白归一 = 行首尾去空、连续 ≥2 空行压成 1 空行、
首尾整体修剪。

全部纯函数：同输入恒同输出，无 I/O 无时钟（core/booksource 架构门）。
"""
from __future__ import annotations

import re
from typing import Sequence

# 内置广告行正则（阅读站水印/引流话术；大小写不敏感）。命中**整行剔除**——
# 这些行对素材正文零信息量，剔除即净化（设计 §2.1 ①「去广告行」）。
DEFAULT_AD_PATTERNS: tuple[str, ...] = (
    r"天才一秒记住.{0,20}(本书|本站|首发)",
    r"(本书|本站)(最新|首发)(网址|地址|域名)",
    r"请(收藏|记住).{0,20}(本书|本站)",
    r"(无弹窗|手机版|安卓版).{0,12}(阅读|下载|地址)",
    r"最新章节.{0,10}(请|到).{0,20}",
    r"(pps|ppx)\s*[:：]",
    r"一秒记住.{0,20}(本书|本站)",
)

# 行内「坏字符」占比阈值：U+FFFD 与 C0/C1 控制字符占行内字符比例 ≥ 该值
# 即判乱码行剔除（短行阈值放宽——两个 FFFD 的 6 字行已不可读）。
_BAD_CHAR_RATIO = 0.20
_BAD_CHAR_RATIO_SHORT = 0.34
_SHORT_LINE = 8

# 连续空行的归一目标（中文段落间 1 空行足够）
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _is_ad_line(line: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(p.search(line) for p in patterns)


def _bad_ratio(line: str) -> float:
    """行内 U+FFFD/C0/C1 控制字符占比（0-1）。"""
    if not line:
        return 0.0
    bad = sum(
        1
        for ch in line
        if ch == "\ufffd" or ("\x00" <= ch <= "\x1f" and ch not in "\t") or "\x7f" <= ch <= "\x9f"
    )
    return bad / len(line)


def compile_rules(extra_rules: Sequence[tuple[str, str]]) -> list[tuple[re.Pattern[str], str]]:
    """编译 (pattern, replacement) 对；非法正则 → ValueError（可读含模式）。"""
    out: list[tuple[re.Pattern[str], str]] = []
    for i, pair in enumerate(extra_rules):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError(f"清洗规则[{i}] 必须是 (pattern, replacement) 二元组，实际: {pair!r}")
        pattern, replacement = pair
        if not isinstance(pattern, str) or not isinstance(replacement, str):
            raise ValueError(f"清洗规则[{i}] 的 pattern/replacement 必须是字符串")
        try:
            out.append((re.compile(pattern), replacement))
        except re.error as exc:
            raise ValueError(f"清洗规则[{i}] 正则非法: {pattern!r}（{exc}）") from None
    return out


def clean_chapter(
    text: str,
    extra_rules: Sequence[tuple[str, str]] = (),
    *,
    ad_patterns: Sequence[str] = DEFAULT_AD_PATTERNS,
) -> str:
    """清洗一章原始文本 → 可喂提炼/入库的正文（纯函数）。

    序：extra_rules 逐对 `re.sub`（源级 replaceRegex 简化语义）→ 广告行
    剔除 → 乱码行剔除 → 空白归一。空文本返回空串（调用方按缺章处理）。
    """
    if not text:
        return ""
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern, replacement in compile_rules(extra_rules):
        body = pattern.sub(replacement, body)
    ads = [re.compile(p, re.IGNORECASE) for p in ad_patterns]
    kept: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if _is_ad_line(stripped, ads):
            continue
        ratio = _bad_ratio(stripped)
        threshold = _BAD_CHAR_RATIO_SHORT if len(stripped) < _SHORT_LINE else _BAD_CHAR_RATIO
        if ratio >= threshold:
            continue
        kept.append(stripped)
    out = "\n".join(kept)
    out = _BLANK_RUN_RE.sub("\n\n", out)
    return out.strip()
