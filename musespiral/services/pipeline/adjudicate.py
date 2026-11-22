"""pipeline 包：adjudicate（裁决态原语）。

原 services/pipeline_service_plugins.py 按 ARCH-2 拆出的裁决簇。边界
如实说明：AI 三态裁决（draft/adopted/discarded）的 **adopt/discard 端点
实现在 studio 层**（``musespiral/studio/plugin_actions/`` 包（run_adjudicate 域）的三态裁决
端点族，正典=《mdpub-AI产物裁决机制设计-2026-11-21》§2.3），不在服务层
本文件；本簇持有的是服务层的裁决态写原语：

- ``_reset_draft_status``——重生成产物状态重置 draft（裁决机制设计
  §2.3：重生成后用户要重新裁决——即使节点 config.auto_adopt=true 也
  不豁免，重生的语义就是「再来一版待审」）。消费方=regenerate。

包门面与全量符号面见 ``musespiral.services.pipeline`` 的 ``__init__``。
"""
from __future__ import annotations

from typing import Any


def _reset_draft_status(outputs: dict[str, Any]) -> int:
    """重生成产物状态重置 draft（裁决机制设计 §2.3：重生成后用户要重新
    裁决——即使节点 config.auto_adopt=true 也不豁免，重生的语义就是
    「再来一版待审」）。只动 AI 裁决端口（ranges 逐项 / article 与顶层
    article_status），其余端口原样；返回触达项数。"""
    touched = 0
    ranges = outputs.get("ranges")
    if isinstance(ranges, list):
        for item in ranges:
            if isinstance(item, dict):
                item["status"] = "draft"
                touched += 1
    article = outputs.get("article")
    if isinstance(article, dict):
        article["status"] = "draft"
        touched += 1
    if "article_status" in outputs:
        outputs["article_status"] = "draft"
    return touched
