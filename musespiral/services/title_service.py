"""标题优选用例编排（F32 下沉，E1 金路径 title 步）。

金路径五步中唯一含 LLM 的一步：`core.quality.title.candidates` 内部经
`ai.chat(task="title")` 取 3 条 AI 候选（未配置/调用失败静默降级为启发式
≥2 条）——评审修正后的「混合管线」事实（组合批评审记录 §一 评审点 2）。
本服务只做「取候选 → 选第一条 → 写回 frontmatter.title + 版本池备份」的
用例编排，不定义候选生成规则、不直接调 AI HTTP（与 adapt/cover 服务同纪律）。

写回语义与 `musespiral title --apply 1` 完全一致：先 `backup_article` 入滚动
版本池（restore 可恢复），再 load→改 title→dumps 整体重写（YAML 注释会
丢失——与 digest --apply 同款已知权衡，CLI 已向用户说明）。
"""
from __future__ import annotations

from pathlib import Path

import frontmatter

from ..core.quality.title import candidates
from ..infra.storage.filesystem import backup_article


def improve_title(
    article_path: str | Path,
    *,
    platform: str | None = None,
) -> str:
    """取第一条标题候选写回 frontmatter，返回写回的标题文本。

    - 候选来自 `core.quality.title.candidates`（AI 优先、启发式兜底；无 AI
      配置也能产出——LLM 步自带降级，金路径不会因未配 AI 卡在 title）；
    - 正文与 frontmatter 全空 → 无候选，抛 ValueError（可读错误，管线
      失败即停并保留已完成步）；
    - 写回前当前版入版本池（幂等可重跑，restore 可恢复）。
    """
    items = candidates(article_path, platform=platform)
    if not items:
        raise ValueError("正文与 frontmatter 全空，无法生成标题候选")
    picked = items[0].text
    path = Path(article_path)
    backup_article(path)  # F43 滚动版本池（title --apply 同款 helper）
    post = frontmatter.load(path)
    post.metadata["title"] = picked
    with path.open("w", encoding="utf-8") as f:
        frontmatter.dump(post, f)
    return picked


__all__ = ["improve_title"]
