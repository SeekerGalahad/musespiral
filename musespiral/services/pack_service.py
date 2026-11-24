"""``pack_service`` —— 兼容门面（W2-2 发布域包化批，2026-11-24）。

实现已迁 ``services/publish/pack_service.py``；本模块 re-export 其**全量符号**
（含单下划线私有名——测试与 studio 调用面零改动）。符号集由
``tests/test_publish_pkg_facade.py`` 运行时对拍锁定（防未来漂移）。
"""
from __future__ import annotations

from .publish.pack_service import (  # noqa: F401
    Any,
    PACK_PLATFORM_CODES,
    PLIMITS,
    PackPreviewError,
    Path,
    _DESC_LINE_RE,
    _PLATFORM_GUIDES,
    _checks_lines,
    _find_article_cover,
    _first_prose_line,
    _material_checks_lines,
    _normalize_limits,
    _normalize_tags,
    _pack_dir,
    _payload_md,
    annotations,
    build_pack,
    build_pack_for,
    diff_result,
    pack_inventory,
    pack_preview,
    parse_article,
    read_pack,
    render_diff_html,
)

__all__ = [
    "Any",
    "PACK_PLATFORM_CODES",
    "PLIMITS",
    "PackPreviewError",
    "Path",
    "_DESC_LINE_RE",
    "_PLATFORM_GUIDES",
    "_checks_lines",
    "_find_article_cover",
    "_first_prose_line",
    "_material_checks_lines",
    "_normalize_limits",
    "_normalize_tags",
    "_pack_dir",
    "_payload_md",
    "annotations",
    "build_pack",
    "build_pack_for",
    "diff_result",
    "pack_inventory",
    "pack_preview",
    "parse_article",
    "read_pack",
    "render_diff_html",
]
