"""一稿多态（F14）——一篇文章改写成四平台文字变体（xhs/channels/douyin/zhihu）。

article2media 思路落在文字层：文章读为与风格无关的结构化内容，再按
PLATFORM_SPECS（specs.py）的各平台形态改写（generator.py），产物落盘前
经 validator.py 本地字数兜底。AI 已配置时复用 ai.load_ai_config 与
habits_inject.style_block 风格块注入（风格飞轮跨平台生效）；AI 未配置时 xhs/zhihu
降级为启发式（抽小标题组要点拼装 + 复用原 frontmatter tags），
channels/douyin 口播稿无可靠启发式，抛 AdaptNeedsAI（CLI 层转退出码 1）。

红线：只产出本地 markdown 文件（<stem>.<platform>.md，存在即拒绝覆盖），
人审后才进入 pack/publish，绝不直接发布。

自 musespiral/adapt.py 拆出（后端架构正典 §八 Phase B）；旧路径为 re-export
垫片，消费方 import 零改动。
"""
from __future__ import annotations

from .generator import AdaptNeedsAI, generate
from .specs import PLATFORM_SPECS

__all__ = ["AdaptNeedsAI", "PLATFORM_SPECS", "generate"]
