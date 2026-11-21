"""素材库单一持有者（第二十期 §1.2/F56）——索引读写/查询/分诊/删除连坐/迁移。

`~/.musespiral/materials/materials.jsonl`（追加式 JSONL）的全部业务动作经本
模块：CLI 与 Studio 双入口 append 同文件（与 publish_log 同语义，正典
§四-Q5）。读写纪律：

  - 追加原子写（父目录自建 + 行级追加——infra/storage/jsonl 纪律；
    不复用 append_log：其 platform 键注入属发布日志域，会污染 Material
    schema 与整行幂等比较）；
  - 读序合并：同 id 末次胜出（core/materials.model.merge_rows）；
  - immutable 原始：add 校验后追加，此后 payload/provenance 不改写——
    人工动作只经 update_triage（triage 三键窄门）追加新行；
  - 删除=tombstone 行追加（不重写文件），连坐清单当场返回、徽章数据
    随查询就地解析（model.ref_statuses）；
  - M2 增量：add_many=采集批量提交点（失败不半写/墓碑不复活，ingest
    消费）；query_scored=发现页收编后端（查询+评分现算+drafted 推导，
    F56——kb 类沿 F27 现算、radar_signal 取 meta 分/级）；
  - 第二十一期（Phase A 多模态）：add_uploaded_image（图片上传全链，
    F59）/ register_video（视频登记制，F61——零出网）/ read_material_file
    （文件服务三道门，F60）/ query 扩 tag+q+collection（F62/F63/F64）/
    tag_counts（标签频次表，F63）。

迁移三件之二/之三（正典 §1.2 迁移表；执行时点 M4）：
  - migrate_p19_stars：discover_stars.json（P19 收藏，{KB相对路径: ts}）
    → triage.starred——星标文档无对应素材则落一条 kb 引用素材
    （引用不复制：payload.local_path 指回 KB 文档）；
  - drafted 推导面：discovered.json 的 drafted 标记改由「正稿
    frontmatter.source 指回」在查询视图内推导（drafted_uris/is_drafted），
    discovered.json 停写动作与发现页收编（M2/M3）同步切换，M1 只立读侧。
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .. import config
from ..core.discovery import scanner, scorer
from ..core.materials import model, triage
from ..core.materials import confidence as confidence_mod  # N-4 低置信阈值单一权威
from ..core.materials import search as mat_search
from ..infra.ai.gateway import chat_policed as _chat_policed  # distill 注入缝（refine 的 _chat 同款）
from ..infra.storage.jsonl import read_rows as _jsonl_rows
from ..jobs.store import now_iso as _now_iso

# 素材库数据面（config.home_dir() 下）
MATERIALS_DIR = "materials"
MATERIALS_FILE = "materials.jsonl"
# P19 星标旧存储（档A actions.py 同名文件；迁移只读不写）
STARS_FILE = "discover_stars.json"


def materials_path() -> Path:
    """素材索引路径：~/.musespiral/materials/materials.jsonl（随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / MATERIALS_DIR / MATERIALS_FILE


def _read_rows() -> list[dict[str, Any]]:
    """宽容读素材索引（infra/storage/jsonl.read_rows 单一权威实现的薄壳）。"""
    return _jsonl_rows(materials_path())


def _read_index() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """读序合并：({id: 末次行}, 已删 id 集)。"""
    normalized = [n for n in (model.normalize_material(r) for r in _read_rows()) if n is not None]
    index = model.merge_rows(normalized)
    _, deleted = model.split_active(index)
    return index, deleted


def _append(row: dict[str, Any]) -> None:
    """追加一行（父目录自建 + 行级原子追加——infra/storage/jsonl 纪律）。

    不复用 jsonl.append_log：它向行注入 platform 键（发布日志域标记），
    会污染 Material schema 并破坏 add_material 的整行幂等比较。
    """
    path = materials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── 读面（查询/详情）────────────────────────────────────────────────


def _sorted_active(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """活跃素材展示序：fetched_at 降序，同刻按 id 稳定排序。"""
    return sorted(active, key=lambda m: (str(m.get("fetched_at") or ""), m["id"]), reverse=True)


def list_materials() -> list[dict[str, Any]]:
    """全部活跃素材（合并读序后，fetched_at 降序）。"""
    index, _ = _read_index()
    active, _ = model.split_active(index)
    return _sorted_active(active)


def deleted_ids() -> set[str]:
    """已删（墓碑）id 集（连坐徽章数据面）。"""
    return _read_index()[1]


def get_material(material_id: str) -> dict[str, Any] | None:
    """取单条活跃素材；不存在或已删返回 None（删后详情走徽章语义非静默）。"""
    index, deleted = _read_index()
    if material_id in deleted or material_id not in index:
        return None
    row = index[material_id]
    if model.is_tombstone(row):
        return None
    return row


def get_detail(material_id: str) -> dict[str, Any] | None:
    """素材详情：素材本体 + 血统引用就地状态（「素材已删」徽章，正典 §1.2）
    + 所属集合清单（第二十一期 F62 连坐声明姊妹面，只读）。"""
    material = get_material(material_id)
    if material is None:
        return None
    index, _ = _read_index()
    active_ids = {mid for mid, row in index.items() if not model.is_tombstone(row)}
    from . import collections_service  # 函数体内导入（层内解耦）

    return {
        **material,
        "references": model.ref_statuses(material, active_ids),
        "collections": collections_service.collections_of(material_id),
    }


def query(
    *,
    starred: bool | None = None,
    read: bool | None = None,
    enabled: bool | None = None,
    mtype: str | None = None,
    sub_type: str | None = None,
    source_id: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    collection: str | None = None,
    confidence: str | None = None,
) -> list[dict[str, Any]]:
    """素材查询（全参数正交 AND：分诊 + 类型/子类型/源/标签/集合 + 搜索谓词）。

    第二十一期扩展（F62/F63/F64）：tag=triage.tags 成员判定；collection=
    手工集合活跃成员集（集合不存在 → 空集，宽容读）；q=词面搜索（最后
    应用——先过滤后搜索谓词，正典 §二-F64）。
    N-2 扩展（2026-11-21）：sub_type=payload.meta.sub_type 判别——spark
    chip 筛选读 sub_type 而非 type（裁决：类型枚举不动，语义在 meta 内）；
    未携带 sub_type 键的存量素材在该过滤下天然不命中（普通 text 不受影响）。
    N-4 扩展（2026-11-21）：confidence='low' → triage.confidence 非 None 且
    < confidence.CONFIDENCE_LOW_THRESHOLD（0.5）——只筛「AI 给过低分」的；
    confidence=None（未自评）与 verified=true 的素材不命中（三态语义：
    低置信与已核验互斥）；枚举外取值 ValueError（400 呈现，不静默恒真）。
    """
    rows = triage.filter_materials(list_materials(), starred=starred, read=read, enabled=enabled)
    if mtype is not None:
        rows = [m for m in rows if m.get("type") == mtype]
    if sub_type is not None:
        rows = [
            m for m in rows
            if ((m.get("payload") or {}).get("meta") or {}).get("sub_type") == sub_type
        ]
    if source_id is not None:
        rows = [m for m in rows if (m.get("origin") or {}).get("source_id") == source_id]
    if tag is not None:
        rows = [m for m in rows if triage.matches(m, tags=tag)]
    if collection is not None:
        from . import collections_service  # 函数体内导入（层内解耦）

        members = collections_service.active_member_ids(collection)
        rows = [m for m in rows if m["id"] in members]
    if confidence is not None:
        if confidence != "low":
            raise ValueError(f"confidence 只支持 'low' 筛选: {confidence!r}")
        rows = [
            m for m in rows
            if _confidence_of(m) is not None
            and _confidence_of(m) < confidence_mod.CONFIDENCE_LOW_THRESHOLD
            and not (m.get("triage") or {}).get("verified", False)
        ]
    if q:
        rows = [m for m in rows if mat_search.matches_query(m, q)]
    return rows


def _confidence_of(material: dict[str, Any]) -> float | None:
    """素材的 AI 自评置信度（归一后恒合法；防御性再归一供纯读路径）。"""
    value = (material.get("triage") or {}).get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if 0.0 <= value <= 1.0 else None


def tag_counts() -> list[dict[str, Any]]:
    """标签频次表（F63：全库现算，count 降序、同频按名升序——确定性终值）。"""
    counts: dict[str, int] = {}
    for material in list_materials():
        tags = (material.get("triage") or {}).get("tags")
        if not isinstance(tags, list):
            continue  # 宽容读：脏数据按无标签
        for tag in tags:
            if isinstance(tag, str) and tag:
                counts[tag] = counts.get(tag, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# ── 发现页收编后端（F56：查询 + 评分现算，M2）───────────────────────


def _kb_root_for_scoring(kb_root: str | Path | None) -> Path | None:
    """评分用 KB 根解析（sources_service.kb_root 单一权威级联的薄壳）。"""
    from ..services import sources_service  # 函数体内导入（层内解耦）

    return sources_service.kb_root(kb_root)


def _score_kb_material(material: dict[str, Any], kb_root: Path | None, families: set[str] | None) -> dict[str, Any] | None:
    """kb 类 text 素材评分现算：scanner 重扫原文 → F27 scorer（正典 §二-F56）。

    文档缺失（KB 侧已删/移动）→ None（视图按无分沉底，不炸查询）；评分是
    现算派生值，不回写素材库（immutable 红线——分值随文档与新鲜度演进）。
    """
    if kb_root is None:
        return None
    uri = str((material.get("origin") or {}).get("uri") or "")
    if not uri:
        return None
    path = kb_root / uri
    if not path.is_file():
        return None
    try:
        meta = scanner.scan_file(path)
    except OSError:
        return None
    return scorer.score_doc(meta, families=families)


def query_scored(
    *,
    articles_root: str | Path,
    kb_root: str | Path | None = None,
    families: set[str] | None = None,
    mtype: str | None = None,
    sub_type: str | None = None,
    source_id: str | None = None,
    starred: bool | None = None,
    read: bool | None = None,
    enabled: bool | None = None,
    tag: str | None = None,
    q: str | None = None,
    collection: str | None = None,
    confidence: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """发现页（素材视图）查询面：materials 查询 + 评分现算 + drafted 推导。

    评分口径（正典 §二-F56「评分现算」）：
      - radar_signal：分值/评级取自 payload.meta（采集时落的 grade/score），
        附 grade 键供四维条与「查看原文」直跳（meta.url）；
      - kb 类 text 素材：沿 F27 评分函数对原文现算（_score_kb_material），
        附 score_breakdown 四维；
      - 其余类型（link/crawled/derived/非 kb 源 text/第二十一期 image/
        video）：无分（None，沉底——正典 §1.4 枚举零变更不新造评分）。

    排序：score 降序（无分按 -1 沉底）→ fetched_at 降序 → id 升序（确定性
    终值）。drafted=正稿 frontmatter.source 指回判定（is_drafted 双形态）。
    families 缺省经 discover_service.load_family_titles(kb_root) 载入（词典
    缺失 None——F27 契约）；kb_root 缺省 config.knowledge_root()。limit
    钳制 [1, ∞)（0/负数按未限）。tag/q/collection 为第二十一期正交过滤
    （F62/F63/F64，全参数 AND，先过滤后搜索谓词）。sub_type 为 N-2 扩展
    （2026-11-21，spark chip 读 meta.sub_type 非 type）。confidence 为 N-4
    扩展（'low' 低置信筛选，query() 同语义）。total=命中总数，
    **不随 limit 截断缩水**（F64 截断可见性：total>limit 时前端须显「前
    N 条/共 total」）。
    """
    from ..services import sources_service  # 函数体内导入（导入期解耦）

    rows = query(
        mtype=mtype, sub_type=sub_type, source_id=source_id, starred=starred,
        read=read, enabled=enabled,
        tag=tag, q=q, collection=collection, confidence=confidence,
    )
    drafted = drafted_uris(articles_root)
    root = _kb_root_for_scoring(kb_root)
    if families is None and root is not None and root.is_dir():
        from ..services import discover_service

        families = discover_service.load_family_titles(root)
    source_types = {s["id"]: s.get("type") for s in sources_service.list_sources()}

    items: list[dict[str, Any]] = []
    for m in rows:
        row = dict(m)
        row["drafted"] = is_drafted(m, drafted, kb_root=root)
        meta = (m.get("payload") or {}).get("meta") or {}
        if m.get("type") == "radar_signal":
            try:
                row["score"] = int(meta.get("score") or 0)
            except (TypeError, ValueError):
                row["score"] = 0
            row["grade"] = str(meta.get("grade") or "")
        elif m.get("type") == "text" and source_types.get((m.get("origin") or {}).get("source_id")) == "kb":
            scored = _score_kb_material(m, root, families)
            if scored is not None:
                row["score"] = scored["score"]
                row["score_breakdown"] = scored["score_breakdown"]
            else:
                row["score"] = None
        else:
            row["score"] = None
        items.append(row)

    items = _sort_scored(items)
    total = len(items)  # 命中总数（先于截断——F64 截断可见性契约）
    if limit is not None and int(limit) > 0:
        items = items[: int(limit)]
    return {"items": items, "total": total}


def _sort_scored(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """展示序（三趟稳定排序合成多向序）：score 降序 → fetched_at 降序 → id 升序。"""
    ordered = sorted(items, key=lambda m: m["id"])
    ordered.sort(key=lambda m: str(m.get("fetched_at") or ""), reverse=True)
    ordered.sort(key=lambda m: -(m["score"] if isinstance(m.get("score"), int) else -1))
    return ordered


# ── 写面（落库/分诊/删除）──────────────────────────────────────────


def add_material(material: dict[str, Any]) -> dict[str, Any]:
    """落库一条素材（归一→校验→幂等判定→追加；返回落库/既有行）。

    - 归一：normalize_material 先行（N-4 起 triage 六键——老调用方携带
      四键 triage 的行补齐 verified/confidence 缺省后再比再存，追加强制
      写归一形态，文件内单形）；坏行（None）→ ValueError 可读；
    - 校验：model.validate_material 空错误清单方可入册（ValueError 带清单）；
    - 幂等：同 id 活跃行已存在且整行相等 → 零追加返回既有行（重采/重迁移
      天然收敛）；不相等 → ValueError（immutable 红线：payload 不许改写）；
    - 已删同 id（墓碑）→ 允许追加复活（追加式纪律的自然行为，删除可逆）。
    """
    normalized = model.normalize_material(material)
    if normalized is None:
        raise ValueError(f"素材行不合法（id 缺失或非对象行）: {material!r}")
    material = normalized
    errors = model.validate_material(material)
    if errors:
        raise ValueError("素材行不合法: " + "; ".join(errors))
    index, _ = _read_index()
    existing = index.get(material["id"])
    if existing is not None and not model.is_tombstone(existing) and existing != material:
        raise ValueError(
            f"素材 id 已存在且内容不同（immutable 红线——payload 落库后不改写）: {material['id']}"
        )
    if existing == material:
        return existing
    _append(material)
    return material


def add_many(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """批量落库（采集提交点，M2 ingest 消费）：全量校验→冲突预检→一次性追加。

    失败不半写（正典 §五 ingest 行）：任何一行校验失败、或与库内活跃行同 id
    异内容（immutable 冲突）、或批内同 id 异内容，都在**追加第一行之前**
    raise ValueError——库内零残留。与 add_material 的三点差异（采集语义）：

      - 墓碑 id 不复活：用户删除过的素材重采跳过（采集不得与人工删除打架；
        add_material 的复活语义留给迁移/人工路径）；
      - 批内同 id 等值行去重（kb 双子源重叠扫描天然收敛）；批内同 id 异内容
        仍 ValueError（调用方去重责任——ingest 对跨线同仓取首线）；
      - 单次读索引（add_material 逐行读，批量路径 O(1) 次读）。

    返回 {"added": 新增行数, "skipped": 幂等跳过行数（等值/墓碑）}。

    N-4 起逐行先 normalize_material 归一再校验/比较（与 add_material 同因：
    老调用方四键 triage 与库内六键归一行对比恒等收敛，重采不误报 immutable
    冲突）；批内比较也在归一形态上进行。
    """
    seen: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    skipped = 0
    for i, raw in enumerate(rows):
        normalized = model.normalize_material(raw)
        if normalized is None:
            raise ValueError(f"批量第 {i + 1} 行素材不合法（零写入，id 缺失或非对象行）: {raw!r}")
        row = normalized
        errors = model.validate_material(row)
        if errors:
            raise ValueError(f"批量第 {i + 1} 行素材不合法（零写入）: " + "; ".join(errors))
        rid = row["id"]
        if rid in seen:
            if seen[rid] != row:
                raise ValueError(f"批量内素材 id 重复且内容不同（零写入）: {rid}")
            skipped += 1  # 批内等值去重（kb 双子源重叠扫描收敛）
            continue
        seen[rid] = row
        ordered.append(row)
    index, deleted = _read_index()
    pending: list[dict[str, Any]] = []
    for row in ordered:
        rid = row["id"]
        if rid in deleted:
            skipped += 1  # 墓碑：删除优先，不复活
            continue
        existing = index.get(rid)
        if existing is not None:
            if existing != row:
                raise ValueError(
                    f"素材 id 已存在且内容不同（immutable 红线，批量零写入）: {rid}"
                )
            skipped += 1
            continue
        pending.append(row)
    for row in pending:
        _append(row)
    return {"added": len(pending), "skipped": skipped}


def update_triage(material_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """分诊更新：triage 窄门（core.triage.triage_patch 五键——三布尔+tags+
    verified，confidence 不入人工窄门）+ 追加全量新行。

    新行=当前行原样 + 补丁后 triage（payload/provenance 逐字节不动——
    immutable 由「只改 triage 后整行重写」的构造路径保证）；同 id 末次
    胜出读序下旧行自然失效。素材不存在/已删 → ValueError。
    """
    target = get_material(material_id)
    if target is None:
        raise ValueError(f"素材不存在或已删: {material_id!r}")
    new_triage = triage.triage_patch(target.get("triage") or {}, changes)
    if new_triage == target.get("triage"):
        return target  # 零变化零追加（幂等）
    row = {**target, "triage": new_triage}
    _append(row)
    return row


def delete_material(material_id: str) -> dict[str, Any]:
    """删除素材：追加墓碑行 + 当场返回连坐清单（两步 confirm 的声明数据）。

    连坐清单=活跃集中引用该素材的 derived 素材（model.resolve_cascade）；
    引用者本身不删（徽章语义，非级联删除）。重复删除 → ValueError。
    """
    index, deleted = _read_index()
    if material_id in deleted:
        raise ValueError(f"素材已删（墓碑在册）: {material_id!r}")
    if material_id not in index or model.is_tombstone(index[material_id]):
        raise ValueError(f"素材不存在: {material_id!r}")
    active, _ = model.split_active(index)
    cascade = model.resolve_cascade(material_id, active)
    _append(model.make_tombstone(material_id))
    return {
        "deleted": material_id,
        "cascade": [
            {"id": m["id"], "type": m.get("type"), "title": (m.get("origin") or {}).get("title", "")}
            for m in cascade
        ],
    }


def cascade_preview(material_id: str) -> list[dict[str, Any]]:
    """删除连坐预览（M3 两步 confirm 的声明数据面；不落墓碑、纯读）。

    与 delete_material 返回的连坐清单同源同形（model.resolve_cascade 对
    当前活跃集）；素材不存在/已删 → ValueError（调用方 400/404 呈现）。
    """
    index, deleted = _read_index()
    if (
        material_id in deleted
        or material_id not in index
        or model.is_tombstone(index[material_id])
    ):
        raise ValueError(f"素材不存在或已删: {material_id!r}")
    active, _ = model.split_active(index)
    return [
        {"id": m["id"], "type": m.get("type"), "title": (m.get("origin") or {}).get("title", "")}
        for m in model.resolve_cascade(material_id, active)
    ]


# ── 第二十一期 Phase A：图片上传全链（F59）/ 视频登记（F61）──────────


# 上传素材的虚拟源与视频登记的人工源（正典 §1.4 迁移表：均不入 sources.json）
UPLOAD_SOURCE_ID = "upload"
VIDEO_SOURCE_ID = "manual"
# 视频平台域名表（F61：域名后缀匹配，其余 other）
_VIDEO_DOMAINS: tuple[tuple[str, str], ...] = (
    ("bilibili.com", "bilibili"), ("b23.tv", "bilibili"),
    ("youtube.com", "youtube"), ("youtu.be", "youtube"),
)
# 选填文本字段上限（F61：title/note/duration_text 各 ≤200 字符）
_VIDEO_TEXT_MAX = 200


def add_uploaded_image(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """图片上传全链（F59）：体检→落盘→缩略→索引（幂等，同 sha256 同 id 零追加）。

    - 校验链全在 infra.media_store（8MiB 门/魔数嗅探/Pillow verify/尺寸
      真值）；license 枚举外 → ValueError（400）；
    - 幂等锚 uri = upload://sha256:<内容散列>（正典 §1.4）——同图重传同
      id：既有活跃行直接命中返回（dedup=true，jsonl 零追加；文件仓内容
      寻址重写同字节，无 immutable 冲突）；
    - 响应 {material, dedup, thumb_ready}（缩略同步生成，无异步补偿路径）。
    """
    from ..infra import media_store  # 函数体内导入（导入期解耦）

    if license not in model.LICENSES:
        raise ValueError(f"license 必须是 {model.LICENSES} 之一: {license!r}")
    info = media_store.inspect_image(data)
    sha256 = info["sha256"]
    uri = f"upload://sha256:{sha256}"
    mid = model.make_material_id(UPLOAD_SOURCE_ID, "image", uri)
    existing = get_material(mid)
    if existing is not None:
        media_store.store_original(mid, info["ext"], data)  # 内容寻址：重写同字节（文件缺失自愈）
        media_store.make_thumbnail(mid, data)
        return {"material": existing, "dedup": True, "thumb_ready": True}
    material = model.make_material(
        mtype="image",
        source_id=UPLOAD_SOURCE_ID,
        uri=uri,
        title=name.strip() or f"{sha256[:8]}.{info['ext']}",
        license=license,
        payload={
            "local_path": media_store.local_rel_path(mid, info["ext"]),
            "meta": {
                "width": info["width"],
                "height": info["height"],
                "bytes": info["bytes"],
                "format": info["format"],
                "sha256": sha256,
                "license_note": note.strip(),
            },
        },
        fetched_at=fetched_at or _now_iso(),
    )
    media_store.store_original(mid, info["ext"], data)
    media_store.make_thumbnail(mid, data)
    row = add_material(material)
    return {"material": row, "dedup": False, "thumb_ready": True}


# ── 文档上传（2026-11-20 格式生态线 DOC-1）──────────────────────────
# doc-extract 插件目录名带连字符（doc-extract），不能常规 import——importlib
# 按文件位置加载（与 pipeline_service_plugins._load_python_entry 同款），模块级
# 缓存避免每次上传重复 exec。插件 run() 是提取逻辑的**唯一实现**，这里只做
# 「字节→临时文件→run()→material 落库」的宿主编排，不复制任何解析逻辑。
_DOC_EXTRACT_PLUGIN = (
    Path(__file__).resolve().parents[1] / "plugins_builtin" / "doc-extract" / "plugin.py"
)
_doc_extract_mod: Any = None


def _load_doc_extract() -> Any:
    """加载内置 doc-extract 插件 run()（importlib 位置加载 + 模块级缓存）。"""
    global _doc_extract_mod
    if _doc_extract_mod is None:
        import importlib.util  # 函数调用期导入（仅文档上传路径需要）

        spec = importlib.util.spec_from_file_location("musespiral_builtin_doc_extract", _DOC_EXTRACT_PLUGIN)
        if spec is None or spec.loader is None:  # pragma: no cover - importlib 形态防御
            raise ValueError(f"doc-extract 插件无法加载: {_DOC_EXTRACT_PLUGIN}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _doc_extract_mod = module
    return _doc_extract_mod


class _DocExtractCtx:
    """doc-extract.run() 的极简宿主上下文（log/progress/cancelled 三面）。"""

    cancelled = False

    def log(self, msg: str) -> None:  # noqa: D401 - 接口形状
        pass

    def progress(self, done: int, total: int) -> None:
        pass


def add_uploaded_document(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """文档上传全链（DOC-1）：后缀门→doc-extract.run()→text 素材落库（幂等）。

    - 后缀白名单复用 doc-extract.ALLOWED_SUFFIXES（大小写不敏感）；非文档
      后缀（图片等）→ ValueError（400 可读），由 server 按文件名分流；
    - 提取逻辑**完全复用** doc-extract 插件 run()——扫描版 PDF 可读拒绝、
      空结果拒绝、可选依赖缺席指引等纪律全在插件内，这里不重复实现；
    - 幂等锚 uri = upload://sha256:<内容散列>（与图片同 scheme，mtype=text
      使 id 不同）——同文档重传同 id 零追加；原文落 files/ 保血统；
    - 产出 type=text / payload.text=提取正文 → refine 可直接消费（下游联通）。
    """
    from ..core.plugins.context import PluginError

    plugin = _load_doc_extract()
    raw_name = name.strip()
    suffix = Path(raw_name).suffix.lower()
    if suffix not in plugin.ALLOWED_SUFFIXES:
        raise ValueError(
            f"不是可提取的文档格式（{'/'.join(plugin.ALLOWED_SUFFIXES)}）: {raw_name or suffix!r}"
        )
    if license not in model.LICENSES:
        raise ValueError(f"license 必须是 {model.LICENSES} 之一: {license!r}")

    sha256 = hashlib.sha256(data).hexdigest()
    uri = f"upload://sha256:{sha256}"
    mid = model.make_material_id(UPLOAD_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}

    # 字节→临时文件（doc-extract.run 接路径入参；原始字节不落库，仅短暂驻留）
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
            fh.write(data)
            tmp_path = Path(fh.name)
        try:
            out = plugin.run(
                {"doc_path": str(tmp_path), "title": Path(raw_name).stem},
                config_overrides or {},
                _DocExtractCtx(),
            )
        except PluginError as err:
            # 插件可读失败（扫描版/空结果/缺依赖）→ ValueError→server 400 原文呈现
            raise ValueError(str(err)) from None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    text = out["text"]
    pages = out.get("pages")
    from ..infra import media_store  # 函数体内导入（导入期解耦）

    media_store.store_original(mid, suffix.lstrip("."), data)
    meta: dict[str, Any] = {
        "bytes": len(data), "sha256": sha256, "suffix": suffix,
        "filename": raw_name, "license_note": note.strip(),
    }
    if pages is not None:
        meta["pages"] = pages
    material = model.make_material(
        mtype="text",
        source_id=UPLOAD_SOURCE_ID,
        uri=uri,
        title=Path(raw_name).stem or f"{sha256[:8]}{suffix}",
        license=license,
        payload={
            "text": text,
            "local_path": media_store.local_rel_path(mid, suffix.lstrip(".")),
            "meta": meta,
        },
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}


def add_uploaded(
    data: bytes,
    *,
    name: str = "",
    license: str = "UNKNOWN",
    note: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """上传分流（DOC-1）：文档后缀走 doc-extract 提取，其余按图片全链。

    后缀知识收口本层——server 只管字节与 confirm 闸门，不重复解析文件名。
    文档后缀判定复用 doc-extract.ALLOWED_SUFFIXES（唯一事实源）。
    """
    suffix = Path(name.strip()).suffix.lower()
    if suffix in _load_doc_extract().ALLOWED_SUFFIXES:
        return add_uploaded_document(
            data, name=name, license=license, note=note, fetched_at=fetched_at
        )
    return add_uploaded_image(
        data, name=name, license=license, note=note, fetched_at=fetched_at
    )


def _normalize_video_url(url: str) -> tuple[str, str]:
    """视频 URL 校验+规范化（F61 提交期，全 400 语义）→ (uri, 规范化URL)。

    校验：scheme ∈ {http,https}；无 userinfo（user:pass@ 拒）；host 非回环/
    内网（localhost/私网/保留段——防协议注入，不做域名白名单：登记不是
    抓取，任意视频站可登记）。规范化：host 小写 + 去末尾斜杠 + query 原样
    保留（B 站 BV 号在 path、YT t= 在 query，不裁剪；fragment 丢弃）。
    uri = "video://" + 规范化URL（幂等锚，正典 §1.4）。"""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme 必须是 http/https: {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL 缺少主机名")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL 不得携带 user:pass@ 用户信息")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("URL 主机不得是回环地址: localhost")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass  # 非 IP 字面量（域名）：登记零出网，域名解析不在此门
    else:
        if not addr.is_global:
            raise ValueError(f"URL 主机不得是回环/内网/保留地址: {host}")
    netloc = host + (f":{parsed.port}" if parsed.port else "")
    path = parsed.path.rstrip("/")
    normalized = f"{parsed.scheme}://{netloc}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return f"video://{normalized}", normalized


def _video_platform(host: str) -> str:
    """域名表映射（F61）：bilibili.com/b23.tv→bilibili、youtube.com/youtu.be
    →youtube、其余→other（host 后缀匹配，大小写归一由 urlsplit 保证）。"""
    for domain, platform in _VIDEO_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return platform
    return "other"


def register_video(
    url: str,
    *,
    title: str = "",
    note: str = "",
    duration_text: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """视频登记（F61 登记制）：URL 校验→规范化幂等锚→落库。**零出网**——
    纯本地登记，无任何 HTTP 请求（不下载、不解码、无预览帧）。

    同 URL（规范化后）再登记 → dedup=true 返回既有行（零追加）；选填
    字段各 ≤200 字符；title 缺省取 URL 尾段。响应 {material, dedup}。"""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("缺少必填字段 url（http/https 视频 URL）")
    for field, value in (("title", title), ("note", note), ("duration_text", duration_text)):
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是字符串")
        if len(value) > _VIDEO_TEXT_MAX:
            raise ValueError(f"{field} 最长 {_VIDEO_TEXT_MAX} 字符: {len(value)}")
    uri, normalized = _normalize_video_url(url)
    mid = model.make_material_id(VIDEO_SOURCE_ID, "video", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}
    parsed = urlsplit(normalized)
    segments = [seg for seg in parsed.path.split("/") if seg]
    meta: dict[str, Any] = {"platform": _video_platform(parsed.hostname or "")}
    if duration_text.strip():
        meta["duration_text"] = duration_text.strip()
    if note.strip():
        meta["note"] = note.strip()
    material = model.make_material(
        mtype="video",
        source_id=VIDEO_SOURCE_ID,
        uri=uri,
        title=title.strip() or (segments[-1] if segments else parsed.hostname or normalized),
        payload={"url": url.strip(), "meta": meta},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}


# ── E-5 人物卡（2026-11-19，正典=《musespiral-专业创作者视角深度评审与改进-
#    2026-11-18》§3.6「人物卡 = type=character 新素材类型」）────────────
#
# 落地口径：**不新增 MATERIAL_TYPES 取值**——人物卡是 type=text 载体 +
# payload.meta.sub_type=character（正典给了 sub_type=character 与
# meta.character=true 两条等价路，取前者）。理由见 core/materials/model.py
# META_SUB_TYPES 注释：类型枚举是素材库全部读侧的分支面，扩一个只服务小说
# 模式的取值会把空分支散到评分/文件服务/CLI 各处。
#
# CRUD 复用既有 materials 面（正典 §3.6 原文「人物卡 CRUD 复用 materials
# API（sub_type=character）」）：
#   - 建/改：add_character（本函数；同 name 同 id → 幂等返回既有行，
#     异内容 → ValueError「人物卡已存在，请改不同姓名或先删除」——人物卡的
#     可变面是「换一张卡」不是「原地改」（immutable 红线同款））；
#   - 查：list_characters / get_character；删除走既有 delete_material（墓碑）。

NOVEL_SOURCE_ID = "novel"


def _character_uri(name: str) -> str:
    """人物卡幂等锚：``character://<name>``（同名片天然同 id）。"""
    return f"character://{name.strip()}"


def add_character(
    name: str,
    *,
    role: str = "",
    appearance: str = "",
    speech_style: str = "",
    key_events: list[str] | None = None,
    license: str = "UNKNOWN",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """建一张人物卡（E-5 §3.6）→ ``{material, dedup}``。

    校验（core.composition.novel.validate_character 同源）：name 必填非空、
    role/appearance/speech_style 为 str、key_events 为 str 列表；不合法
    → ValueError（studio 400 可读）。同名片重复提交：整行相等 → dedup=true
    返回既有行（幂等，零追加）；内容不同 → ValueError（人物卡不改写——
    要改就删了重建，保持「卡是快照」语义）。
    """
    from ..core.composition import (
        SUB_TYPE_CHARACTER,
        normalize_character,
        validate_character,
    )

    payload = {
        "name": name,
        "role": role,
        "appearance": appearance,
        "speech_style": speech_style,
        # 校验前**不**做 list(...) 归一：str 会被 list() 拆成字符列表而「通过」
        # 字符串数组校验（实测判例：key_events="不是数组" 被吞成 ['不','是',…]）。
        # 原值进校验，通过后才由 normalize_character 归一。
        "key_events": key_events if key_events is not None else [],
    }
    errors = validate_character(payload)
    if errors:
        raise ValueError("人物卡不合法: " + "; ".join(errors))
    if license not in model.LICENSES:
        raise ValueError(f"license 必须是 {model.LICENSES} 之一: {license!r}")
    card = normalize_character(payload)
    meta: dict[str, Any] = {"sub_type": SUB_TYPE_CHARACTER, **card}
    material = model.make_material(
        mtype="text",
        source_id=NOVEL_SOURCE_ID,
        uri=_character_uri(card["name"]),
        title=card["name"],
        license=license,
        # payload.text = 人物卡的 Markdown 速览（读侧/详情面可读；唯一真值是
        # meta 五键——渲染归 core.composition.novel.render_character）
        payload={"text": _character_body(card), "meta": meta},
        fetched_at=fetched_at or _now_iso(),
    )
    mid = material["id"]
    existing = get_material(mid)
    if existing is not None:
        if existing == material:
            return {"material": existing, "dedup": True}
        raise ValueError(
            f"人物卡已存在且内容不同（人物卡是快照，不改写——先删除再重建）: {card['name']}"
        )
    row = add_material(material)
    return {"material": row, "dedup": False}


def _character_body(card: dict[str, Any]) -> str:
    """人物卡 Markdown 速览（payload.text；不参与 prompt——prompt 走五键渲染）。"""
    lines = [f"# {card['name']}" + (f"（{card['role']}）" if card.get("role") else "")]
    if card.get("appearance"):
        lines.append(f"- 外貌：{card['appearance']}")
    if card.get("speech_style"):
        lines.append(f"- 说话风格：{card['speech_style']}")
    for event in card.get("key_events") or []:
        lines.append(f"- {event}")
    return "\n".join(lines)


def is_character(material: dict[str, Any]) -> bool:
    """该素材是否人物卡（meta.sub_type=character）。"""
    meta = (material.get("payload") or {}).get("meta") or {}
    return isinstance(meta, dict) and meta.get("sub_type") == "character"


def get_character(material_id: str) -> dict[str, Any] | None:
    """按素材 id 取人物卡视图（非人物卡/不存在 → None）。"""
    from ..core.composition import character_from_meta

    material = get_material(material_id)
    if material is None or not is_character(material):
        return None
    card = character_from_meta((material.get("payload") or {}).get("meta"))
    card["id"] = material["id"]
    return card


def list_characters() -> list[dict[str, Any]]:
    """全部活跃人物卡（挂卡选择器的数据面；fetched_at 降序同素材序）。"""
    from ..core.composition import character_from_meta

    out: list[dict[str, Any]] = []
    for material in _sorted_active(list_materials()):
        if not is_character(material):
            continue
        card = character_from_meta((material.get("payload") or {}).get("meta"))
        card["id"] = material["id"]
        out.append(card)
    return out


# ── N-2 素材闭环（2026-11-21，正典=《musespiral-素材闭环与专业编辑器设计-
#    2026-11-18.md》§三/§五 P-1 +《musespiral-优化重构与后续开发总体方案-
#    2026-11-19.md》N-2）───────────────────────────────────────────────
#
# 飞轮回程三件：灵感速记（spark）/ 文章反哺（from-article 三路径）/
# 内部源（sources.json type=internal 刷新）。类型枚举不动（用户裁决：
# spark = type=text 载体 + meta.sub_type='spark'，E-5 人物卡同款纪律）。

# 灵感速记虚拟源（设计 §五「origin=manual」）——与 register_video 的
# VIDEO_SOURCE_ID 同值同域：人工录入族共用一个不入 sources.json 的虚拟
# 源（正典 §1.4 迁移表 upload/manual 同款口径，不新增注册表条目）。
SPARK_SOURCE_ID = "manual"
# 文章反哺虚拟源（full/excerpt/distill 三路径产物 origin.source_id）——
# 指回文章走 provenance.chain（from_id=slug，articles_root/<slug>.md 真实
# 可寻址），本 id 只标记「来源族=自家文章」，不入 sources.json。
ARTICLE_SOURCE_ID = "article"


def is_spark(material: dict[str, Any]) -> bool:
    """该素材是否灵感速记（meta.sub_type=spark；is_character 同款判别面）。"""
    meta = (material.get("payload") or {}).get("meta") or {}
    return isinstance(meta, dict) and meta.get("sub_type") == "spark"


def add_spark(
    text: str,
    *,
    title: str = "",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """灵感速记（N-2 ①）：一句话极简创建 → ``{material, dedup}``。

    只需 text，其余自动填：type=text 载体 + meta.sub_type=spark（裁决：
    不新增 MATERIAL_TYPES——读侧未命中者按普通 text 零回归）、origin.
    source_id=manual（不入册虚拟源）、triage 缺省 read=False。title 缺省
    取全文首行压平前 24 字（仅作清单展示——唯一真值是 payload.text）。

    幂等锚 uri = ``spark://sha1:<全文散列>``：同一句话重发同 id——先查
    既有活跃行直接返回 dedup=true（register_video 同款先查后建，重复
    提交/手滑双击天然收敛零追加）。
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("缺少必填字段 text（灵感内容不能为空）")
    text = text.strip()
    uri = f"spark://sha1:{hashlib.sha1(text.encode('utf-8')).hexdigest()}"
    mid = model.make_material_id(SPARK_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"material": existing, "dedup": True}
    material = model.make_material(
        mtype="text",
        source_id=SPARK_SOURCE_ID,
        uri=uri,
        title=title.strip() or text.replace("\n", " ")[:24],
        payload={"text": text, "meta": {"sub_type": "spark"}},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"material": row, "dedup": False}


# ── 文章反哺三路径（N-2 ②，设计 §3.2 路径 A/C + 摘取 B）──────────────

# distill 的 AI 任务键（TASKS 白名单同批扩键；模板覆盖级联 prompts.d/
# material_distill.txt 自动生效——profiles.TASKS 扩键即继承任务面全套机制）
DISTILL_TASK = "material_distill"
# 论点条数上界（正典 §3.2 路径 C「AI 抽 3-5 条核心论点」：解析时钳制 ≤5；
# 少于 3 条不硬拒——AI 偶尔少给时采可用值，0 条才报错）
DISTILL_MAX_POINTS = 5

DEFAULT_DISTILL_PROMPT = (
    "你是编辑助手。阅读下面这篇文章，提炼 3-5 条核心论点，每条一行、"
    "一句独立成立的话（不带编号、不带列表符号、不换行内再分段）。"
    "只输出论点本身，不要任何前言或解释。\n\n"
    "输出末尾单独加一行标注你对自己准确提炼原文的把握，格式固定为"
    "「CONFIDENCE: 0.8」（0-1 小数；不确定时给低值）——该行不算论点。\n\n"
    "【标题】{{title}}\n\n【正文】\n{{body}}\n"
)


def _article_file(slug: str, articles_root: str | Path) -> Path:
    """文章路径解析（articles_root/<slug>.md；不存在 → ValueError 可读）。"""
    if not isinstance(slug, str) or not slug.strip():
        raise ValueError("缺少必填字段 slug（文章标识）")
    slug = slug.strip()
    if "/" in slug or "\\" in slug or slug in (".", ".."):
        raise ValueError(f"slug 必须是文章根内的文件名主干: {slug!r}")
    path = Path(articles_root).expanduser() / f"{slug}.md"
    if not path.is_file():
        raise ValueError(f"文章不存在（articles_root 下无 <slug>.md）: {slug}")
    return path


def _article_text(path: Path) -> tuple[str, str]:
    """读文章 → (frontmatter 表, 正文)（scanner.split_frontmatter 单一口径）。"""
    from ..core.discovery import scanner  # 函数体内导入（导入期解耦）

    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = scanner.split_frontmatter(text)
    return fm, body


def add_from_article(
    slug: str,
    mode: str,
    *,
    articles_root: str | Path,
    start_line: int | None = None,
    end_line: int | None = None,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """文章反哺（N-2 ②）三路径统一入口。

    - mode='full'（路径 A 整篇归档）：正文全文（frontmatter 剥离——
      origin.title/meta 已携带元信息，素材正文不带 YAML 噪声）→ type=text
      素材，provenance.chain=[{from_id: slug, op: published_from}]；
    - mode='excerpt'（路径 B 段落摘取）：正文行区间 [start_line, end_line]
      （1-based 双闭，对剥除 frontmatter 后的正文行计——行号随 frontmatter
      增删不漂移），同 published_from 链；
    - mode='distill'（路径 C 论点提炼）：AI 抽 3-5 条核心论点 → 多条 spark
      （每条 provenance.chain=[{from_id: slug, op: distill}]，license=
      AI-GENERATED + meta.ai_generated——G1 生成物口径）。

    幂等锚：uri = ``article://<slug>``（full）/ ``article://<slug>#L{s}-L{e}``
    （excerpt）/ ``article://<slug>#distill-<i>``（distill 逐条槽位）——
    先查既有活跃行（register_video 同款）：命中返回 dedup=true（immutable
    红线下文章改稿不覆盖既有条，首次采集为准）。

    返回：full/excerpt → ``{mode, material, dedup}``；distill →
    ``{mode, materials, skipped, points}``（materials=本次新增，skipped=
    既有槽位数）。mode 枚举外/区间非法/AI 未配置 → ValueError/AIError
    （studio 转 400 / ok:false 可读面）。
    """
    path = _article_file(slug, articles_root)
    slug = slug.strip()
    fm, body = _article_text(path)
    title = str(fm.get("title") or "").strip() or slug
    if mode == "distill":
        return _distill_article(slug, title, body, fetched_at=fetched_at)
    if mode not in ("full", "excerpt"):
        raise ValueError(f"mode 必须是 full/excerpt/distill 之一: {mode!r}")
    if mode == "excerpt":
        body_lines = body.splitlines()
        if not isinstance(start_line, int) or isinstance(start_line, bool):
            raise ValueError("excerpt 需要整数行区间 start_line")
        if not isinstance(end_line, int) or isinstance(end_line, bool):
            raise ValueError("excerpt 需要整数行区间 end_line")
        if not (1 <= start_line <= end_line <= len(body_lines)):
            raise ValueError(
                f"行区间非法（1 ≤ start ≤ end ≤ {len(body_lines)}）: "
                f"{start_line}-{end_line}"
            )
        text = "\n".join(body_lines[start_line - 1 : end_line])
        uri = f"article://{slug}#L{start_line}-L{end_line}"
        meta: dict[str, Any] = {
            "article_slug": slug, "mode": "excerpt",
            "line_start": start_line, "line_end": end_line, "fm_title": title,
        }
    else:
        text = body
        uri = f"article://{slug}"
        meta = {"article_slug": slug, "mode": "full", "fm_title": title}
    mid = model.make_material_id(ARTICLE_SOURCE_ID, "text", uri)
    existing = get_material(mid)
    if existing is not None:
        return {"mode": mode, "material": existing, "dedup": True}
    material = model.make_material(
        mtype="text",
        source_id=ARTICLE_SOURCE_ID,
        uri=uri,
        title=title,
        license="ALL-RIGHTS",  # 自产文章：权利自有（区别于抓取物 UNKNOWN）
        payload={"text": text, "meta": meta},
        provenance={"chain": [{"from_id": slug, "op": "published_from"}], "derived_from": []},
        fetched_at=fetched_at or _now_iso(),
    )
    row = add_material(material)
    return {"mode": mode, "material": row, "dedup": False}


def _parse_points(reply: str) -> list[str]:
    """AI 回复 → 论点行清单（剥代码围栏/列表符号/编号；空行丢弃）。

    每行一条（模板已约束「每条一行」）；行首的列表符号/编号统一剥除
    （`-`/`*`/`•`/`·`/`1.`/`1、`/`1）` 等形态），钳制 ≤5 条（正典路径 C
    「3-5 条」上界；少于 3 条不硬拒——AI 偶尔少给时采可用值，0 条才报错）。
    """
    from .rewrite_service import _strip_code_fence  # 函数体内导入（同层解耦）

    bullet = re.compile(r"^(?:[-*•·]+|\d{1,2}[.、)）])\s*")
    points: list[str] = []
    for line in _strip_code_fence(str(reply or "")).splitlines():
        cleaned = bullet.sub("", line.strip()).strip()
        if cleaned:
            points.append(cleaned)
    return points[:DISTILL_MAX_POINTS]


def _distill_article(
    slug: str, title: str, body: str, *, fetched_at: str | None
) -> dict[str, Any]:
    """路径 C：AI 提炼论点 → 逐条 spark 落库（dedup 按槽位 first-capture）。"""
    from ..infra.ai import AIError, ai_configured  # 函数体内导入（导入期解耦）

    if not ai_configured(DISTILL_TASK):
        raise AIError(
            "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )
    from ..infra.ai.prompts import load_override  # 函数体内导入（导入期解耦）

    template = load_override(DISTILL_TASK) or DEFAULT_DISTILL_PROMPT
    prompt = template.replace("{{title}}", title).replace("{{body}}", body)
    res = _chat_policed([{"role": "user", "content": prompt}], task=DISTILL_TASK, cache=False)
    # N-4：先剥置信度行再解析论点（容错契约——AI 不带该行时 conf=None 不阻
    # 断面，sparks 照常落库；行混进论点会污染 _parse_points 输出，故先剥）
    reply, conf = confidence_mod.parse_ai_confidence(str(res.get("text") or ""))
    points = _parse_points(reply)
    if not points:
        raise AIError("AI 未返回可用论点（空响应）")
    added: list[dict[str, Any]] = []
    skipped = 0
    for i, point in enumerate(points, 1):
        uri = f"article://{slug}#distill-{i}"
        mid = model.make_material_id(ARTICLE_SOURCE_ID, "text", uri)
        if get_material(mid) is not None:
            skipped += 1  # 槽位已有（重跑提炼不覆盖既有条——immutable 红线）
            continue
        material = model.make_material(
            mtype="text",
            source_id=ARTICLE_SOURCE_ID,
            uri=uri,
            title=point[:24],
            license="AI-GENERATED",
            payload={
                "text": point,
                "meta": {
                    "sub_type": "spark",
                    "ai_generated": True,
                    "article_slug": slug,
                    "distill_slot": i,
                    "fm_title": title,
                },
            },
            provenance={"chain": [{"from_id": slug, "op": "distill"}], "derived_from": []},
            triage={"confidence": conf},  # AI 自评（None=未自评，归一后六键补齐）
            fetched_at=fetched_at or _now_iso(),
        )
        added.append(add_material(material))
    return {"mode": "distill", "materials": added, "skipped": skipped, "points": points}


# ── 内部源刷新（N-2 ④，设计 §3.1：sources.json type=internal）────────


def refresh_internal_articles(
    source_id: str,
    scan_root: str | Path,
    *,
    include_drafts: bool = True,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """内部源刷新：扫文章目录 → 逐篇产 type=text 素材（幂等，按 slug 去重）。

    - 幂等口径：uri=文章 slug → make_material_id 稳定散列；库内已有活跃
      行 → 跳过（immutable 红线下改稿不覆盖既有条，首次采集为准；连跑
      两次零新增）；墓碑 id 不复活（删除优先，add_many 同语义）；
    - 每篇 payload.meta 携带 frontmatter（title/tags/series——设计 §3.1
      「frontmatter 成为素材 meta」）+ article_slug；license=ALL-RIGHTS
      （自产）；provenance 恒空（内部源素材是原始采集物，指回原文走
      origin.uri=slug，与创作工坊 slug 互链——设计 §3.1）；
    - include_drafts=False 时只收 publish_log 里的 publish 行文章
      （merge_logs 按 scan_root 优先双日志合并读）。

    返回 ``{source_id, added, skipped, total}``；scan_root 非目录 →
    ValueError（studio 400 可读）。
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("缺少必填字段 source_id（内部源 id）")
    root = Path(scan_root).expanduser() if isinstance(scan_root, str) else Path(scan_root)
    if not root.is_dir():
        raise ValueError(f"内部源 scan_root 不是目录: {root}")
    wanted: list[Path] = sorted(root.glob("*.md"))
    if not include_drafts:
        from .publish_log_service import merge_logs  # 函数体内导入（同层解耦）

        published = {
            str(Path(str(row.get("article") or "")).stem)
            for row in merge_logs(root)
            if row.get("action") == "publish" or row.get("result") == "published"
        }
        wanted = [p for p in wanted if p.stem in published]
    sid = source_id.strip()
    index, deleted = _read_index()
    from ..core.discovery import scanner  # 函数体内导入（导入期解耦）

    added = skipped = 0
    for path in wanted:
        uri = path.stem  # slug（articles_root 平铺约定，drafted_uris 同口径）
        mid = model.make_material_id(sid, "text", uri)
        row = index.get(mid)
        if row is not None and not model.is_tombstone(row):
            skipped += 1  # 已采集（改稿不覆盖既有条——immutable 红线）
            continue
        if mid in deleted:
            skipped += 1  # 墓碑：删除优先，不复活
            continue
        try:
            meta = scanner.scan_file(path)
        except OSError:
            skipped += 1
            continue
        fm, body = _article_text(path)
        payload_meta: dict[str, Any] = {"article_slug": uri}
        for key in ("title", "series"):
            value = str(fm.get(key) or "").strip()
            if value:
                payload_meta[key] = value
        tags = meta.get("tags") or []
        if tags:
            payload_meta["tags"] = list(tags)
        material = model.make_material(
            mtype="text",
            source_id=sid,
            uri=uri,
            title=str(meta.get("title") or uri),
            license="ALL-RIGHTS",
            payload={"text": body, "meta": payload_meta},
            fetched_at=fetched_at or _now_iso(),
        )
        errors = model.validate_material(material)
        if errors:
            raise ValueError(f"内部源素材行不合法（{uri}）: " + "; ".join(errors))
        _append(material)
        added += 1
    return {"source_id": sid, "added": added, "skipped": skipped, "total": len(wanted)}


# ── 第二十一期 F60：素材文件服务（三道门——服务层统一 ValueError=400 /
#    FileNotFoundError=404，表现层只做状态码映射）──────────────────────


_FILE_SUFFIX_WHITELIST = ("png", "jpg", "jpeg", "gif", "webp")  # svg 恒拒（assets.py 判例同源）


def read_material_file(material_id: str, variant: str = "thumb") -> tuple[bytes, str]:
    """按 id 服务素材文件字节（F60：thumb/full 两态）——服务端钉死三道门：

    1. 路径边界：payload.local_path 必须解析落在 ~/.musespiral/materials/files/
       内（../、绝对路径、symlink 逃逸全拒——_resolve_inside 同款纪律）；
    2. 后缀白名单：png/jpg/jpeg/gif/webp（svg 恒拒——同源 SVG 执行脚本
       可绕 confirm 直发动作，assets.py:22 判例原文）；
    3. 魔数复核：读文件头复核真实格式与后缀一致（防入库后改扩展名）。

    返回 (bytes, content_type)；Content-Type 按魔数真值。404 面（素材/
    文件不存在或已删）抛 FileNotFoundError，400 面抛 ValueError。"""
    if variant not in ("thumb", "full"):
        raise ValueError(f"variant 必须是 thumb/full 之一: {variant!r}")
    from ..infra import media_store  # 函数体内导入（导入期解耦）

    material = get_material(material_id)
    if material is None:
        raise FileNotFoundError(f"素材不存在或已删: {material_id!r}")
    if material.get("type") != "image":
        raise ValueError(f"素材无文件面（仅 image 类型可服务文件）: type={material.get('type')!r}")
    files = media_store.files_dir().resolve()
    if variant == "thumb":
        target = files / f"{material_id}.thumb.webp"
    else:
        rel = str((material.get("payload") or {}).get("local_path") or "")
        if not rel:
            raise ValueError("素材缺 payload.local_path（无文件可服务）")
        if "\x00" in rel or Path(rel).is_absolute():
            raise ValueError(f"local_path 非法（须为素材仓内相对路径）: {rel!r}")
        try:
            target = (config.home_dir() / rel).resolve()
        except (OSError, ValueError):
            raise ValueError(f"local_path 非法: {rel!r}") from None
        if not target.is_relative_to(files):
            raise ValueError("local_path 越界（必须落在素材文件仓 materials/files/ 内）")
        suffix = target.suffix.lower().lstrip(".")
        if suffix not in _FILE_SUFFIX_WHITELIST:
            raise ValueError(
                f"后缀不在白名单（{'/'.join(_FILE_SUFFIX_WHITELIST)}；svg 恒拒）: .{suffix}"
            )
    if not target.is_file():
        raise FileNotFoundError(f"素材文件缺失: {target.name}")
    try:
        body = target.read_bytes()
    except OSError:
        raise FileNotFoundError(f"素材文件读取失败: {target.name}") from None
    sniffed = media_store.sniff_format(body[:64])
    if not sniffed:
        raise ValueError(f"文件魔数无法识别（非图片字节）: {target.name}")
    if variant == "thumb":
        if sniffed != "webp":
            raise ValueError(f"缩略图魔数不符（应为 WebP，判定为 {sniffed}）: {target.name}")
        return body, "image/webp"
    suffix = target.suffix.lower().lstrip(".")
    expected = {"jpg", "jpeg"} if sniffed == "jpeg" else {sniffed}
    if suffix not in expected:
        raise ValueError(f"文件魔数与后缀不符（魔数判定为 {sniffed}，后缀 .{suffix}）: {target.name}")
    return body, media_store.ALLOWED_FORMATS[sniffed]


# ── KB 收藏星标（档A discover/star 动作面；M4 起读写 materials）─────


def set_kb_starred(
    path_rel: str, starred: bool, *, source_id: str = "kb"
) -> dict[str, Any]:
    """收藏/取消收藏一条 KB 引用（引用不复制：payload.local_path 指回）。

    与 migrate_p19_stars 同语义（原 studio/actions._action_discover_star 的
    业务逻辑下沉，行为零变更）：素材在册 → update_triage；不在册且收藏 →
    落一条 kb 引用素材（triage.starred=True）；取消收藏对不在册素材零动作。
    KB 根界检查归表现层（越界/绝对路径 → 400 先于本函数）。

    返回 {"starred": bool, "ts": str}——收藏时 ts=回写行 fetched_at，
    取消收藏 ts=""（与原动作响应逐键一致）。
    """
    mid = model.make_material_id(source_id, "text", path_rel)
    existing = get_material(mid)
    if starred:
        if existing is None:
            add_material(
                model.make_material(
                    mtype="text",
                    source_id=source_id,
                    uri=path_rel,
                    title="",
                    payload={"local_path": path_rel},
                    triage={"read": False, "starred": True, "enabled": True, "tags": []},
                    fetched_at=_now_iso(),
                )
            )
        else:
            update_triage(mid, {"starred": True})
        row = get_material(mid)
        ts = str((row or {}).get("fetched_at") or "")
    else:
        if existing is not None:
            update_triage(mid, {"starred": False})
        ts = ""
    return {"starred": starred, "ts": ts}


# ── 迁移件三：drafted 推导面（discovered.json 读侧兼容）─────────────


def drafted_uris(articles_root: str | Path) -> set[str]:
    """正稿 frontmatter.source 指回集合（骨架已生成判定面）。

    扫文章根 *.md（不递归——骨架平铺在 root，discover_service.DEFAULT_
    ARTICLES_ROOT 同约定），解析 frontmatter 块内 `source:` 行原值；
    坏文件/无 frontmatter 跳过。discovered.json 的 drafted 标记自此由
    本推导面替代（正典 §1.2 迁移表：查询视图内推导，不存结果集）。
    """
    root = Path(articles_root)
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for path in sorted(root.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        value = _frontmatter_source(text)
        if value:
            out.add(value)
    return out


def _frontmatter_source(text: str) -> str | None:
    """取 frontmatter 块内 source 行原值（宽容：无块/无键 → None）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return None  # 块结束未遇 source
        if stripped.startswith("source:"):
            return stripped[len("source:"):].strip()
    return None


def is_drafted(material: dict[str, Any], drafted: set[str], kb_root: str | Path | None = None) -> bool:
    """素材的骨架已生成判定：origin.uri 命中正稿指回集合。

    双形态命中（discover_service._source_label 两种落点）：KB 相对路径
    直接命中；kb_root 给定时绝对形态 str(kb_root/uri) 也命中——同一篇
    文档无论骨架以何种路径形态溯源都判定为 drafted。
    """
    uri = str((material.get("origin") or {}).get("uri") or "")
    if not uri:
        return False
    if uri in drafted:
        return True
    if kb_root is not None:
        try:
            return str((Path(kb_root) / uri).resolve()) in drafted
        except (OSError, ValueError):
            return False
    return False


# ── 迁移件二：P19 星标 → triage.starred（幂等）─────────────────────


def migrate_p19_stars(kb_source_id: str = "kb") -> dict[str, Any]:
    """discover_stars.json 迁移到 triage.starred（迁移三件之二，M4 执行）。

    幂等以「每颗星标的语义已体现在素材库」为完成标志——重复执行零追加
    （任务约束：迁移幂等=重复执行零重复行）：

      - uri 已有活跃素材且 starred=True → 跳过（零追加）；
      - uri 已有活跃素材未星标 → update_triage 追加 starred 行（二跑
        命中上一条分支，不再追加）；
      - uri 无素材 → 落一条 kb 引用素材（type=text、origin.source_id=
        kb 源 id、payload.local_path 指回——引用不复制红线），fetched_at
        取星标时间戳（收藏时序可追溯）；二跑时 add_material 幂等收敛。

    旧文件 discover_stars.json 只读不删（端点改读写 materials 归 M2
    收编切换；档A actions 微端点在切换前继续可用）。
    """
    stars = _load_p19_stars()
    migrated = skipped = 0
    for relpath, ts in sorted(stars.items()):
        mid = model.make_material_id(kb_source_id, "text", relpath)
        existing = get_material(mid)
        if existing is not None:
            if (existing.get("triage") or {}).get("starred") is True:
                skipped += 1
                continue
            update_triage(mid, {"starred": True})
            migrated += 1
            continue
        material = model.make_material(
            mtype="text",
            source_id=kb_source_id,
            uri=relpath,
            title="",
            payload={"local_path": relpath},
            triage={"read": False, "starred": True, "enabled": True, "tags": []},
            material_id=mid,
            fetched_at=ts or _now_iso(),
        )
        add_material(material)
        migrated += 1
    return {"migrated": migrated, "skipped": skipped, "total": len(stars)}


def _load_p19_stars() -> dict[str, str]:
    """宽容读 P19 星标旧存储（{KB相对路径: ISO ts}；缺/坏 → 空表）。"""
    path = config.home_dir() / STARS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k).strip(): str(v)
        for k, v in data.items()
        if isinstance(k, str) and k.strip()
    }
