"""发布域包（W2-2 领域包化批，2026-11-24）。

收编 7 个散在 services/ 根目录的发布域服务：
  publish_service      发布编排（建草稿/提交发表）
  pack_service         manual_pack 物料包（README/payload/checks/cover）
  adapt_service        平台适配（一稿多态文字变体）
  site_service         静态站导出（HTML+index+RSS）
  tts_service          朗读 MP3（edge-tts）
  publish_log_service  发布日志（周历/stats 数据源）
  cover_service        封面五路线派生

**为什么是这 7 个**：它们同属「文章 → 对外形态」的转化链，彼此共享 frontmatter
口径与版本池纪律；且实测消费面均 <30 处（包化风险可控）。
**不包化的**（消费面过大、平铺已够）：materials_service(51 refs) /
pipeline_service(25) / compose_service(25) —— 沿 D1 裁决。

旧路径服务文件保留为薄 facade（见 services/<name>.py），消费方零改动。
"""
from __future__ import annotations

from .adapt_service import *  # noqa: F401,F403
from .cover_service import *  # noqa: F401,F403
from .pack_service import *  # noqa: F401,F403
from .publish_log_service import *  # noqa: F401,F403
from .publish_service import *  # noqa: F401,F403
from .site_service import *  # noqa: F401,F403
from .tts_service import *  # noqa: F401,F403
