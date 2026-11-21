"""TTS 朗读领域层（G-T5，L2 纯；正典=《musespiral-调研吸收功能设计-2026-11-18》§六）。

两模块零 I/O、零网络、零三方依赖：
  - voice：声音白名单 + 语速串校验/规整（提交期判定的唯一事实源）
  - script：文章解析结果 → 朗读脚本（标题/正文/列表/引用进，代码/表格/图片出）

朗读的**网络出口**在 services/tts_service（edge-tts 包的 Communicate——那是
Microsoft Edge 的 TTS 服务端点，需要网络但无需 API key，**不经 musespiral 的
LLM gateway**：TTS 是外部服务而非 LLM，不进 TASKS/routing）。
"""
from __future__ import annotations

from .script import speech_text
from .voice import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    MP3_MAGIC,
    RATE_MAX_PERCENT,
    VOICES,
    VOICE_NAMES,
    looks_like_mp3,
    normalize_rate,
    validate_voice,
    voice_labels,
)

__all__ = [
    "DEFAULT_RATE",
    "DEFAULT_VOICE",
    "MP3_MAGIC",
    "RATE_MAX_PERCENT",
    "VOICES",
    "VOICE_NAMES",
    "looks_like_mp3",
    "normalize_rate",
    "speech_text",
    "validate_voice",
    "voice_labels",
]
