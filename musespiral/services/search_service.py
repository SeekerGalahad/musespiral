"""素材语义搜索编排（ARCH-5 第 3+4 件，正典=docs/项目方案/musespiral-极限架构
与功能扩展总规划-2026-11-21.md §ARCH-5）——FEAT-1 自然语言搜索的后端数据面。

三层分工（与前两件互不越界）：
  - 纯数学（余弦/top-k/归一化）= core/materials/vector.py（零 I/O）；
  - 外部推理（Ollama REST + sha256 缓存）= infra/embedding.py（不 import 本层）；
  - 本模块 = 编排：向量索引落盘（SQLite BLOB）+ 三路合并搜索 + 增量钩子。

降级承诺（与 markitdown 集成同理念——embedding 不可用时永远给用户一个能用的
搜索面）：
  - hybrid_search 三路合并中语义路缺位（Ollama 未运行/未拉模型/索引空）→
    静默收编关键词+标签两路继续出结果，绝不抛错；
  - semantic_search 显式要语义时对 EmbeddingError 不吞——透传
    infra/embedding 的可读中文错误（含安装引导），由 HTTP 面转 ok:false
    降级载荷（from-article 的 AI 环境态先例：环境态不是请求错误）；
  - 增量钩子（on_material_*）对一切异常零抛出——索引失败绝不影响素材
    主写路径（immutable 素材库红线优先于可再建的派生索引）。

索引存储：~/.musespiral/material_vectors.sqlite（MUSESPIRAL_HOME 覆写）——
表 material_vectors(material_id PK, model, dim, text_hash, vec BLOB,
indexed_at)。vec 为 float32 数组字节（同 infra/embedding 缓存口径，1024 维
≈4KB/条）；model 列随 MUSESPIRAL_EMBED_MODEL 隔离——换模型后旧行读侧天然
过滤、rebuild 覆写，不撞假相似度；text_hash=sha256(可搜文本) 使索引幂等
（同内容重复 add 零重嵌）。素材 ≤10000 暴力扫描 <100ms（正典 §ARCH-5 技术选型），
不引入向量数据库。

搜索面三入口：keyword_search（词面谓词现算）/ semantic_search（query→embed→
top-k）/ hybrid_search（关键词+标签+语义 RRF 名次融合——k=60 标准参数，与
radar-hub 全景索引同族策略）。条目裁剪面：id/type/title/uri/source_id/
fetched_at/tags + score/similarity/matched_lanes（搜索结果不回传整 payload
正文——详情走 /api/materials/detail）。
"""

from __future__ import annotations

import array
import hashlib
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import config
from ..core.materials import search as mat_search
from ..core.materials import vector
from ..infra import embedding
from ..jobs.store import now_iso as _now_iso

# 索引文件名（config.home_dir() 下，随 MUSESPIRAL_HOME——测试隔离同款惯例）
INDEX_FILE = "material_vectors.sqlite"
# RRF 名次融合常数（标准 k=60：名次贡献 1/(60+rank)，稳态于前 ~几十名）
RRF_K = 60.0
# hybrid 的语义路取宽候选（融合前多取，名次贡献按相似度序）
SEMANTIC_LANE_K = 50
# 语义路入路阈值：余弦低于此值不入 lane（防「全库都是语义命中」——RRF 名次
# 已把低相似排后，阈值只裁标签语义与语义路独占发现的明显误报；bge-m3 中文
# 相似度谱系压缩，0.35 取保守低界，宁漏勿滥）
SEMANTIC_MIN_SIM = 0.35
# 单次 embed 批大小（bge-m3 本地推理的稳态批；rebuild 分批喂）
INDEX_BATCH = 32
# 索引文本截断（bge-m3 上下文 8192 token；长正文截首段保主要语义，防请求膨胀）
INDEX_TEXT_MAX = 4000
# 可用性探测缓存 TTL（秒）——钩子路径防探测风暴；Ollama 启停窗口内的陈值
# 由 TTL 过期或显式 rebuild 兜底
_AVAIL_TTL = 300.0

_LANES = ("keyword", "tag", "semantic")

# 可用性探测进程级缓存（TTL；测试 monkeypatch embedding.embed_available 后
# 须调 reset_ready_cache() 防跨用例泄漏）
_avail_cache: bool | None = None
_avail_cache_at: float = 0.0
_avail_lock = threading.Lock()


# ── 索引存储（SQLite + float32 BLOB）────────────────────────────────


def index_store_path() -> Path:
    """向量索引库路径：~/.musespiral/material_vectors.sqlite（随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / INDEX_FILE


def _connect() -> sqlite3.Connection:
    path = index_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.execute(
        "CREATE TABLE IF NOT EXISTS material_vectors ("
        "material_id TEXT PRIMARY KEY, "
        "model TEXT NOT NULL, "
        "dim INTEGER NOT NULL, "
        "text_hash TEXT NOT NULL, "
        "vec BLOB NOT NULL, "
        "indexed_at TEXT NOT NULL)"
    )
    return con


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pack(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _up_to_date(con: sqlite3.Connection, material_id: str, text: str) -> bool:
    """索引行是否新鲜（同模型 + 同文本哈希）——幂等索引的判据。"""
    row = con.execute(
        "SELECT model, text_hash FROM material_vectors WHERE material_id = ?",
        (material_id,),
    ).fetchone()
    return row is not None and row[0] == embedding._model() and row[1] == _text_hash(text)


def _upsert_rows(rows: list[tuple[str, int, str, bytes]]) -> None:
    """落一批索引行 (material_id, dim, text_hash, vec)——同 id 覆写（新模型胜出）。"""
    if not rows:
        return
    model = embedding._model()
    stamped = _now_iso()
    con = _connect()
    try:
        con.executemany(
            "INSERT OR REPLACE INTO material_vectors"
            "(material_id, model, dim, text_hash, vec, indexed_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(mid, model, dim, th, blob, stamped) for mid, dim, th, blob in rows],
        )
        con.commit()
    finally:
        con.close()


def _load_vectors() -> dict[str, list[float]]:
    """全量载当前模型的索引向量（坏行宽容跳过；换模型旧行天然不可见）。"""
    con = _connect()
    out: dict[str, list[float]] = {}
    try:
        cur = con.execute(
            "SELECT material_id, dim, vec FROM material_vectors WHERE model = ?",
            (embedding._model(),),
        )
        for mid, dim, blob in cur.fetchall():
            arr = array.array("f")
            try:
                arr.frombytes(bytes(blob))
            except (ValueError, OverflowError):
                continue  # 坏行宽容（与 infra/embedding._unpack 同语义）
            if len(arr) != dim:
                continue
            out[str(mid)] = arr.tolist()
    finally:
        con.close()
    return out


def remove_indexed(material_id: str) -> bool:
    """清一条索引（素材删除钩子）。返回是否有行被删。"""
    con = _connect()
    try:
        cur = con.execute("DELETE FROM material_vectors WHERE material_id = ?", (material_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def indexed_ids() -> set[str]:
    """已建索引的素材 id 集（诊断/测试面）。"""
    con = _connect()
    try:
        return {str(r[0]) for r in con.execute("SELECT material_id FROM material_vectors")}
    finally:
        con.close()


# ── 可用性探测（TTL 缓存）───────────────────────────────────────────


def semantic_ready() -> bool:
    """embedding 是否就绪（embedding.embed_available + 进程级 TTL 缓存）。

    缓存动机：增量钩子高频调用（ingest 批量 add 每批一次探测），探测本身
    是 HTTP 往返——TTL 内复用结果，未就绪时钩子零网络成本静默跳过。
    """
    global _avail_cache, _avail_cache_at
    with _avail_lock:
        now = time.monotonic()
        if _avail_cache is not None and now - _avail_cache_at < _AVAIL_TTL:
            return _avail_cache
        ok = embedding.embed_available()
        _avail_cache, _avail_cache_at = ok, now
        return ok


def reset_ready_cache() -> None:
    """清可用性缓存（测试 monkeypatch 探测后必调；线上语义路失败时也会自清）。"""
    global _avail_cache, _avail_cache_at
    with _avail_lock:
        _avail_cache, _avail_cache_at = None, 0.0


# ── 索引写面（单条增量 / 批量 / 全量重建）───────────────────────────


def _material_text(material: dict[str, Any]) -> str:
    """素材的待嵌文本 = 词面命中域同一拍平面（searchable_text 单一权威），
    截 INDEX_TEXT_MAX 防长正文撑爆请求。"""
    return mat_search.searchable_text(material)[:INDEX_TEXT_MAX]


def _index_texts(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """索引核心（无语义就绪门——调用方自决）：(id, 待嵌文本) 清单 → 新鲜度
    预检（同模型+同哈希零重嵌）→ 未命中按 INDEX_BATCH 分批 embed → 落库。

    空文本不入库（零向量无语义）且清残留行，计入 empty_text。embed 失败
    抛 embedding.EmbeddingError（可读中文）——本函数不吞。
    """
    result: dict[str, Any] = {"indexed": 0, "skipped_unchanged": 0, "missing": 0, "empty_text": 0}
    pending: list[tuple[str, str]] = []
    con = _connect()
    try:
        for mid, text in pairs:
            if not text.strip():
                con.execute("DELETE FROM material_vectors WHERE material_id = ?", (mid,))
                result["empty_text"] += 1
                continue
            if _up_to_date(con, mid, text):
                result["skipped_unchanged"] += 1
                continue
            pending.append((mid, text))
        con.commit()
    finally:
        con.close()
    for start in range(0, len(pending), INDEX_BATCH):
        chunk = pending[start : start + INDEX_BATCH]
        vecs = embedding.embed([text for _, text in chunk])
        _upsert_rows(
            [(mid, len(vec), _text_hash(text), _pack(vec)) for (mid, text), vec in zip(chunk, vecs)]
        )
        result["indexed"] += len(chunk)
    return result


def index_material(material_id: str) -> bool:
    """单条素材建索引（增量钩子主入口）。

    返回 False 的诚实缺位：素材不存在/已删、可搜文本为空（零向量无语义，
    不入库）、embedding 未就绪（降级——不抛错，留给 rebuild 兜底）。
    embed 失败抛 embedding.EmbeddingError（可读中文）——钩子路径由 _guarded
    吞掉，显式调用方拿得到原因。同内容重复索引零重嵌（text_hash 幂等）。
    """
    if not semantic_ready():
        return False
    from . import materials_service  # 函数体内导入（导入期解耦）

    material = materials_service.get_material(material_id)
    if material is None:
        return False
    res = _index_texts([(material_id, _material_text(material))])
    # 新嵌成功或已新鲜（幂等重入）都算「索引在位」；空文本/失败为 False
    return res["indexed"] == 1 or res["skipped_unchanged"] == 1


def index_materials(material_ids: list[str]) -> dict[str, Any]:
    """批量建索引（add_many 钩子/内部源刷新批路径）：未命中文本合并成
    INDEX_BATCH 一批的 embed 调用（infra/embedding 的 batch 承诺）。

    语义门与 index_material 同款：embedding 未就绪 → 零调用早退（结果带
    embedding_ready:false 供调用方诊断）。幂等同 text_hash 判据。
    """
    if not material_ids:
        return {
            "indexed": 0,
            "skipped_unchanged": 0,
            "missing": 0,
            "empty_text": 0,
            "embedding_ready": True,
        }
    if not semantic_ready():
        return {
            "indexed": 0,
            "skipped_unchanged": 0,
            "missing": 0,
            "empty_text": 0,
            "embedding_ready": False,
        }
    from . import materials_service  # 函数体内导入（导入期解耦）

    pairs: list[tuple[str, str]] = []
    missing = 0
    for mid in material_ids:
        material = materials_service.get_material(mid)
        if material is None:
            missing += 1
            continue
        pairs.append((mid, _material_text(material)))
    result = _index_texts(pairs)
    result["missing"] += missing
    result["embedding_ready"] = True
    return result


def rebuild_index() -> dict[str, Any]:
    """全量重建索引（POST /api/materials/reindex 的执行面，显式动作不设
    语义就绪门——embedding 缺位时首批 embed 抛可读 EmbeddingError 而非
    静默空重建的假成功）。

    步骤：活跃素材逐批重嵌（text_hash 幂等——已新鲜的零重嵌）+ 清理非
    活跃 id 的残留行（墓碑/误删兜底，钩子漏清时的安全网）。返回计数面。
    """
    from . import materials_service  # 函数体内导入（导入期解耦）

    rows = materials_service.list_materials()
    active = {m["id"] for m in rows}
    stale = sorted(indexed_ids() - active)
    for mid in stale:
        remove_indexed(mid)
    result = _index_texts(sorted((m["id"], _material_text(m)) for m in rows))
    result["removed_stale"] = len(stale)
    result["total"] = len(active)
    return result


# ── 搜索读面（keyword / semantic / hybrid 三入口）──────────────────


def _clamp_limit(limit: int) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return 10
    return max(1, min(n, 100))


def _item(
    material: dict[str, Any],
    *,
    score: float | None,
    similarity: float | None,
    lanes: list[str],
) -> dict[str, Any]:
    """搜索结果条目（裁剪面：不回传整 payload 正文——详情走 /api/materials/detail）。"""
    origin = material.get("origin") or {}
    triage = material.get("triage") or {}
    raw_tags = triage.get("tags")
    tags: list[Any] = raw_tags if isinstance(raw_tags, list) else []
    return {
        "id": material.get("id"),
        "type": material.get("type"),
        "title": str(origin.get("title") or ""),
        "uri": str(origin.get("uri") or ""),
        "source_id": str(origin.get("source_id") or ""),
        "fetched_at": material.get("fetched_at"),
        "tags": [str(t) for t in tags if isinstance(t, str)],
        "score": score,
        "similarity": similarity,
        "matched_lanes": lanes,
    }


def keyword_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """词面搜索（core/materials/search 谓词现算，零索引零依赖）。

    排序沿 list_materials 展示序（fetched_at 降序）。matched_lanes 恒
    ["keyword"]；score/similarity 为 None（词面命中无数值语义——正典 F64）。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("搜索词不能为空")
    n = _clamp_limit(limit)
    from . import materials_service  # 函数体内导入（导入期解耦）

    hits = [m for m in materials_service.list_materials() if mat_search.matches_query(m, q)]
    return [_item(m, score=None, similarity=None, lanes=["keyword"]) for m in hits[:n]]


def semantic_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """语义搜索：query → embed → 索引向量 top-k（余弦相似度降序）。

    embedding 不可用/失败 → 抛 embedding.EmbeddingError（可读中文 + 安装
    引导，透传 infra 的错误面）——显式要语义时不静默换路。索引空（素材
    未建索引）返回 []（环境就绪但无数据是诚实空态，非错误）。读侧只取
    活跃素材对应的向量行（钩子漏清时的墓碑安全网）。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("搜索词不能为空")
    n = _clamp_limit(limit)
    from . import materials_service  # 函数体内导入（导入期解耦）

    by_id = {m["id"]: m for m in materials_service.list_materials()}
    try:
        qvec = embedding.embed([q])[0]
    except embedding.EmbeddingError:
        reset_ready_cache()  # 失败自清缓存（Ollama 中途宕机的陈阳性值）
        raise
    vectors = {mid: v for mid, v in _load_vectors().items() if mid in by_id}
    ranked = vector.top_k(qvec, vectors, n)
    return [
        _item(by_id[mid], score=round(score, 6), similarity=round(score, 6), lanes=["semantic"])
        for mid, score in ranked
    ]


def hybrid_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """三路合并搜索（关键词 + 标签 + 语义 RRF 名次融合）——降级承诺的落点。

    语义路缺位（未就绪/索引空/推理失败）→ 静默收编其余两路继续出结果，
    绝不抛错；关键词/标签路恒可用（词面谓词与 triage 现算，零外部依赖）。
    融合分 = Σ_lane 1/(RRF_K + 名次)，名次为各路内 1-based 序；条目带
    matched_lanes（参与了哪几路）与 similarity（语义路原始余弦，未入路
    为 None）。排序：融合分降序 → id 升序（确定性终值）。
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("搜索词不能为空")
    n = _clamp_limit(limit)
    from . import materials_service  # 函数体内导入（导入期解耦）

    rows = materials_service.list_materials()
    by_id = {m["id"]: m for m in rows}
    lanes: dict[str, list[str]] = {}
    similarity: dict[str, float] = {}

    # 路 1 关键词（词面谓词现算；rank 沿展示序 fetched_at 降序）
    kw_ids = [m["id"] for m in rows if mat_search.matches_query(m, q)]
    if kw_ids:
        lanes["keyword"] = kw_ids

    # 路 2 标签（查询词元 × triage.tags 子串命中；rank 按命中数降序）
    terms = [t for kind, t in mat_search.parse_query(q) if kind == "word"]
    if terms:
        tag_hits: list[tuple[int, str]] = []
        for m in rows:
            tags = (m.get("triage") or {}).get("tags")
            if not isinstance(tags, list):
                continue
            hits = sum(
                1 for t in tags if isinstance(t, str) and any(term in t.lower() for term in terms)
            )
            if hits:
                tag_hits.append((-hits, m["id"]))
        if tag_hits:
            tag_hits.sort()
            lanes["tag"] = [mid for _, mid in tag_hits]

    # 路 3 语义（降级点：未就绪/失败/空索引都只缺席这一路；低于入路阈值的
    # 候选不入 lane——matched_lanes 的「semantic」必须是真命中不是排序尾巴）
    try:
        if semantic_ready():
            qvec = embedding.embed([q])[0]
            vectors = {mid: v for mid, v in _load_vectors().items() if mid in by_id}
            if vectors:
                top = [
                    (mid, score)
                    for mid, score in vector.top_k(qvec, vectors, SEMANTIC_LANE_K)
                    if score >= SEMANTIC_MIN_SIM
                ]
                if top:
                    lanes["semantic"] = [mid for mid, _ in top]
                    similarity = {mid: score for mid, score in top}
    except embedding.EmbeddingError:
        reset_ready_cache()  # 陈阳性缓存自清；本路缺席，其余两路照常

    fused: dict[str, float] = {}
    for lane in _LANES:
        for rank, mid in enumerate(lanes.get(lane, []), 1):
            fused[mid] = fused.get(mid, 0.0) + 1.0 / (RRF_K + rank)
    lane_sets = {lane: set(ids) for lane, ids in lanes.items()}
    ordered = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    out: list[dict[str, Any]] = []
    for mid, score in ordered:
        hit_lanes = [lane for lane in _LANES if mid in lane_sets.get(lane, set())]
        sim = similarity.get(mid)
        out.append(
            _item(
                by_id[mid],
                score=round(score, 6),
                similarity=round(sim, 6) if sim is not None else None,
                lanes=hit_lanes,
            )
        )
    return out


# ── 相关素材（FEAT-1 ②/③：详情侧栏 top-5 + AI 加工 top-3 摘要）─────


def related(material_id: str, limit: int = 5, *, exclude_id: str | None = None) -> list[dict[str, Any]]:
    """与给定素材语义最相近的其它活跃素材（余弦相似度降序，排除自身）。

    FEAT-1 的两个消费面共用本函数：素材详情「语义相关」top-5（带相似度
    百分比）、refine 提炼时拼进 prompt 的 top-3 摘要。

    降级承诺（同 hybrid 语义路）：
      - 源素材未建索引 / embedding 未就绪 / 推理失败 / 索引为空 → 一律 []
        （不抛错——相关推荐是增强面，绝不影响详情加载与 AI 加工主路径）；
      - exclude_id 额外排除一条（refine 时避免把派生版本反喂回源素材链——
        调用方可传 chain 成员 id；缺省排除 source 自身）。
    """
    # 索引库不存在=零向量=零相关——早退，且**绝不创建空库文件**（refine preview
    # 的「零落盘零痕迹」红线：相关推荐是纯读增强面，不得因一次读在磁盘留文件）。
    if not index_store_path().exists():
        return []
    n = _clamp_limit(limit)
    from . import materials_service  # 函数体内导入（导入期解耦）

    try:
        if not semantic_ready():
            return []
        vectors = _load_vectors()
        if material_id not in vectors:
            return []  # 源素材未建索引（空文本/索引尚未重建）——诚实空态
        qvec = vectors[material_id]
        by_id = {m["id"]: m for m in materials_service.list_materials()}
        skip = {material_id}
        if exclude_id:
            skip.add(exclude_id)
        candidates = {mid: v for mid, v in vectors.items() if mid in by_id and mid not in skip}
        if not candidates:
            return []
        ranked = vector.top_k(qvec, candidates, n)
        return [
            _item(by_id[mid], score=round(score, 6), similarity=round(score, 6), lanes=["semantic"])
            for mid, score in ranked
            if score >= SEMANTIC_MIN_SIM  # 与 hybrid 语义路同阈值——低相似不是「相关」（宁缺勿滥）
        ]
    except embedding.EmbeddingError:
        reset_ready_cache()
        return []


# ── 增量索引钩子（crud/feedback 写面调用——异步不阻塞主流程）────────


def _hook_mode() -> str:
    """钩子执行模式：async（默认，守护线程）/ sync（同步直跑，测试与 CLI
    批处理）/ off（全关，测试隔离）。MUSESPIRAL_INDEX_HOOK 覆写。"""
    return os.environ.get("MUSESPIRAL_INDEX_HOOK", "async").strip().lower()


def _guarded(fn: Callable[[], Any]) -> None:
    """钩子执行体：全吞异常——索引是可再建派生面，失败绝不影响素材主写路径。"""
    try:
        fn()
    except Exception:
        pass


def _run_hook(fn: Callable[[], Any]) -> None:
    mode = _hook_mode()
    if mode == "off":
        return
    if mode == "sync":
        _guarded(fn)
        return
    threading.Thread(target=_guarded, args=(fn,), daemon=True, name="musespiral-index").start()


def on_material_added(material_id: str) -> None:
    """素材落库后钩子（crud.add_material）：异步单条建索引。"""
    _run_hook(lambda: index_material(material_id))


def on_materials_added(material_ids: list[str]) -> None:
    """批量落库后钩子（crud.add_many / feedback 内部源刷新）：异步一批建索引
    （未命中合并为批量 embed——一次调 Ollama）。"""
    if not material_ids:
        return
    _run_hook(lambda: index_materials(list(material_ids)))


def on_material_deleted(material_id: str) -> None:
    """素材删除后钩子（crud.delete_material）：清索引行（本地 SQLite，无
    embedding 依赖——Ollama 缺位也照清）。"""
    _run_hook(lambda: remove_indexed(material_id))
