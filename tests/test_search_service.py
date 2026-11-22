"""ARCH-5 语义搜索编排测试（services/search_service + 增量钩子 + 端点）。

正典=docs/项目方案/musespiral-极限架构与功能扩展总规划-2026-11-21.md
§ARCH-5 第 3+4+5 件。测试纪律（同 tests/test_embedding.py）：**不真调
Ollama**——embedding 全走 monkeypatch（embed_available/embed 两个模块属性，
search_service 经模块属性调用故可替）；MUSESPIRAL_HOME 指 tmp 隔离
（素材库 + 向量索引库同隔离）；钩子以 MUSESPIRAL_INDEX_HOOK=sync 同步跑
（断言无竞态；conftest 已把测试进程默认设 off——本文件逐用例开启）。

假 embedding 语义化：向量=词表命中计数（「向量/语义」同义簇共占一维），
让「关键词不命中、语义命中」的可测场景成立（真模型同义能力的确定替身）。
端点面沿 test_materials_feedback 惯例：真实回环服务器 + http.client。
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.infra import embedding
from musespiral.services import materials_service as ms
from musespiral.services import search_service as svc

# 假 embedding 词表（末两维给「视频」「标签」路可辨性；「向量/语义」同义簇）
VOCAB = ("嵌入", "检索", "视频", "标签")


def fake_vec(text: str) -> list[float]:
    """确定性假向量：dim0=同义簇（向量+语义）计数，其余按词表计数。"""
    syn = text.count("向量") + text.count("语义")
    vec = [float(syn)] + [float(text.count(w)) for w in VOCAB]
    return [x + 0.01 for x in vec]  # +0.01 防零向量（余弦在零向量上恒 0）


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """全隔离：素材库 + 向量索引库 + embed 缓存都落 tmp；钩子同步跑。"""
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setenv("MUSESPIRAL_INDEX_HOOK", "sync")  # conftest 默认 off → 本文件开同步
    monkeypatch.delenv("MUSESPIRAL_EMBED_MODEL", raising=False)
    svc.reset_ready_cache()
    yield home
    svc.reset_ready_cache()


@pytest.fixture
def embed_on(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """假 embedding 就位态：embed_available=True、embed=词表向量。

    返回调用记录（每元素=一次调用的文本批），供 batch/幂等断言。"""
    calls: list[list[str]] = []

    def fake_embed(texts: list[str]) -> list[list[float]]:
        calls.append(list(texts))
        return [fake_vec(t) for t in texts]

    monkeypatch.setattr(embedding, "embed_available", lambda timeout=5: True)
    monkeypatch.setattr(embedding, "embed", fake_embed)
    return calls


@pytest.fixture
def embed_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """embedding 缺位态（Ollama 未运行）：探测 False；embed 若被误调用则
    抛真 EmbeddingError（可读中文安装引导——降级面文案的断言锚）。"""
    monkeypatch.setattr(embedding, "embed_available", lambda timeout=5: False)

    def refused(texts: list[str]) -> list[list[float]]:
        raise embedding.EmbeddingError(embedding.INSTALL_HINT)

    monkeypatch.setattr(embedding, "embed", refused)


def _material(
    uri: str, text: str, *, tags: list[str] | None = None, fetched_at: str = "2026-11-22T10:00:00"
) -> dict:
    triage = None
    if tags is not None:
        triage = {
            "read": False,
            "starred": False,
            "enabled": True,
            "tags": tags,
            "verified": False,
            "confidence": None,
        }
    return model.make_material(
        mtype="text",
        source_id="kb",
        uri=uri,
        title=uri,
        payload={"text": text},
        triage=triage,
        fetched_at=fetched_at,
    )


# ── ① 增量索引钩子（add/upload/feedback 漏斗 → 向量落库）─────────────


def test_add_hook_indexes_material(_isolated_home: Path, embed_on) -> None:
    """add_material（sync 钩子）→ 向量行在库（ARCH-5 第 4 件主路径）。"""
    row = ms.add_material(_material("a.md", "向量检索的工程实践"))
    assert svc.indexed_ids() == {row["id"]}
    # 行内容：当前模型 + 待嵌文本哈希（幂等判据在位）
    con = svc._connect()
    try:
        mid, mdl, th = con.execute(
            "SELECT material_id, model, text_hash FROM material_vectors"
        ).fetchone()
    finally:
        con.close()
    assert mid == row["id"] and mdl == "bge-m3"
    assert th == svc._text_hash(svc._material_text(row))


def test_add_many_hook_single_batch_call(_isolated_home: Path, embed_on) -> None:
    """add_many 三条 → 未命中文本合并为**一次** embed 批调用（batch 承诺）。"""
    ms.add_many(
        [
            _material("a.md", "向量检索"),
            _material("b.md", "嵌入维度"),
            _material("c.md", "视频转写"),
        ]
    )
    assert len(embed_on) == 1
    assert len(embed_on[0]) == 3
    assert len(svc.indexed_ids()) == 3


def test_delete_hook_clears_index(_isolated_home: Path, embed_on) -> None:
    """删除（墓碑）→ 索引行同步清掉（本地 SQLite，不依赖 embedding）。"""
    row = ms.add_material(_material("a.md", "向量检索"))
    assert svc.indexed_ids() == {row["id"]}
    ms.delete_material(row["id"])
    assert svc.indexed_ids() == set()


def test_hook_off_then_manual_incremental_index(
    _isolated_home: Path, embed_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钩子关闭面（conftest 缺省态）零索引；index_material 手动单条补建。"""
    monkeypatch.setenv("MUSESPIRAL_INDEX_HOOK", "off")
    row = ms.add_material(_material("a.md", "向量检索"))
    assert svc.indexed_ids() == set()  # off：写路径不触发
    assert svc.index_material(row["id"]) is True  # 手动增量
    assert svc.indexed_ids() == {row["id"]}


def test_index_idempotent_no_reembed(_isolated_home: Path, embed_on) -> None:
    """同内容重复索引零重嵌（text_hash 幂等）；等值重 add 亦不重嵌。"""
    row = ms.add_material(_material("a.md", "向量检索"))
    assert len(embed_on) == 1
    assert svc.index_material(row["id"]) is True  # 已新鲜 → True 但零 embed
    ms.add_material(_material("a.md", "向量检索"))  # 幂等重入（dedup 路径）
    assert len(embed_on) == 1


def test_register_video_hook(_isolated_home: Path, embed_on) -> None:
    """upload/register 漏斗（register_video → add_material）同样被钩住。"""
    result = ms.register_video("https://www.bilibili.com/video/BV1xx411c7mD", title="向量检索分享")
    assert svc.indexed_ids() == {result["material"]["id"]}


# ── ② 降级承诺（embedding 缺位 → 永远给一个能用的搜索面）────────────


def test_hybrid_degrades_to_keyword_tag_when_off(_isolated_home: Path, embed_off) -> None:
    """hybrid 在 embedding 缺位时不抛错：关键词+标签两路照常出结果。"""
    ms.add_material(_material("a.md", "向量检索的工程实践", tags=["检索理论"]))
    ms.add_material(_material("b.md", "视频转写完全指南", tags=["视频生产"]))
    hits = svc.hybrid_search("向量 检索")
    assert [h["uri"] for h in hits] == ["a.md"]
    assert hits[0]["matched_lanes"] == ["keyword", "tag"]  # 语义路缺席不冒名
    assert hits[0]["similarity"] is None


def test_semantic_raises_readable_when_off(_isolated_home: Path, embed_off) -> None:
    """显式 semantic 模式不静默换路：透传可读 EmbeddingError（安装引导）。"""
    ms.add_material(_material("a.md", "向量检索"))
    with pytest.raises(embedding.EmbeddingError, match="ollama pull bge-m3"):
        svc.semantic_search("向量")


def test_index_material_off_returns_false(_isolated_home: Path, embed_off) -> None:
    """缺位态 index_material 安静 False（钩子路径降级）；写路径不受影响。"""
    row = ms.add_material(_material("a.md", "向量检索"))  # 钩子内部 False 但不抛
    assert svc.indexed_ids() == set()
    assert svc.index_material(row["id"]) is False
    assert ms.get_material(row["id"]) is not None  # 素材本体完好


def test_rebuild_off_raises_readable(_isolated_home: Path, embed_off) -> None:
    """显式 rebuild 不做静默空重建：缺位以可读 EmbeddingError 冒出
    （有素材待嵌时——空库 rebuild 无事可做是诚实成功，不在此例）。"""
    ms.add_material(_material("a.md", "向量检索"))  # embed_off 下钩子未建索引
    with pytest.raises(embedding.EmbeddingError, match="ollama pull bge-m3"):
        svc.rebuild_index()


def test_semantic_ready_cache_flip(_isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """可用性 TTL 缓存：flip 探测值须 reset 才可见（跨态不粘缓存）。"""
    monkeypatch.setattr(embedding, "embed_available", lambda timeout=5: False)
    assert svc.semantic_ready() is False
    monkeypatch.setattr(embedding, "embed_available", lambda timeout=5: True)
    assert svc.semantic_ready() is False  # TTL 内陈值
    svc.reset_ready_cache()
    assert svc.semantic_ready() is True


# ── ③ 搜索面（keyword / semantic / hybrid 三入口）──────────────────


def test_keyword_search_order_and_shape(_isolated_home: Path, embed_on) -> None:
    """keyword 模式：词面谓词命中、fetched_at 降序、条目为裁剪面。"""
    ms.add_material(_material("old.md", "向量检索", fetched_at="2026-11-01T00:00:00"))
    ms.add_material(_material("new.md", "向量检索入门", fetched_at="2026-11-22T00:00:00"))
    ms.add_material(_material("miss.md", "视频转写"))  # 不命中
    hits = svc.keyword_search("向量")
    assert [h["uri"] for h in hits] == ["new.md", "old.md"]
    assert hits[0]["matched_lanes"] == ["keyword"]
    assert hits[0]["score"] is None and hits[0]["similarity"] is None
    assert "payload" not in hits[0]  # 裁剪面：正文不回传


def test_semantic_search_ranking(_isolated_home: Path, embed_on) -> None:
    """semantic 模式：query→top-k 相似度降序；同义簇替身下「语义」命中「向量」。"""
    ms.add_material(_material("vec.md", "向量索引与余弦相似度的暴力扫描实现"))
    ms.add_material(_material("video.md", "视频转写与裁段"))
    ms.add_material(_material("both.md", "向量与视频的跨模态检索"))
    hits = svc.semantic_search("语义检索")  # 关键词不命中 vec.md（「语义」≠「向量」）
    uris = [h["uri"] for h in hits]
    assert uris[0] in ("vec.md", "both.md")  # 同义簇：语义≈向量
    assert "video.md" == uris[-1]  # 无关素材沉底
    assert hits[0]["similarity"] > hits[-1]["similarity"]
    assert hits[0]["matched_lanes"] == ["semantic"]


def test_hybrid_rrf_multi_lane_beats_single(_isolated_home: Path, embed_on) -> None:
    """RRF：三路全中的素材融合分高于仅词面命中的素材；无关素材不入语义路
    （SEMANTIC_MIN_SIM 阈值——matched_lanes 的 semantic 必须是真命中）。"""
    ms.add_material(_material("all.md", "向量检索的工程实践", tags=["向量检索"]))
    ms.add_material(_material("kw.md", "向量检索的另一篇"))
    ms.add_material(_material("none.md", "视频转写"))
    hits = svc.hybrid_search("向量检索")
    assert hits[0]["uri"] == "all.md"
    assert set(hits[0]["matched_lanes"]) == {"keyword", "tag", "semantic"}
    assert hits[0]["similarity"] is not None  # 语义路原始余弦在位
    kwhit = next(h for h in hits if h["uri"] == "kw.md")
    assert kwhit["matched_lanes"] == ["keyword", "semantic"]
    assert hits[0]["score"] > kwhit["score"]
    assert all(h["uri"] != "none.md" for h in hits)  # 词面不中且相似度低于阈值 → 不出结果


def test_hybrid_semantic_only_discovery(_isolated_home: Path, embed_on) -> None:
    """语义路可发现词面不命中的素材（FEAT-1 的核心价值面）。"""
    ms.add_material(_material("vec.md", "向量索引原理"))  # 词面不含「语义」
    hits = svc.hybrid_search("语义")  # 同义簇替身：语义≈向量
    assert [h["uri"] for h in hits] == ["vec.md"]
    assert hits[0]["matched_lanes"] == ["semantic"]


def test_empty_query_rejected(_isolated_home: Path, embed_on) -> None:
    for fn in (svc.keyword_search, svc.semantic_search, svc.hybrid_search):
        with pytest.raises(ValueError, match="不能为空"):
            fn("   ")


def test_limit_clamped(_isolated_home: Path, embed_on) -> None:
    for i in range(3):
        ms.add_material(_material(f"m{i}.md", f"向量检索第{i}篇"))
    assert len(svc.keyword_search("向量", limit=999)) == 3  # 超库返回全库
    assert len(svc.keyword_search("向量", limit=2)) == 2


# ── ④ 全量重建（幂等 / 清残留 / 换模型隔离）────────────────────────


def test_rebuild_counts_and_gc(_isolated_home: Path, embed_on) -> None:
    """rebuild：计数面 + 非活跃残留行清理 + 二跑零重嵌（幂等）。"""
    ms.add_material(_material("a.md", "向量检索"))
    ms.add_material(_material("b.md", "嵌入维度"))
    # 清空索引表模拟「从未建索引/换模型后旧行废弃」，另塞一条非活跃 id 的
    # 残留行（钩子漏清场景的 rebuild 安全网）
    con = svc._connect()
    try:
        con.execute("DELETE FROM material_vectors")
        con.execute(
            "INSERT OR REPLACE INTO material_vectors"
            "(material_id, model, dim, text_hash, vec, indexed_at)"
            " VALUES ('ghost', 'bge-m3', 5, X'00', X'0000803F', '2026-01-01')"
        )
        con.commit()
    finally:
        con.close()
    result = svc.rebuild_index()
    assert result["total"] == 2 and result["indexed"] == 2 and result["removed_stale"] == 1
    assert "ghost" not in svc.indexed_ids()
    embed_on.clear()
    again = svc.rebuild_index()
    assert again["indexed"] == 0 and again["skipped_unchanged"] == 2
    assert embed_on == []  # 全新鲜 → 零 embed


def test_model_switch_isolates_old_vectors(
    _isolated_home: Path, embed_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换模型：旧行读侧不可见（不撞假相似度），rebuild 覆写为当前模型。"""
    ms.add_material(_material("a.md", "向量检索"))
    monkeypatch.setenv("MUSESPIRAL_EMBED_MODEL", "bge-m3-v2")
    # 旧行（bge-m3）在新模型下不可见
    assert svc._load_vectors() == {}
    svc.rebuild_index()
    con = svc._connect()
    try:
        models = {r[0] for r in con.execute("SELECT model FROM material_vectors")}
    finally:
        con.close()
    assert models == {"bge-m3-v2"}  # 同 id 覆写（一素材一行，当前模型胜出）


# ── ⑤ 端点面（真实回环服务器：GET search / POST reindex）────────────


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path):
    httpd = studio.make_server(studio_root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def _request(server, method: str, path: str, body: dict | None = None):
    from urllib.parse import quote

    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    # query 串里的中文/非 ASCII 必须 percent-encode（http.client 要求 ASCII 请求行）
    if "?" in path:
        base, _, qs = path.partition("?")
        path = base + "?" + quote(qs, safe="=&")
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data)


def test_endpoint_search_modes_and_validation(server, _isolated_home, embed_on) -> None:
    """GET /api/materials/search：三 mode 全通 + 参数校验 400 面。"""
    ms.add_material(_material("a.md", "向量检索的工程实践", tags=["检索理论"]))
    # keyword
    status, data = _request(server, "GET", "/api/materials/search?q=向量&mode=keyword")
    assert status == 200 and data["ok"] is True and data["mode"] == "keyword"
    assert [i["uri"] for i in data["items"]] == ["a.md"]
    # semantic
    status, data = _request(server, "GET", "/api/materials/search?q=检索&mode=semantic")
    assert status == 200 and data["ok"] is True and data["semantic_ready"] is True
    assert data["items"][0]["similarity"] > 0
    # hybrid 缺省 mode
    status, data = _request(server, "GET", "/api/materials/search?q=向量")
    assert status == 200 and data["mode"] == "hybrid"
    # 校验面
    status, data = _request(server, "GET", "/api/materials/search")
    assert status == 400  # 缺 q
    status, data = _request(server, "GET", "/api/materials/search?q=x&mode=bogus")
    assert status == 400  # mode 枚举
    status, data = _request(server, "GET", "/api/materials/search?q=x&limit=abc")
    assert status == 400  # limit 非整数


def test_endpoint_semantic_degraded_ok_false(server, _isolated_home, embed_off) -> None:
    """端点降级面：semantic 缺位 → 200 ok:false 带安装引导（环境态非 5xx）。"""
    ms.add_material(_material("a.md", "向量检索"))
    status, data = _request(server, "GET", "/api/materials/search?q=向量&mode=semantic")
    assert status == 200
    assert data["ok"] is False and data["semantic_ready"] is False
    assert "ollama pull bge-m3" in data["error"]
    assert data["items"] == []
    # hybrid 同况不降级成错误：照常出关键词结果
    status, data = _request(server, "GET", "/api/materials/search?q=向量&mode=hybrid")
    assert status == 200 and data["ok"] is True
    assert [i["uri"] for i in data["items"]] == ["a.md"]


def test_endpoint_reindex_confirm_and_run(server, _isolated_home, embed_on) -> None:
    """POST /api/materials/reindex：confirm 闸门 + 全量重建计数面。"""
    ms.add_material(_material("a.md", "向量检索"))
    ms.add_material(_material("b.md", "嵌入维度"))
    # 增量钩子已建索引 → 第一趟 rebuild 幂等零重嵌
    status, data = _request(server, "POST", "/api/materials/reindex", {"confirm": True})
    assert status == 200 and data["ok"] is True
    assert data["total"] == 2 and data["indexed"] == 0 and data["skipped_unchanged"] == 2
    # 清空索引表（模拟换模型/首次启用）→ 第二趟真重建
    con = svc._connect()
    try:
        con.execute("DELETE FROM material_vectors")
        con.commit()
    finally:
        con.close()
    status, data = _request(server, "POST", "/api/materials/reindex", {"confirm": True})
    assert status == 200 and data["ok"] is True and data["indexed"] == 2
    assert len(svc.indexed_ids()) == 2
    status, data = _request(server, "POST", "/api/materials/reindex", {})
    assert status == 400  # 缺 confirm（人审闸门）


def test_endpoint_full_chain_spark_to_semantic(
    server, studio_root, _isolated_home, embed_on
) -> None:
    """全链：POST spark（写路径钩子）→ 向量在库 → GET semantic 搜得回。"""
    status, data = _request(
        server,
        "POST",
        "/api/materials/spark",
        {
            "confirm": True,
            "text": "灵感：语义搜索的关键在于向量召回的排序质量",
        },
    )
    assert status == 200 and data["ok"] is True
    mid = data["material"]["id"]
    assert svc.indexed_ids() == {mid}  # 增量钩子经真实 HTTP 写路径生效
    status, data = _request(server, "GET", "/api/materials/search?q=向量召回&mode=semantic")
    assert status == 200
    assert [i["id"] for i in data["items"]] == [mid]
    assert data["items"][0]["similarity"] > 0.5  # 同义簇替身下高相似


# ── FEAT-1 ②③：相关素材 related()（详情侧栏 + refine prompt 的数据源）────────


def test_related_ranking_and_excludes_self(_isolated_home: Path, embed_on) -> None:
    """related：source 素材的相似邻居按余弦降序，排除自身，带相似度。"""
    src = ms.add_material(_material("src.md", "向量索引与检索"))
    near = ms.add_material(_material("near.md", "向量检索的索引实现"))
    far = ms.add_material(_material("far.md", "视频转写裁段"))
    hits = svc.related(src["id"], limit=5)
    ids = [h["id"] for h in hits]
    assert src["id"] not in ids  # 排除自身
    assert near["id"] in ids and far["id"] not in ids  # 近邻入、远邻不入（score>0 门）
    assert all(h["similarity"] is not None and h["matched_lanes"] == ["semantic"] for h in hits)
    assert hits[0]["similarity"] >= hits[-1]["similarity"]  # 降序
    # limit 生效 + exclude_id 额外排除（refine 排除派生链成员）
    assert len(svc.related(src["id"], limit=1)) == 1
    assert near["id"] not in [h["id"] for h in svc.related(src["id"], exclude_id=near["id"])]


def test_related_degrades_empty_when_off_or_no_index(_isolated_home: Path, embed_off) -> None:
    """降级：embedding 缺位 / 索引不存在 → related 返回 [] 且不建库文件、不抛错。"""
    src = ms.add_material(_material("src.md", "向量检索"))
    # 索引钩子因 embed_off 未建索引；索引库不存在 → 空相关且不创建文件
    assert not svc.index_store_path().exists()
    assert svc.related(src["id"]) == []
    assert not svc.index_store_path().exists()  # 纯读零落盘（refine 零痕迹红线）
    # 即便索引库在（手动造表），embedding 缺位也降级为空
    svc._connect().close()
    assert svc.related(src["id"]) == []


def test_endpoint_detail_includes_related(_isolated_home: Path, embed_on, server) -> None:
    """GET /api/materials/detail 带 related 字段（详情侧栏数据源），缺失 key 即回归。"""
    src = ms.add_material(_material("src.md", "向量索引与检索"))
    ms.add_material(_material("near.md", "向量检索的索引实现"))
    status, data = _request(server, "GET", "/api/materials/detail?id=" + src["id"])
    assert status == 200 and data["ok"] is True
    assert "related" in data and isinstance(data["related"], list)
    assert len(data["related"]) >= 1
