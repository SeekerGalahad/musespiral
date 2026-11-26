"""素材库服务（ARCH-2 拆分后的包门面）——``materials/`` 单一持有者。

原 ``services/materials_service.py``（1492 行单文件）按 ARCH-2（正典=
《musespiral-极限架构与功能扩展总规划-2026-11-21》§ARCH-2，沿 2026-11-19
R-1 compose 拆分模式）拆为七簇：crud（存储原语/CRUD/人物卡）/ triage
（分诊窄门/标签频次/置信度/KB 星标与 P19 迁移）/ search（查询谓词/发现页
评分现算/drafted 推导）/ upload（图片·文档上传/视频登记/文件服务三道门）/
spark（灵感速记）/ feedback（文章反哺三路径/内部源刷新）/ iterations
（打磨版本链读面——自 refine_service 迁入，链写面 adopt 仍在 refine）。

本 ``__init__`` 是包门面：re-export 拆分前 materials_service 全量符号
（含下划线私有名——测试与 studio 调用面零改动，``__all__`` 锁定 85 名）；
旧路径 ``services/materials_service.py`` 保留为兼容层再转发本包。运行期
注入缝 ``_chat_policed``（distill 的 AI 入口）由兼容门面 ``__setattr__``
同步转发到真实消费子模块 feedback（compose_service 对 _chat/_chat_stream
同款机制）。

素材库数据面与读写纪律（原模块头正典，随簇迁入各子模块）：追加式 JSONL、
同 id 末次胜出、immutable 原始 payload、删除=tombstone、分诊走 triage
窄门——语义逐字未动，本包只改落点。
"""
from __future__ import annotations

# ---- 与拆分前 materials_service 顶层 import 面一致（门面契约：dir() 等价）----
import hashlib
import ipaddress
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ... import config
from ...core.discovery import scanner, scorer
from ...core.materials import model
from ...core.materials import confidence as confidence_mod  # N-4 低置信阈值单一权威
from ...core.materials import search as mat_search
from ...infra.ai.gateway import chat_policed as _chat_policed  # distill 注入缝（refine 的 _chat 同款）
from ...infra.storage.jsonl import read_rows as _jsonl_rows
from ...jobs.store import now_iso as _now_iso

# ---- 七簇（ARCH-2 职责边界）----
from .crud import (  # noqa: F401
    MATERIALS_DIR,
    MATERIALS_FILE,
    NOVEL_SOURCE_ID,
    STARS_FILE,
    _append,
    _character_body,
    _character_uri,
    _read_index,
    _read_rows,
    _sorted_active,
    add_character,
    add_many,
    add_material,
    cascade_preview,
    delete_material,
    deleted_ids,
    get_character,
    get_detail,
    get_material,
    is_character,
    list_characters,
    list_materials,
    materials_path,
)
from .triage import (  # noqa: F401
    _confidence_of,
    _load_p19_stars,
    migrate_p19_stars,
    set_kb_starred,
    tag_counts,
    update_triage,
)
from .search import (  # noqa: F401
    _frontmatter_source,
    _kb_root_for_scoring,
    _score_kb_material,
    _sort_scored,
    drafted_uris,
    is_drafted,
    query,
    query_scored,
)
from .upload import (  # noqa: F401
    UPLOAD_SOURCE_ID,
    VIDEO_SOURCE_ID,
    _DOC_EXTRACT_PLUGIN,
    _DocExtractCtx,
    _FILE_SUFFIX_WHITELIST,
    _VIDEO_DOMAINS,
    _VIDEO_TEXT_MAX,
    _doc_extract_mod,
    _load_doc_extract,
    _normalize_video_url,
    _video_platform,
    add_uploaded,
    add_uploaded_document,
    add_uploaded_image,
    read_material_file,
    register_local_video,
    register_video,
)
from .spark import (  # noqa: F401
    SPARK_SOURCE_ID,
    add_spark,
    is_spark,
)
from .feedback import (  # noqa: F401
    ARTICLE_SOURCE_ID,
    DEFAULT_DISTILL_PROMPT,
    DISTILL_MAX_POINTS,
    DISTILL_TASK,
    _article_file,
    _article_text,
    _distill_article,
    _parse_points,
    add_from_article,
    refresh_internal_articles,
)
# 迭代链读面（ARCH-2 自 refine_service 迁入）：命名空间不进 __all__——拆分前
# materials_service 面上没有这七个名字（它们原属 refine_service），门面以
# 拆分前全集为口径；消费方仍走 refine_service（已 re-import）或本子模块。
from . import iterations  # noqa: F401

# 同名消歧：上面 `from .triage import …` 的子模块装载副作用会把包属性
# `triage` 重绑到本包子模块；拆分前命名空间里的 `triage` 指
# core.materials.triage（域层分诊模块，dir() 契约）——最后再绑回来。
from ...core.materials import triage  # noqa: F401

__all__ = [
    "ARTICLE_SOURCE_ID",
    "Any",
    "DEFAULT_DISTILL_PROMPT",
    "DISTILL_MAX_POINTS",
    "DISTILL_TASK",
    "MATERIALS_DIR",
    "MATERIALS_FILE",
    "NOVEL_SOURCE_ID",
    "Path",
    "SPARK_SOURCE_ID",
    "STARS_FILE",
    "UPLOAD_SOURCE_ID",
    "VIDEO_SOURCE_ID",
    "_DOC_EXTRACT_PLUGIN",
    "_DocExtractCtx",
    "_FILE_SUFFIX_WHITELIST",
    "_VIDEO_DOMAINS",
    "_VIDEO_TEXT_MAX",
    "_append",
    "_article_file",
    "_article_text",
    "_character_body",
    "_character_uri",
    "_chat_policed",
    "_confidence_of",
    "_distill_article",
    "_doc_extract_mod",
    "_frontmatter_source",
    "_jsonl_rows",
    "_kb_root_for_scoring",
    "_load_doc_extract",
    "_load_p19_stars",
    "_normalize_video_url",
    "_now_iso",
    "_parse_points",
    "_read_index",
    "_read_rows",
    "_score_kb_material",
    "_sort_scored",
    "_sorted_active",
    "_video_platform",
    "add_character",
    "add_from_article",
    "add_many",
    "add_material",
    "add_spark",
    "add_uploaded",
    "add_uploaded_document",
    "add_uploaded_image",
    "annotations",
    "cascade_preview",
    "confidence_mod",
    "config",
    "delete_material",
    "deleted_ids",
    "drafted_uris",
    "get_character",
    "get_detail",
    "get_material",
    "hashlib",
    "ipaddress",
    "is_character",
    "is_drafted",
    "is_spark",
    "json",
    "list_characters",
    "list_materials",
    "mat_search",
    "materials_path",
    "migrate_p19_stars",
    "model",
    "query",
    "query_scored",
    "re",
    "read_material_file",
    "register_local_video",
    "refresh_internal_articles",
    "register_video",
    "scanner",
    "scorer",
    "set_kb_starred",
    "tag_counts",
    "tempfile",
    "triage",
    "update_triage",
    "urlsplit",
]
