"""素材正文抽取（L3 单一口径）——F57 refine 与 Phase D F76/F77 共用同一份实现。

**为什么抽公共**（设计 §二-F76.2「同族两面」表）：refine（F57）与 synthesis
（F76）/ tag_suggest（F77）的正文抽取口径必须逐字一致——同一条素材，摘要看到
的正文与综合/标签看到的正文不能是不同的截取规则，否则「AI 对同一素材的理解
前后矛盾」将无从归因。F57 的实现原地提取到本模块，`refine_service._material_text`
退化为 `RefineError` 域包装（错误类归属服务域，抽取逻辑不重复）。

抽取级联（M0 口径，与 F57 落地时逐字一致）：

  1. `payload.text`（crawled/derived 等自带正文）；
  2. `payload.local_path`（kb 类引用不复制红线——正文在 KB 根下原文，读时
     解析：显式根 > sources.json kb 条目 config.root > config.knowledge_root()
     级联，单一权威=`services/sources_service.kb_root`）；
  3. 两者皆无 → `MaterialTextError`（可读错误，含 type——radar_signal/link
     只有引用元数据，无正文面）。

超长截断由**调用方按自己的预算**决定（refine 20k / extract 步 2000 / 标签
4000）——本模块只提供 `max_chars` 截断入口，不内置任何一家的预算常量，
避免「谁的预算才是默认」的隐含优先级。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class MaterialTextError(ValueError):
    """素材无可摘正文（可读消息；调用方按域归一到 RefineError/TagError/AgentServiceError）。"""


def kb_root() -> Path | None:
    """KB 根解析（`sources_service.kb_root` 单一权威级联的薄壳）。"""
    from . import sources_service

    return sources_service.kb_root()


def material_text(material: dict[str, Any], *, max_chars: int | None = None) -> str:
    """提取素材可摘正文（payload.text > payload.local_path@KB 根）。

    无正文面（radar_signal/link 等只有引用元数据）→ MaterialTextError 可读
    错误。`max_chars` 给定且正文超长时截断到该长度（不做标注——预算标注由
    调用方的 prompt 组装层负责，本模块只管取正文）。
    """
    payload = material.get("payload") or {}
    text = payload.get("text")
    if isinstance(text, str) and text.strip():
        body = text.strip()
    else:
        local = payload.get("local_path")
        if not (isinstance(local, str) and local.strip()):
            raise MaterialTextError(
                f"素材无可摘正文（type={material.get('type')} 只有引用元数据；"
                f"面向 text/kb/derived/crawled 等有正文档素材）"
            )
        root = kb_root()
        path = (root / local.strip()) if root is not None else None
        if path is None or not path.is_file():
            raise MaterialTextError(
                f"素材引用的原文不存在: {local.strip()!r}（KB 根未配置或文档已被移动）"
            )
        try:
            body = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError as exc:
            raise MaterialTextError(f"素材原文不可读: {exc}") from None
        if not body:
            raise MaterialTextError("素材原文为空文件，无可摘内容")
    if max_chars is not None and max_chars > 0 and len(body) > max_chars:
        body = body[:max_chars]
    return body


__all__ = ["MaterialTextError", "kb_root", "material_text"]
