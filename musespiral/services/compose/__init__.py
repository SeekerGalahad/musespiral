"""创作台编排服务（第十八期 §2.3/§2.4/§2.6 正典）——``<slug>.compose/`` 目录
单一持有者（§2.7-Q1）。

R-1（2026-11-19）拆分：原 ``services/compose_service.py`` 1734 行单文件按
六簇真实职责边界拆为本包——store（会话存储/CRUD/锁/原子写）/ prompt
（prompt 组装/插槽/_fill_slots/各 block 渲染）/ summary（滚动摘要+小说前情，
两者同族）/ generate（生成/三态裁决/双模型）/ outline（大纲六函数）/
characters（人物卡+冲突检测）。本 ``__init__`` 是包门面：re-export 拆分前
全量符号（含下划线私有名——测试与 studio 调用面零改动）；旧路径
``services/compose_service.py`` 保留为兼容层再转发本包。

编排 core/composition 四模块 + discovery scanner（章节勾选）+ infra/ai
（chat task="compose"）+ F43 版本池（adopt 前入池）。CLI 与 Studio 都只调
本层。

动作面（§2.3）：
  new(slug, title)                建会话（session.json + 空 draft.md）
  add_source(slug, path, sections) 素材入池（章节勾选复用 discovery scanner）
  remove_source(slug, path)       素材撤池（F-C：sources 删条+原子重写，
                                  返回被移除源勾选章节——连坐文案数据面）
  generate(slug, job_id=None)     prompt 管线 → chat(compose) → 定界解析 →
                                  匹配继承 → rounds/N.md 即时落盘
  decide(slug, round, block, …)   逐块三态裁决（N.decisions.json +
                                  feedback.jsonl 追加）
  adopt(slug)                     三态合并 → draft.md 原子写+版本池 →
                                  habits learn(adopt_edited 差分) → 正稿
                                  入队列（source/source_extra 归因）
  status/list/detail              读面

prompt 组装管线（顺序固定，全部插槽化——F47 prompts.d 覆盖级联）：
  [任务底座模板（含输出协议指令）] → [{{style}} 风格块（P17 第七注入任务）]
  → [{{sources}} 素材节选（勾选章节，预算 2000/素材+8000/会话）]
  → [{{rolling}} 滚动摘要（G-T4 §五：会话有 rolling_summary 即注入，
  空则整节缺席）] → [{{draft}} 当前真稿（分块带锚）] → [{{decisions}} 上轮
  逐块裁决（含 discard 方向）+ 最近 K=2 轮块级 diff 摘要]

G-T4 滚动摘要（设计 §五）：正稿字数每跨 1500 字 → AI 读已写全文 → 输出
<200 字脉络 → 存 session.json rolling_summary → **后续所有 compose/
generate 的 prompt 自动携带**（build_prompt 的 {{rolling}} 插槽 + 缺槽
回补）。触发点是 adopt 后异步（不阻塞主流程）；手动刷新/用户编辑走
refresh_rolling_summary / set_rolling_summary。

E-5 小说模式（正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》
§3.6）：kind=novel 会话多两份上下文——**前情提要**（AI 摘要前 N-1 章 →
session.json novel_summary，**与 rolling_summary 并存**：rolling 记近期
脉络、novel 记此前各章情节账本，两者各有独立插槽 {{novel}}/{{rolling}}）
与**人物卡**（素材库 meta.sub_type=character，会话挂 id 列表 → 生成时
按五键渲染注入 {{characters}}）。生成后跑**冲突检测**（说话风格规则 ×
对话行 → ⚠️ 提示，**只提示不拦截**，新端点在 services/novel_service）。
生成入口 refresh_novel_summary / set_novel_summary / add_character /
remove_character / set_session_kind。

prompt 组装管线（E-5 后的固定序，见 _SLOT_ORDER）：style → sources →
novel → characters → rolling → draft → decisions。

红线：AI 产出永远先落 rounds/N.md 预览态，「采纳入稿」（adopt）才写
draft.md——块级的人工最后一步。
"""
from __future__ import annotations

# ---- 与拆分前 compose_service 顶层 import 面一致（门面契约：dir() 等价）----
import functools
import json
import re
import threading
from pathlib import Path

from ...core.composition import (
    KIND_ARTICLE,
    PER_SOURCE_BUDGET,
    ROLLING_SUMMARY_STEP,
    ROUND_CONTEXT_K,
    SESSION_BUDGET,
    SESSION_KINDS,
    STATUS_ADOPTED,
    STATUS_APPROVED,
    STATUS_COMPOSING,
    STATUS_EXPANDED,
    STATUS_PENDING,
    Block,
    CompositionSession,
    OutlineSection,
    Round,
    SourceRef,
    blocks_from_markdown,
    markdown_from_blocks,
    match_round,
    outline_from_json,
    outline_to_json,
    parse_ai_output,
    validate_sections,
)
from ...core.composition import (
    apply_decisions as _apply_decisions,
)
from ...core.composition import (
    excerpt_sources as _excerpt_sources,
)
from ...core.composition import (
    habits_pairs as _habits_pairs,
)
from ...core.composition import (
    prior_chapters_text as _prior_chapters_text,
)
from ...core.composition import (
    render_character as _render_character,
)
from ...core.composition import (
    round_diff_summary as _round_diff_summary,
)
from ...core.composition import (
    select_sections as _select_sections,
)
from ...core.composition import (
    validate_decision as _validate_decision,
)
from ...core.discovery import scanner
from ...core.habits import extract_habits, save_habits
from ... import config
from ...infra.ai import chat as _chat
from ...infra.ai import chat_stream as _chat_stream
from ...infra.ai import load_ai_config
from ...infra.ai.habits_inject import load_style_samples, style_block
from ...infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ...infra.storage.filesystem import backup_article as _backup_article
from ...jobs.store import now_iso as _now_iso
from ..discover_service import DEFAULT_ARTICLES_ROOT  # noqa: F401（门面转出口径）

# ---- 六簇（R-1 职责边界）----
from .store import (  # noqa: F401
    _META_LOCKS,
    _META_LOCKS_GUARD,
    _SLUG_RE,
    _draft_path,
    _load_decisions,
    _load_session,
    _meta_lock,
    _meta_path,
    _overlay_origins,
    _read_json,
    _rounds_dir,
    _save_meta,
    _serialized,
    _working_blocks,
    _working_markdown,
    _write_json_atomic,
    DECISIONS_SUFFIX,
    DRAFT_FILE,
    FEEDBACK_FILE,
    META_FILE,
    ROUNDS_DIR,
    SESSION_DIR_SUFFIX,
    ComposeError,
    add_source,
    list_sessions,
    list_source_sections,
    new_session,
    remove_source,
    session_detail,
    session_dir,
    sessions_root,
    status,
)
from .prompt import (  # noqa: F401
    _EMPTY_DECISIONS,
    _EMPTY_DRAFT,
    _EMPTY_SOURCES,
    _SLOT_HEADERS,
    _SLOT_ORDER,
    _SLOT_TOKEN_RE,
    _fill_slots,
    _format_decisions,
    _load_habits_for_style,
    DEFAULT_TEMPLATE,
    build_messages,
    build_prompt,
    load_task_template,
)
from .summary import (  # noqa: F401
    _NOVEL_HEADER,
    _ROLLING_HEADER,
    _clean_novel_summary,
    _clean_summary,
    _novel_messages,
    _rolling_input_text,
    _rolling_lock_for,
    _rolling_messages,
    NOVEL_SUMMARY_INPUT_MAX,
    NOVEL_SUMMARY_MAX,
    NOVEL_SUMMARY_TEMPLATE,
    ROLLING_SUMMARY_INPUT_MAX,
    ROLLING_SUMMARY_MAX,
    ROLLING_SUMMARY_TEMPLATE,
    article_chars,
    maybe_refresh_after_adopt,
    novel_block,
    refresh_novel_summary,
    refresh_rolling_summary,
    rolling_block,
    rolling_due,
    rolling_level,
    set_novel_summary,
    set_rolling_summary,
)
from .generate import (  # noqa: F401
    DUAL_DRAFT_A,
    DUAL_DRAFT_B,
    adopt,
    apply_dual_pick,
    blocks_so_far,
    decide,
    dual_compose,
    generate,
    keep_both_drafts,
)
from .outline import (  # noqa: F401
    _OUTLINE_MARKER_RE,
    _load_outline,
    _outline_path,
    _parse_outline_lines,
    _save_outline,
    _section_summary,
    OUTLINE_EXPAND_TEMPLATE,
    OUTLINE_FILE,
    OUTLINE_GEN_TEMPLATE,
    expand_section,
    outline_approve,
    outline_generate,
    outline_get,
    outline_save,
    outline_update_section,
)
from .characters import (  # noqa: F401
    _CHARACTERS_HEADER,
    _session_characters,
    add_character,
    characters_block,
    novel_chapter_stats,
    remove_character,
    scan_conflicts,
    session_characters,
    set_session_kind,
)

__all__ = [
    "Block",
    "ComposeError",
    "CompositionSession",
    "DECISIONS_SUFFIX",
    "DEFAULT_ARTICLES_ROOT",
    "DEFAULT_TEMPLATE",
    "DRAFT_FILE",
    "DUAL_DRAFT_A",
    "DUAL_DRAFT_B",
    "FEEDBACK_FILE",
    "KIND_ARTICLE",
    "META_FILE",
    "NOVEL_SUMMARY_INPUT_MAX",
    "NOVEL_SUMMARY_MAX",
    "NOVEL_SUMMARY_TEMPLATE",
    "OUTLINE_EXPAND_TEMPLATE",
    "OUTLINE_FILE",
    "OUTLINE_GEN_TEMPLATE",
    "OutlineSection",
    "PER_SOURCE_BUDGET",
    "Path",
    "ROLLING_SUMMARY_INPUT_MAX",
    "ROLLING_SUMMARY_MAX",
    "ROLLING_SUMMARY_STEP",
    "ROLLING_SUMMARY_TEMPLATE",
    "ROUNDS_DIR",
    "ROUND_CONTEXT_K",
    "Round",
    "SESSION_BUDGET",
    "SESSION_DIR_SUFFIX",
    "SESSION_KINDS",
    "STATUS_ADOPTED",
    "STATUS_APPROVED",
    "STATUS_COMPOSING",
    "STATUS_EXPANDED",
    "STATUS_PENDING",
    "SourceRef",
    "_CHARACTERS_HEADER",
    "_EMPTY_DECISIONS",
    "_EMPTY_DRAFT",
    "_EMPTY_SOURCES",
    "_META_LOCKS",
    "_META_LOCKS_GUARD",
    "_NOVEL_HEADER",
    "_OUTLINE_MARKER_RE",
    "_ROLLING_HEADER",
    "_SLOT_HEADERS",
    "_SLOT_ORDER",
    "_SLOT_TOKEN_RE",
    "_SLUG_RE",
    "_apply_decisions",
    "_atomic_write",
    "_backup_article",
    "_chat",
    "_chat_stream",
    "_clean_novel_summary",
    "_clean_summary",
    "_draft_path",
    "_excerpt_sources",
    "_fill_slots",
    "_format_decisions",
    "_habits_pairs",
    "_load_decisions",
    "_load_habits_for_style",
    "_load_outline",
    "_load_session",
    "_meta_lock",
    "_meta_path",
    "_novel_messages",
    "_now_iso",
    "_outline_path",
    "_overlay_origins",
    "_parse_outline_lines",
    "_prior_chapters_text",
    "_read_json",
    "_render_character",
    "_rolling_input_text",
    "_rolling_lock_for",
    "_rolling_messages",
    "_round_diff_summary",
    "_rounds_dir",
    "_save_meta",
    "_save_outline",
    "_section_summary",
    "_select_sections",
    "_serialized",
    "_session_characters",
    "_validate_decision",
    "_working_blocks",
    "_working_markdown",
    "_write_json_atomic",
    "add_character",
    "add_source",
    "adopt",
    "apply_dual_pick",
    "article_chars",
    "blocks_from_markdown",
    "blocks_so_far",
    "build_messages",
    "build_prompt",
    "characters_block",
    "config",
    "decide",
    "dual_compose",
    "expand_section",
    "extract_habits",
    "functools",
    "generate",
    "json",
    "keep_both_drafts",
    "list_sessions",
    "list_source_sections",
    "load_ai_config",
    "load_style_samples",
    "load_task_template",
    "markdown_from_blocks",
    "match_round",
    "maybe_refresh_after_adopt",
    "new_session",
    "novel_block",
    "novel_chapter_stats",
    "outline_approve",
    "outline_from_json",
    "outline_generate",
    "outline_get",
    "outline_save",
    "outline_to_json",
    "outline_update_section",
    "parse_ai_output",
    "re",
    "refresh_novel_summary",
    "refresh_rolling_summary",
    "remove_character",
    "remove_source",
    "rolling_block",
    "rolling_due",
    "rolling_level",
    "save_habits",
    "scan_conflicts",
    "scanner",
    "session_characters",
    "session_detail",
    "session_dir",
    "sessions_root",
    "set_novel_summary",
    "set_rolling_summary",
    "set_session_kind",
    "status",
    "style_block",
    "threading",
    "validate_sections",
]
