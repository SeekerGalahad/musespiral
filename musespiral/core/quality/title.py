"""标题多候选优选（F32）——title 候选生成，镜像 digest（F10）范式。

候选 = AI 3 条（ai.chat(task="title")，TASKS 白名单含 title 键；未配置或
调用/解析失败静默降级）+ 启发式 ≥2 条（H1 原文 / 首句钩子截断 / frontmatter
title+tags 重组）——无 AI 配置时也可用。

--platform 字限过滤：字限全部取 platforms/profiles PLIMITS 既有键，不新造
第二套字限表——mp 用 title_max_bytes 按字节判定（checks.py 标题检查同口径），
其余平台用 title_max_chars / title_min_chars 按字数判定；超限候选直接不
产出（前移拦截，不留 pack 事后 FAIL）。

红线：本模块只产本地结果，绝不写 check 门；infra.ai 懒导入
（suggester.ai_enhance 同款模式，纯启发式路径不触碰 infra）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from .digest import _first_prose_line, _parse_candidates, _prose_sentences, _trim

# 无 --platform 时启发式候选的内部截断长度（生成器行为，非平台字限表）
DEFAULT_MAX_CHARS = 30

_PROMPT = """你是公众号资深编辑。请为下面的文章写 3 条备选标题（title），风格各不相同：

1. 直陈式：一句话说清文章解决什么问题
2. 数字式：带具体数字或量化结果
3. 悬念式：抛出反常识点或问题，勾起点开欲

要求：
- 每条不超过 {limit_hint}
- 彼此不重复，不用夸张营销词（最好/第一/秒杀等广告法违禁词）
- 只输出一个 JSON 数组（3 个字符串），不要解释文字，不要代码围栏

文章当前标题：{title}
文章正文：
---
{content}
---
"""

_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class TitleCandidate:
    """一条标题候选：文本 + 来源标注（AI / 启发式·H1 / 启发式·首句钩子 / 启发式·tags重组）。"""

    text: str
    source: str


def title_limit(platform: str | None) -> dict | None:
    """取平台标题字限（PLIMITS 唯一事实源）。

    返回 {"max_bytes": N}（mp 字节口径）或 {"max_chars": N, "min_chars": M}
    （字数口径，min_chars 缺省无下限）；platform 为 None 返回 None（不过滤）。
    未知平台由 profiles.get_limits 抛 ValueError。
    """
    if platform is None:
        return None
    from ...platforms import profiles  # domain 同层，惰性导入（pack_checks 注入惯例）

    lim = profiles.get_limits(platform)
    if "title_max_bytes" in lim:
        return {"max_bytes": lim["title_max_bytes"]}
    return {"max_chars": lim.get("title_max_chars"), "min_chars": lim.get("title_min_chars")}


def fits(text: str, limit: dict | None) -> bool:
    """候选是否满足平台字限（mp 字节 / 其余字数；None 恒过）。"""
    if limit is None:
        return True
    if "max_bytes" in limit:
        return len(text.encode("utf-8")) <= limit["max_bytes"]
    max_c, min_c = limit.get("max_chars"), limit.get("min_chars")
    if max_c is not None and len(text) > max_c:
        return False
    return min_c is None or len(text) >= min_c


def describe_limit(platform: str | None) -> str:
    """字限的人类可读描述（AI prompt 与命令行提示共用）。"""
    limit = title_limit(platform)
    if limit is None:
        return f"{DEFAULT_MAX_CHARS} 字"
    if "max_bytes" in limit:
        return f"{limit['max_bytes']} 字节（中文一字三字节）"
    min_c, max_c = limit.get("min_chars"), limit.get("max_chars")
    lo = f"{min_c}-" if min_c is not None else "≤"
    return f"{lo}{max_c} 字"


def candidates(article_path: str | Path, platform: str | None = None) -> list[TitleCandidate]:
    """返回标题候选清单（AI 在前、启发式在后，逐条去重）。

    platform 给定时按 PLIMITS 字限过滤——超限候选直接不产出；首句钩子
    启发式会按字限截断生成合规变体。正文与 frontmatter 全空 → 空列表。
    """
    limit = title_limit(platform)
    max_chars = limit.get("max_chars") if limit and "max_chars" in limit else DEFAULT_MAX_CHARS

    post = frontmatter.load(Path(article_path))
    content = post.content.strip()
    fm_title = str(post.metadata.get("title", "")).strip()

    # AI 3 条（懒导入 infra.ai；未配置/失败静默降级——digest 同款）
    ai_items: list[str] = []
    if (fm_title or content) and _ai_configured():
        raw = _ai_chat(fm_title, content, platform)
        ai_items = _parse_candidates(raw)[:3]

    out: list[TitleCandidate] = []
    seen: set[str] = set()

    def _add(text: str, source: str) -> None:
        text = text.strip()
        if text and text not in seen and fits(text, limit):
            seen.add(text)
            out.append(TitleCandidate(text=text, source=source))

    for s in ai_items:
        _add(s, "AI")
    h1 = _h1(content)
    if h1:
        _add(h1, "启发式·H1")
    hook = _hook(content, max_chars, limit)
    if hook:
        _add(hook, "启发式·首句钩子")
    combo = _title_tags_combo(fm_title, post.metadata.get("tags"), max_chars)
    if combo:
        _add(combo, "启发式·tags重组")
    if len(out) < 2 and fm_title:
        # 兜底保底（§二 F32「启发式 ≥2 条」）：正文无 H1/无首句/无 tags 或材料
        # 不足两条时，frontmatter title 原文补位为候选（与既有候选去重）
        _add(fm_title, "启发式·frontmatter")
    return out


def annotate(candidate: TitleCandidate, platform: str | None) -> str:
    """候选的字数标注：字数（mp 追加字节数）。"""
    n = len(candidate.text)
    if platform == "mp":
        return f"{n}字/{len(candidate.text.encode('utf-8'))}字节"
    return f"{n}字"


def _ai_configured() -> bool:
    from ...infra.ai import ai_configured

    return ai_configured("title")


def _ai_chat(fm_title: str, content: str, platform: str | None) -> str:
    from ...infra.ai import AIError, chat
    from ...infra.ai.habits_inject import load_style_samples, style_block
    from ..habits import load_habits

    prompt = _PROMPT.format(
        limit_hint=describe_limit(platform), title=fm_title or "（无）", content=content
    )
    # F44 style 块（habits+范文现读现拼；两资产全无 → 空串不注入，v1 注入形态）
    messages: list[dict[str, str]] = []
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        messages.append({"role": "system", "content": style_section})
    messages.append({"role": "user", "content": prompt})
    try:
        return chat(messages, task="title", temperature=0.7)
    except AIError:
        return ""


def _h1(content: str) -> str:
    m = _H1_RE.search(content)
    return m.group(1).strip() if m else ""


def _hook(content: str, max_chars: int, limit: dict | None = None) -> str:
    """首句钩子：正文首个完整句（无则首行），按 max_chars 截断。

    字节限平台（mp）按字节限截断生成合规变体，而非超限整条丢弃
    （C3：保「无 AI 候选 ≥2」——30 字中文首句 90 字节超 mp 64 字节限，
    旧口径下启发式钩子会被 fits 整条滤掉）。
    """
    sentences = _prose_sentences(content)
    raw = sentences[0] if sentences else ""
    if not raw:
        line = _first_prose_line(content)
        raw = line or ""
    if not raw:
        return ""
    if limit is not None and "max_bytes" in limit:
        return _trim_bytes(raw, limit["max_bytes"])
    return _trim(raw, max_chars)


def _trim_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节限截断（中文一字三字节；省略号 … 计 3 字节）。

    在字符边界切（绝不劈开多字节字符），保证产物再过 fits 的字节判定。
    """
    s = re.sub(r"\s+", " ", text).strip()
    if len(s.encode("utf-8")) <= max_bytes:
        return s
    out: list[str] = []
    used = 3  # 预留省略号字节
    for ch in s:
        n = len(ch.encode("utf-8"))
        if used + n > max_bytes:
            break
        out.append(ch)
        used += n
    return "".join(out).rstrip() + "…"


def _title_tags_combo(fm_title: str, tags: object, max_chars: int) -> str:
    """frontmatter title + tags 关键词重组（最多 3 个标签，缺 title 或 tags 返回空）。"""
    if not fm_title:
        return ""
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        tag_list = [str(t).strip() for t in tags if str(t).strip()]
    else:
        tag_list = []
    if not tag_list:
        return ""
    return _trim(fm_title + "｜" + "·".join(tag_list[:3]), max_chars)
