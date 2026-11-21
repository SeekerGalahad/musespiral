"""按批注 AI 改写用例编排：行号批注列表 → 逐条回应的新正文预览。

从 studio/actions._action_annotate_rewrite 的 job 执行体提取（Phase A
下沉）：批注列表 + 原文正文 → habits 注入 + annotate_rewrite 任务路由
的 AI 改写 → 产出 <stem>.annotated.md 预览文件（frontmatter 沿袭原文，
绝不在原文上落笔）。

与原实现的差异：JobFailure/脱敏/{ok:...} 包装不再出现——AIError/
OSError/FileExistsError 原样上抛，路径返回绝对 Path。批注 sidecar 的读
取是 Studio 数据面（留在表现层），本模块接收已读好的批注列表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter

from ..core.habits import load_habits
from ..infra.ai.habits_inject import load_style_samples, style_block
from ..core.article import _fm_close_index
from ..infra.ai import chat, load_ai_config
from .rewrite_service import _strip_code_fence

# 按批注改写 prompt：行号锚定批注 → AI 逐条回应输出完整新正文
# Phase A 从 actions.py 迁入（单一权威定义，actions 已不保留副本）
_ANNOTATE_PROMPT = """你在按行号批注修改一篇公众号文章。请逐条回应下面的批注，输出修改后的完整 Markdown 正文。

要求：
- 保留原文事实与观点，不编造数据、引文或案例
- 未涉及批注的段落保持原样
- 只输出 Markdown 正文，不要输出 frontmatter，不要解释

批注列表（行号基于当前正文）：
{notes}

当前正文：
---
{body}
---
"""


def _read_article_text(path: Path) -> str:
    """读文章原文字节（禁用换行翻译，CRLF 原样往返）。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _split_head_body(text: str) -> tuple[str, str]:
    """切成 (frontmatter 头含闭合界定符行, 其后正文字节原样)。

    与 studio 批注数据面同一刀法（core.article._fm_close_index 界定），
    行号与批注侧严格对齐；无 frontmatter 读作头空、全文皆正文。
    """
    lines = text.splitlines(keepends=True)
    close = _fm_close_index(lines)
    if close is None:
        return "", text
    return "".join(lines[: close + 1]), "".join(lines[close + 1 :])


def annotate_rewrite(article_path: str | Path, annotations: list[dict[str, Any]]) -> Path:
    """按批注 AI 改写并产出预览文件，返回输出路径。

    预览文件 <stem>.annotated.md 已存在即拒绝覆盖（FileExistsError）；
    批注为空 ValueError；AI 未配置/网络失败抛 ai.AIError；写盘失败抛
    OSError。产物 frontmatter 沿袭原文并记 ai_model。
    """
    path = Path(article_path)
    if not annotations:
        raise ValueError("没有批注可应用：批注列表为空")
    out = path.with_name(f"{path.stem}.annotated.md")
    if out.exists():
        raise FileExistsError(f"预览文件已存在，拒绝覆盖: {out.name}（人工确认后可删除再生成）")
    _, body = _split_head_body(_read_article_text(path))
    notes = "\n".join(
        "{i}. 第 {start}{span} 行：{comment}".format(
            i=index + 1,
            start=item["line_start"],
            span="-" + str(item["line_end"]) if item["line_end"] != item["line_start"] else "",
            comment=item["comment"],
        )
        for index, item in enumerate(annotations)
    )
    messages: list[dict[str, str]] = []
    # F44 style 块（habits+范文现读现拼；两资产全无 → 空串不注入），与 ai.rewrite 同款
    style_section = style_block(load_habits(), load_style_samples())
    if style_section:
        messages.append({"role": "system", "content": style_section})
    messages.append({"role": "user", "content": _ANNOTATE_PROMPT.format(notes=notes, body=body)})
    # 经 infra.ai.chat 统一入口（task=annotate_rewrite 走任务路由）
    content = _strip_code_fence(chat(messages, task="annotate_rewrite", temperature=0.4))
    post = frontmatter.load(path)  # frontmatter 沿袭原文，仅换正文+记模型
    post.content = content
    post.metadata["ai_model"] = load_ai_config("annotate_rewrite").get("model", "")
    out.write_text(frontmatter.dumps(post, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out
