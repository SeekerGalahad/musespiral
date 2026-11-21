"""笔记→文章 AI 改写用例（Phase C 自顶层 musespiral/ai.py 迁入，行为零变更）。

ai.py 拆分归位（正典 §八）：配置面 infra/ai/profiles、网络面 infra/ai/
client、本用例（AI 改写产本地草稿）归 services/。_strip_code_fence 一并
迁入（annotate_service 复用同款围栏剥离）。

Hard rule: this module only produces a local article file for human review.
It never publishes. key 永不回显、不进日志。
"""
from __future__ import annotations

from pathlib import Path

import frontmatter

from ..core.habits import load_habits
from ..infra.ai.habits_inject import load_style_samples, style_block
from ..infra.ai import chat, load_ai_config
from ..infra.sources.markdown_dir import _auto_digest, _first_line_title

_PROMPT = """你是资深公众号作者。请把下面的个人笔记改写成一篇适合微信公众号发布的文章。

要求：
- 保留原文事实与观点，不编造数据、引文或案例
- 结构清晰：一个吸引人的开头、2-4 个带小标题的主体段落、一个收束结尾
- 语言自然、口语化但不失专业，避免营销腔和空泛排比
- 全文控制在 {words} 字左右
- 只输出 Markdown 正文，不要输出 frontmatter，不要解释

原始笔记：
---
{body}
---
"""


def rewrite(
    note_path: str | Path,
    out_path: str | Path,
    *,
    words: int = 800,
    theme: str = "lapis",
    cover: str = "./assets/cover.png",
) -> Path:
    """Rewrite a note into an article file. Requires configured AI credentials."""
    src = Path(note_path)
    dst = Path(out_path)
    if dst.exists():
        raise FileExistsError(f"目标文章已存在，拒绝覆盖: {dst}")

    post = frontmatter.load(src)
    body = post.content.strip()
    prompt = _PROMPT.format(words=words, body=body)
    messages: list[dict[str, str]] = []
    # F44 style 块（habits+范文现读现拼；两资产全无 → 空串不注入），沿 v1 注入形态
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        messages.append({"role": "system", "content": style_section})
    messages.append({"role": "user", "content": prompt})

    content = _strip_code_fence(chat(messages, task="rewrite", temperature=0.6))
    title = str(post.metadata.get("title") or _first_line_title(content) or src.stem)
    new_post = frontmatter.Post(
        content,
        title=title,
        author=str(post.metadata.get("author", "")),
        digest=_auto_digest(content),
        cover=cover,
        theme=theme,
        code_theme=str(post.metadata.get("code_theme", "monokai")),
        tags=post.metadata.get("tags", []),
        status="draft",
        ai_model=load_ai_config("rewrite").get("model", ""),
    )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(frontmatter.dumps(new_post, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dst


def _strip_code_fence(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return text
