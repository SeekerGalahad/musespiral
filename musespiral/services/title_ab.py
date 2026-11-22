"""标题 A/B 三风格候选（P1-B，2026-11-23）——本地预览三选一。

三个标题风格**刻意差异化**（问题式/数字式/情感式）——差异化是本功能的
核心价值，不是三个相似候选；降级路径同样保持风格互异（启发式三模板各占
一风格，AI 缺位也产出完整三选一面）。

AI 路径：`chat_policed(task="title")` 治理通道（未配置/调用失败静默降级
启发式——core.quality.title「混合管线」同款事实），单次调用产 3 条按序
映射三风格。**不接微信 A/B 测试 API**——做的是「本地预览三选一」，应用
即写回 frontmatter.title（backup_article 版本池先行，title --apply 同款）。

封面 spec 为**本地确定性**产物（零 AI 零落盘）：mp 封面比例 2.35:1
（platforms/profiles cover_ratio 单一事实源）→ 900×383 占位画布 + 风格
色板 + ≤12 字封面短句，供「生成封面」动作做参数底稿；正式封面仍走
`musespiral cover`（本模块不产图）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import frontmatter

from ..core.quality.digest import _first_prose_line, _trim
from ..core.quality.title import _h1
from ..infra.storage.filesystem import backup_article

ChatFn = Callable[..., str]

# 风格轴（顺序即 AI 输出映射顺序，也即启发式产出顺序）
STYLES: tuple[str, ...] = ("问题式", "数字式", "情感式")

_RATIONALE: dict[str, str] = {
    "问题式": "疑问句抛出读者痛点，勾起点开欲——与另两条风格刻意互异",
    "数字式": "具体数字量化内容，显得可执行、有清单感",
    "情感式": "带情绪温度与态度，引发共鸣转发",
}

# mp 封面比例 2.35:1 的整数画布（占位 spec 用；真实尺寸门以 profiles 为准）
_COVER_SIZE = "900x383"
_COVER_TONE: dict[str, dict[str, Any]] = {
    "问题式": {"palette": ["#2563eb", "#dbeafe"], "tone": "冷静提问"},
    "数字式": {"palette": ["#059669", "#d1fae5"], "tone": "量化可信"},
    "情感式": {"palette": ["#dc2626", "#fee2e2"], "tone": "情绪共鸣"},
}

_PROMPT = """你是公众号资深编辑。请为下面的文章出 3 条刻意差异化的 A/B 标题：

1. 问题式：以疑问句抛出读者痛点，勾起点开欲
2. 数字式：带具体数字或量化结果，显得可执行
3. 情感式：带情绪温度或态度，引发共鸣

要求：
- 三条风格必须互异、彼此不重复（这是 A/B 测试的前提）
- 不用夸张营销词（最好/第一/秒杀等广告法违禁词）
- 每条不超过 30 字
- 只输出一个 JSON 数组（3 个字符串，顺序=问题式/数字式/情感式），不要解释文字，不要代码围栏

文章当前标题：{title}
文章正文：
---
{content}
---
"""


def generate_variants(
    article_path: str | Path,
    count: int = 3,
    *,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """生成三风格 A/B 标题候选 → `{source, variants}`。

    - variants：`[{style, title, rationale, cover_spec}]`，cover_spec 为本地
      确定性占位（零 AI 零落盘）；
    - source：`"ai"`（≥1 条 AI 候选被采纳）| `"heuristic"`（AI 未配置/失败
      全启发式兜底）——透明标注候选来源，评审可辨；
    - count：产出条数上限（风格轴只有 3 条，>3 截为 3，<3 取前缀）；
    - 正文与 frontmatter 全空 → ValueError（可读错误，title_service 同款）；
    - chat_fn：chat_policed 测试注入点（None=真实网络）。
    """
    count = max(1, min(count, len(STYLES)))
    path = Path(article_path)
    post = frontmatter.load(path)
    content = post.content.strip()
    fm_title = str(post.metadata.get("title", "")).strip()
    if not fm_title and not content:
        raise ValueError("正文与 frontmatter 全空，无法生成 A/B 标题候选")

    seen: set[str] = set()
    variants: list[dict[str, Any]] = []
    ai_used = False
    if (fm_title or content) and _ai_configured():
        for style, text in zip(STYLES, _ai_titles(fm_title, content, chat_fn)):
            text = str(text or "").strip()
            if text and text not in seen:
                seen.add(text)
                ai_used = True
                variants.append(_variant(style, text))

    topic, n_sections = _topic(fm_title, content)
    for i in range(len(variants), count):
        text = _heuristic_title(STYLES[i], topic, n_sections)
        if text and text not in seen:
            seen.add(text)
            variants.append(_variant(STYLES[i], text))
    return {"source": "ai" if ai_used else "heuristic", "variants": variants}


def apply_variant(article_path: str | Path, title: str) -> str:
    """选中候选写回 frontmatter.title（版本池先行），返回写回文本。

    与 `musespiral title --apply N` 完全同语义：backup_article 入滚动版本池
    （restore 可恢复）→ load→改 title→dumps 整体重写（YAML 注释会丢失——
    既有已知权衡，前端确认弹层已向用户说明）。空标题 ValueError。
    """
    picked = str(title or "").strip()
    if not picked:
        raise ValueError("标题不能为空")
    path = Path(article_path)
    backup_article(path)
    post = frontmatter.load(path)
    post.metadata["title"] = picked
    with path.open("w", encoding="utf-8") as f:
        frontmatter.dump(post, f)
    return picked


# ---- 内部 ----


def _variant(style: str, title: str) -> dict[str, Any]:
    return {
        "style": style,
        "title": title,
        "rationale": _RATIONALE[style],
        "cover_spec": _cover_spec(style, title),
    }


def _cover_spec(style: str, title: str) -> dict[str, Any]:
    """本地确定性封面占位 spec（零 AI 零落盘；正式封面走 musespiral cover）。"""
    spec: dict[str, Any] = {
        "size": _COVER_SIZE,
        "headline": _trim(title, 12),  # 封面短句 ≤12 字（大字排版友好）
        "style": style,
        "note": "本地占位 spec，零落盘；正式封面走 musespiral cover",
    }
    spec.update(_COVER_TONE[style])
    return spec


def _topic(fm_title: str, content: str) -> tuple[str, int]:
    """候选母题（frontmatter title > H1 > 首行）+ 小节数（数字式素材）。"""
    base = fm_title or _h1(content) or _first_prose_line(content)
    topic = base.strip().strip("。！？!?.：:，,、；;～~—-") or "这篇文章"
    n = sum(1 for line in content.splitlines() if line.lstrip().startswith("## "))
    return topic, max(n, 3)


def _heuristic_title(style: str, topic: str, n_sections: int) -> str:
    """启发式三模板（AI 缺位时的差异化兜底——三风格仍互异）。"""
    if style == "问题式":
        return _trim(topic if topic.endswith(("？", "?")) else f"为什么{topic}？")
    if style == "数字式":
        return _trim(f"{n_sections} 个要点，讲透{topic}")
    return _trim(f"终于有人把{topic}讲明白了")


def _ai_configured() -> bool:
    from ..infra.ai import ai_configured

    return ai_configured("title")


def _ai_titles(fm_title: str, content: str, chat_fn: ChatFn | None) -> list[str]:
    """AI 三候选（治理通道 task=title）；未配置外的调用/解析失败 → 空表降级。"""
    from ..infra.ai import AIError
    from ..infra.ai.gateway import chat_policed

    prompt = _PROMPT.format(title=fm_title or "（无）", content=content)
    messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
    try:
        res = chat_policed(
            messages,
            task="title",
            temperature=0.7,
            cache=False,  # 生成类恒不缓存（F79 §八-3）
            template=_PROMPT,  # 内置缺省随调用方传入（gateway 不持业务模板）
            chat_fn=chat_fn,
        )
        items = json.loads(_strip_fence(str(res.get("text") or "")))
    except (AIError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    return [str(x) for x in items[: len(STYLES)]]


def _strip_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return text


__all__ = ["STYLES", "apply_variant", "generate_variants"]
