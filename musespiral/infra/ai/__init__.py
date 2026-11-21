"""AI 基础设施子包（Phase B 自 musespiral/ai.py 拆分，行为零变更）。

- profiles：ai.json 档案状态（纯文件读写，零网络）
- client：统一 chat 入口 + 流式解析（网络接缝，_post_chat_stream 为测试 mock 点）
- prompts / check / habits_inject：F47 覆盖级联 / F45 连通检查 / F44 风格注入
- cache / usage / budget / gateway：Phase D F79 治理四件（缓存/台账/日预算
  + chat() 之上的唯一组合层）。**刻意不在此 re-export**——消费方须显式
  `from ..infra.ai.gateway import chat_policed`，让「谁在用治理层」在 import
  行上可审计（零迁移承诺的可见化：既有六消费方直调 chat 一目了然）。

旧路径 musespiral/ai.py 为兼容垫片（rewrite 仍留在彼处，Phase C 迁 services/），
消费方 import 零改动。
"""
from .client import _post_chat_stream, _sse_lines, adapt_mock, chat, chat_stream
from .profiles import (
    TASKS,
    AIError,
    _ai_path,
    _load_state,
    ai_configured,
    list_profiles,
    load_ai_config,
    save_ai_config,
    save_ai_profile,
    set_default_profile,
    set_route,
)

__all__ = [
    "AIError",
    "TASKS",
    "_ai_path",
    "_load_state",
    "_post_chat_stream",
    "_sse_lines",
    "adapt_mock",
    "ai_configured",
    "chat",
    "chat_stream",
    "list_profiles",
    "load_ai_config",
    "save_ai_config",
    "save_ai_profile",
    "set_default_profile",
    "set_route",
]
