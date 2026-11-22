"""materials 包：search（查询谓词 + 发现页评分现算 + drafted 推导）。

原 materials_service.py 按 ARCH-2 拆出的查询簇：query（全参数正交 AND
——分诊 + 类型/子类型/源/标签/集合 + 词面搜索谓词，F62/F63/F64/N-2/N-4
扩展都在）、query_scored（F56 发现页收编后端：查询 + 评分现算 + drafted
推导 + total 截断可见性契约）、_kb_root_for_scoring/_score_kb_material/
_sort_scored（评分现算三助手）与 drafted_uris/_frontmatter_source/is_drafted
（迁移件三：discovered.json 读侧兼容的骨架已生成推导面）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.discovery import scanner, scorer
from ...core.materials import confidence as confidence_mod  # N-4 低置信阈值单一权威
from ...core.materials import search as mat_search
from ...core.materials import triage  # core.materials.triage（域层分诊谓词）
from .crud import list_materials
from .triage import _confidence_of


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
        from .. import collections_service  # 函数体内导入（层内解耦）

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


# ── 发现页收编后端（F56：查询 + 评分现算，M2）───────────────────────


def _kb_root_for_scoring(kb_root: str | Path | None) -> Path | None:
    """评分用 KB 根解析（sources_service.kb_root 单一权威级联的薄壳）。"""
    from .. import sources_service  # 函数体内导入（层内解耦）

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
    from .. import sources_service  # 函数体内导入（导入期解耦）

    rows = query(
        mtype=mtype, sub_type=sub_type, source_id=source_id, starred=starred,
        read=read, enabled=enabled,
        tag=tag, q=q, collection=collection, confidence=confidence,
    )
    drafted = drafted_uris(articles_root)
    root = _kb_root_for_scoring(kb_root)
    if families is None and root is not None and root.is_dir():
        from .. import discover_service

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
