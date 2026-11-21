"""服务层（L3：应用编排）——Phase A 建立。

正典（docs/项目方案/musespiral-后端架构正典-2026-11-14.md）§二/§三：
- 编排多个 Domain/infra 模块完成一个用例；本层不含业务规则、不做 HTTP
  响应包装、不知道 Studio（不 import musespiral.studio / musespiral.commands）；
- Phase A 过渡期：core/infra 尚未迁位，暂用现有模块路径（checklist=检查门、
  parser=解析、adapt/pack/ai/token/publisher/stats/url_fetch=infra）；
  Phase B/C 迁位后仅改本包内 import，导出签名不变。

各服务导出（表现层只允许用这些入口）：
- publish_service：run_publish_gate / publish_draft / submit_and_track
- adapt_service：generate_variant / generate_batch
- pack_service：build_pack_for
- annotate_service：annotate_rewrite
- fetch_service：fetch_to_draft
- rewrite_service：rewrite（笔记→文章 AI 改写）
"""
from __future__ import annotations

from .adapt_service import BatchAdaptError, generate_batch, generate_variant
from .annotate_service import annotate_rewrite
from .fetch_service import fetch_to_draft
from .pack_service import build_pack_for
from .publish_service import publish_draft, run_publish_gate, submit_and_track
from .rewrite_service import rewrite

__all__ = [
    "BatchAdaptError",
    "annotate_rewrite",
    "build_pack_for",
    "fetch_to_draft",
    "generate_batch",
    "generate_variant",
    "publish_draft",
    "rewrite",
    "run_publish_gate",
    "submit_and_track",
]
