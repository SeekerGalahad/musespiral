"""materials 包：crud（索引存储原语 + 素材 CRUD + 人物卡）。

原 materials_service.py（1492 行单文件）按 ARCH-2（正典=《musespiral-极限
架构与功能扩展总规划-2026-11-21》§ARCH-2，沿 R-1 compose 拆分模式）拆出的
CRUD 簇：JSONL 追加/读序合并原语（materials_path/_read_rows/_read_index/
_append）、读面（list/deleted_ids/get/get_detail）、写面（add_material/
add_many/delete_material/cascade_preview——幂等去重与 immutable 红线在此）
与人物卡族（E-5：add/get/list/is_character——CRUD 复用素材面的 type=text
载体，正典 §3.6）。

分诊（triage）/查询评分（search）/上传（upload）/灵感（spark）/反哺
（feedback）/迭代链（iterations）见各自子模块；包门面与全量符号面见
``musespiral.services.materials`` 的 ``__init__``，旧路径
``services/materials_service.py`` 保留为兼容层。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ... import config
from ...core.materials import model
from ...infra.storage.jsonl import read_rows as _jsonl_rows
from ...jobs.store import now_iso as _now_iso

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


# ── ARCH-5 增量索引钩子（正典 §ARCH-5 第 4 件）──────────────────────
#
# 写面三个漏斗点（add_material/add_many/delete_material）落库成功后通知
# search_service 建向量索引。钩子体在 search_service 内全吞异常且默认异步
# （守护线程）——索引是可再建派生面，其失败绝不阻塞/回滚素材主写路径。
# 函数体内导入避免导入期环（search_service 读面又调本包 list/get）。


def _notify_indexed(material_id: str) -> None:
    """单条落库后 → 异步建索引（ARCH-5）。"""
    from .. import search_service  # 函数体内导入（导入期解耦）

    search_service.on_material_added(material_id)


def _notify_many_indexed(material_ids: list[str]) -> None:
    """批量落库后 → 异步一批建索引（未命中合并单次 embed）。"""
    from .. import search_service  # 函数体内导入（导入期解耦）

    search_service.on_materials_added(material_ids)


def _drop_indexed(material_id: str) -> None:
    """删除（墓碑落盘）后 → 清索引行（本地 SQLite，无 embedding 依赖）。"""
    from .. import search_service  # 函数体内导入（导入期解耦）

    search_service.on_material_deleted(material_id)


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
    from .. import collections_service  # 函数体内导入（层内解耦）

    return {
        **material,
        "references": model.ref_statuses(material, active_ids),
        "collections": collections_service.collections_of(material_id),
    }


# ── 写面（落库/删除）──────────────────────────────────────────────


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
        _notify_indexed(material["id"])  # ARCH-5：幂等重入也收敛索引（未建则建）
        return existing
    _append(material)
    _notify_indexed(material["id"])
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
                raise ValueError(f"素材 id 已存在且内容不同（immutable 红线，批量零写入）: {rid}")
            skipped += 1
            continue
        pending.append(row)
    for row in pending:
        _append(row)
    if pending:
        _notify_many_indexed([row["id"] for row in pending])  # ARCH-5：批量一次 embed
    return {"added": len(pending), "skipped": skipped}


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
    _drop_indexed(material_id)  # ARCH-5：删除清索引（本地 SQLite，Ollama 缺位照清）
    return {
        "deleted": material_id,
        "cascade": [
            {
                "id": m["id"],
                "type": m.get("type"),
                "title": (m.get("origin") or {}).get("title", ""),
            }
            for m in cascade
        ],
    }


def cascade_preview(material_id: str) -> list[dict[str, Any]]:
    """删除连坐预览（M3 两步 confirm 的声明数据面；不落墓碑、纯读）。

    与 delete_material 返回的连坐清单同源同形（model.resolve_cascade 对
    当前活跃集）；素材不存在/已删 → ValueError（调用方 400/404 呈现）。
    """
    index, deleted = _read_index()
    if material_id in deleted or material_id not in index or model.is_tombstone(index[material_id]):
        raise ValueError(f"素材不存在或已删: {material_id!r}")
    active, _ = model.split_active(index)
    return [
        {"id": m["id"], "type": m.get("type"), "title": (m.get("origin") or {}).get("title", "")}
        for m in model.resolve_cascade(material_id, active)
    ]


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
    relations: list[dict[str, str]] | None = None,
    license: str = "UNKNOWN",
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """建一张人物卡（E-5 §3.6）→ ``{material, dedup}``。

    校验（core.composition.novel.validate_character 同源）：name 必填非空、
    role/appearance/speech_style 为 str、key_events 为 str 列表、relations
    （FEAT-4）为 ``[{to, relation}]`` 列表；不合法 → ValueError（studio 400
    可读）。同名片重复提交：整行相等 → dedup=true 返回既有行（幂等，零追加）；
    内容不同 → ValueError（人物卡不改写——要改就删了重建，保持「卡是快照」语义）。
    """
    from ...core.composition import (
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
        # relations 同纪律：原值进校验（None=缺省空表，不与显式 [] 混淆）
        "relations": relations if relations is not None else [],
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
        if _same_character_row(existing, material):
            return {"material": existing, "dedup": True}
        raise ValueError(
            f"人物卡已存在且内容不同（人物卡是快照，不改写——先删除再重建）: {card['name']}"
        )
    row = add_material(material)
    return {"material": row, "dedup": False}


def _same_character_row(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """人物卡行相等判定（幂等面）：人物卡 meta 经 character_from_meta 归一后比对。

    FEAT-4 起卡 meta 新增 relations 键——存量卡没有该键，若逐字节比对会把
    「重复提交旧卡」误判成「内容不同」。归一化把缺省补齐（relations→[]）后
    再比，幂等语义跨批次稳定；其余行键（origin/license/triage…）原样相等。
    """
    from ...core.composition import character_from_meta

    def _norm(row: dict[str, Any]) -> dict[str, Any]:
        payload = row.get("payload")
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if not (isinstance(meta, dict) and meta.get("sub_type") == "character"):
            return row
        out = dict(row)
        out["payload"] = {**payload, "meta": {"sub_type": "character", **character_from_meta(meta)}}
        return out

    return _norm(a) == _norm(b)


def _character_body(card: dict[str, Any]) -> str:
    """人物卡 Markdown 速览（payload.text；不参与 prompt——prompt 走六键渲染）。"""
    lines = [f"# {card['name']}" + (f"（{card['role']}）" if card.get("role") else "")]
    if card.get("appearance"):
        lines.append(f"- 外貌：{card['appearance']}")
    if card.get("speech_style"):
        lines.append(f"- 说话风格：{card['speech_style']}")
    for event in card.get("key_events") or []:
        lines.append(f"- {event}")
    for rel in card.get("relations") or []:
        to = str(rel.get("to") or "").strip()
        if to:
            lines.append(f"- 与 {to}：{rel.get('relation') or '关系未注明'}")
    return "\n".join(lines)


def is_character(material: dict[str, Any]) -> bool:
    """该素材是否人物卡（meta.sub_type=character）。"""
    meta = (material.get("payload") or {}).get("meta") or {}
    return isinstance(meta, dict) and meta.get("sub_type") == "character"


def get_character(material_id: str) -> dict[str, Any] | None:
    """按素材 id 取人物卡视图（非人物卡/不存在 → None）。"""
    from ...core.composition import character_from_meta

    material = get_material(material_id)
    if material is None or not is_character(material):
        return None
    card = character_from_meta((material.get("payload") or {}).get("meta"))
    card["id"] = material["id"]
    return card


def list_characters() -> list[dict[str, Any]]:
    """全部活跃人物卡（挂卡选择器的数据面；fetched_at 降序同素材序）。"""
    from ...core.composition import character_from_meta

    out: list[dict[str, Any]] = []
    for material in _sorted_active(list_materials()):
        if not is_character(material):
            continue
        card = character_from_meta((material.get("payload") or {}).get("meta"))
        card["id"] = material["id"]
        out.append(card)
    return out
