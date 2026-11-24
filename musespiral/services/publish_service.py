"""``publish_service`` —— 兼容门面（W2-2 发布域包化批，2026-11-24）。

实现已迁 ``services/publish/publish_service.py``；本模块 re-export 其**全量符号**
（含单下划线私有名——测试与 studio 调用面零改动）。符号集由
``tests/test_publish_pkg_facade.py`` 运行时对拍锁定（防未来漂移）。
"""
from __future__ import annotations

from .publish.publish_service import (  # noqa: F401
    CheckResult,
    DRAFT,
    ParsedArticle,
    Path,
    REVIEWED,
    Renderer,
    _gate,
    annotations,
    auto_mark_reviewed,
    check_publish_status,
    default_log_path,
    get_access_token,
    has_failures,
    parse_article,
    publish_draft,
    publish_flow,
    read_status,
    render_article,
    render_checks,
    run_checks,
    run_publish_gate,
    set_publish_url,
    set_status,
    submit_and_track,
    submit_publish,
)

__all__ = [
    "CheckResult",
    "DRAFT",
    "ParsedArticle",
    "Path",
    "REVIEWED",
    "Renderer",
    "_gate",
    "annotations",
    "auto_mark_reviewed",
    "check_publish_status",
    "default_log_path",
    "get_access_token",
    "has_failures",
    "parse_article",
    "publish_draft",
    "publish_flow",
    "read_status",
    "render_article",
    "render_checks",
    "run_checks",
    "run_publish_gate",
    "set_publish_url",
    "set_status",
    "submit_and_track",
    "submit_publish",
]
