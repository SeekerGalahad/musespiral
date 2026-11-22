"""质量循环（check → AI 修复 → 再 check）用例编排（P1-A，2026-11-23）。

每轮：`run_checks`（core.quality.checks 既有审核清单）→ 有 FAIL 项且
auto_fix → AI 修复（复用 `chat_policed(task="rewrite")` 治理通道——缓存/
预算/台账五段序，F79 全套机制自动继承）→ 重跑 checks；全过或达到
max_rounds 即停。

**红线：绝不偷偷改用户文章**——修复稿写 `<stem>.quality_fix.md` 预览文件
（frontmatter 沿袭原文，annotate_service 同款刀法），人工确认后手动覆盖；
原文任何一字节不动。预览件是本循环的自有产物，重跑覆盖旧稿（原文不受
影响，无覆盖风险）。

AI 缺位/失败**明确报「AI 不可用」**而非静默假成功：未配置（ai_configured）
在修复前判定；调用失败/空响应（AIError）原样上抛终止循环——已完成的
预览件与修复记录保留，final_result 不伪造 PASS。

局限（如实交代）：AI 修复只改正文——frontmatter/文件级 FAIL（封面缺失/
摘要超限/平台字限等）AI 修不了，循环会照常跑满轮数并在 final_result 里
如实留下这些 FAIL 项（不静默、不假装修复）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import frontmatter

from ..core.content.parser import parse_article
from ..core.habits import load_habits
from ..core.quality.checks import CheckResult, has_failures, run_checks
from ..infra.ai import AIError, ai_configured, load_ai_config
from ..infra.ai.gateway import chat_policed
from ..infra.ai.habits_inject import load_style_samples, style_block
from .annotate_service import _read_article_text, _split_head_body
from .rewrite_service import _strip_code_fence

# 修复预览件命名（<stem>.quality_fix.md；与 .annotated.md 同族衍生后缀，
# 扫描面不作为可发布文章入队列）
FIX_SUFFIX = ".quality_fix.md"

ChatFn = Callable[..., str]

_PROMPT = """你在按发布前检查清单修复一篇公众号文章。请修复下面列出的问题，输出修复后的完整 Markdown 正文。

要求：
- 只修与问题清单相关的部分，其余段落保持原样
- 保留原文事实与观点，不编造数据、引文或案例
- 涉及图片/封面文件本身的问题无法凭空修复：不要伪造文件，可删除失效引用
- 只输出 Markdown 正文，不要输出 frontmatter，不要解释

检查问题清单（FAIL 项）：
{fails}

当前正文：
---
{body}
---
"""


def fix_preview_path(article_path: str | Path) -> Path:
    """修复预览件路径：<stem>.quality_fix.md（与原文同目录）。"""
    path = Path(article_path)
    return path.with_name(path.stem + FIX_SUFFIX)


def loop(
    article_path: str | Path,
    max_rounds: int = 3,
    auto_fix: bool = True,
    *,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """跑质量循环，返回 `{rounds, final_result, fixes_applied}`。

    - rounds：实际跑的检查轮数（首轮检查即全过 → 1）；
    - final_result：最后一轮 `run_checks` 结果（CheckResult 元组列表，原样
      保留领域形态；展示/序列化由调用方处理）；
    - fixes_applied：每轮修复一行摘要（含 FAIL 数与预览件名），空=未修复；
    - auto_fix=False 或首轮全过：只跑一轮检查，AI 零调用；
    - max_rounds < 1 → ValueError（CLI 层 IntRange 已拦，服务层再守一道）；
    - chat_fn：chat_policed 测试注入点（editor_ai_service 同款；None=真实网络）。
    """
    if max_rounds < 1:
        raise ValueError("max_rounds 必须 ≥1")
    path = Path(article_path)
    results = run_checks(parse_article(path), path)
    rounds = 1
    fixes: list[str] = []
    current = path
    while has_failures(results) and auto_fix and rounds < max_rounds:
        if not ai_configured("rewrite"):
            raise AIError(
                "AI 不可用：存在未通过项但 AI 未配置——先 musespiral ai config "
                "配置档案，或用 --no-fix 只看检查报告"
            )
        n_fails = sum(1 for lvl, _ in results if lvl == "FAIL")
        preview = fix_preview_path(path)
        _ai_fix(current, preview, results, chat_fn)
        fixes.append(f"第 {rounds} 轮：{n_fails} 项 FAIL → AI 修复稿 {preview.name}")
        current = preview
        results = run_checks(parse_article(current), current)
        rounds += 1
    return {"rounds": rounds, "final_result": list(results), "fixes_applied": fixes}


def _ai_fix(
    source: Path,
    preview: Path,
    results: list[CheckResult],
    chat_fn: ChatFn | None,
) -> None:
    """按 FAIL 清单 AI 修复正文 → 写预览件（frontmatter 沿袭原文 + 记模型）。"""
    fails = "\n".join(f"- [{lvl}] {msg}" for lvl, msg in results if lvl == "FAIL")
    _, body = _split_head_body(_read_article_text(source))
    messages: list[dict[str, str]] = []
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        messages.append({"role": "system", "content": style_section})
    messages.append({"role": "user", "content": _PROMPT.format(fails=fails, body=body)})
    res = chat_policed(
        messages,
        task="rewrite",
        temperature=0.4,
        cache=False,  # 生成类恒不缓存（F79 §八-3）
        chat_fn=chat_fn,
    )
    content = _strip_code_fence(str(res.get("text") or "")).strip()
    if not content:
        raise AIError("AI 不可用：修复调用返回空响应")
    post = frontmatter.load(source)  # frontmatter 沿袭来源（首轮=原文，之后=预览件自身）
    post.content = content
    post.metadata["ai_model"] = load_ai_config("rewrite").get("model", "")
    preview.write_text(
        frontmatter.dumps(post, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


__all__ = ["FIX_SUFFIX", "fix_preview_path", "loop"]
