"""``tts_service`` —— 兼容门面（W2-2 发布域包化批，2026-11-24）。

实现已迁 ``services/publish/tts_service.py``；本模块 re-export 其**全量符号**
（含单下划线私有名——测试与 studio 调用面零改动）。符号集由
``tests/test_publish_pkg_facade.py`` 运行时对拍锁定（防未来漂移）。
"""
from __future__ import annotations

from .publish.tts_service import (  # noqa: F401
    Any,
    Path,
    SYNTHESIS_BUDGET_SECONDS,
    TTSServiceError,
    TTSSynthesisError,
    TTSUnavailableError,
    _assert_mp3,
    _import_edge_tts,
    annotations,
    looks_like_mp3,
    mp3_path_for,
    normalize_rate,
    parse_article,
    speech_text,
    synthesize,
    synthesize_article,
    validate_voice,
)

__all__ = [
    "Any",
    "Path",
    "SYNTHESIS_BUDGET_SECONDS",
    "TTSServiceError",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "_assert_mp3",
    "_import_edge_tts",
    "annotations",
    "looks_like_mp3",
    "mp3_path_for",
    "normalize_rate",
    "parse_article",
    "speech_text",
    "synthesize",
    "synthesize_article",
    "validate_voice",
]
