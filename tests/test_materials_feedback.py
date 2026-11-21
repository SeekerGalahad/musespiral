"""N-2 素材闭环后端契约测试（2026-11-21，正典=《musespiral-素材闭环与专业编辑器
设计-2026-11-18.md》§三/§五 P-1 +《总体方案》N-2）。

沿 test_studio_materials.py 惯例：真实回环服务器 + http.client，tmp_path 与
MUSESPIRAL_HOME 全隔离（零外网、不触 ~/.musespiral）。四条验收硬要求逐条落测：

  - spark 创建后按 sub_type 筛出，且**评分/集合/文件服务路径不受影响**
    （读侧零回归断言——类型枚举不动裁决的守护面）；
  - 反哺三路径（full/excerpt/distill）各自产素材且 provenance 正确
    （published_from/distill 链贯穿断言）；
  - 内部源刷新幂等（连跑两次不产重复素材）；
  - 创作工坊引用自己产的素材能进 prompt（素材 id 直引 + 文章 slug 两形态）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.infra.sources import registry
from musespiral.services import compose_service
from musespiral.services import materials_service as ms

ARTICLE = """---
title: 素材闭环三日谈
tags: [闭环, 反哺]
series: 闭环系列
---

## 缘起

飞轮缺回程是结构性问题。

## 三条路径

整篇、摘取与提炼各司其职。
"""


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(_isolate_home: Path) -> Path:
    """本地无 key 档案（material_distill 未配路由 → 走 default）。"""
    (_isolate_home / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return _isolate_home


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    return root


@pytest.fixture
def server(studio_root: Path) -> studio.ThreadingHTTPServer:
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
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers: dict[str, str] = {}
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _seed_article(studio_root: Path, slug: str = "loop3") -> None:
    (studio_root / f"{slug}.md").write_text(ARTICLE, encoding="utf-8")


def _post(server, path: str, body: dict):
    return _request(server, "POST", path, body)


def _materials(server, query: str = "") -> dict:
    status, data = _request(server, "GET", f"/api/materials{query}")
    assert status == 200, data
    return data


# ── ① 灵感速记（spark）───────────────────────────────────────────────


def test_spark_quick_capture_and_subtype_filter(server, studio_root, _isolate_home) -> None:
    """spark 创建 → sub_type 筛出；type 恒 text（枚举不动裁决）。"""
    _seed_article(studio_root)  # 占位：文章根非空（不参与本测断言）
    status, data = _post(server, "/api/materials/spark", {"confirm": True, "text": "灵感：给素材库补一条回程。\n第二行"})
    assert status == 200 and data["ok"] is True and data["dedup"] is False
    spark = data["material"]
    assert spark["type"] == "text"  # 裁决：不新增 MATERIAL_TYPES 取值
    assert spark["payload"]["meta"]["sub_type"] == "spark"
    assert spark["triage"]["read"] is False
    assert spark["origin"]["source_id"] == ms.SPARK_SOURCE_ID == "manual"
    assert spark["provenance"]["chain"] == []
    assert "灵感：给素材库补一条回程。" in spark["payload"]["text"]

    # sub_type 筛选（chip 读 meta.sub_type 非 type）
    hits = _materials(server, "?sub_type=spark")
    assert hits["total"] == 1 and hits["items"][0]["id"] == spark["id"]
    plain = _materials(server, "?sub_type=nosuch")
    assert plain["total"] == 0
    # 存量无 sub_type 键的素材在 sub_type 过滤下天然不命中（不崩读侧）
    all_items = _materials(server)
    assert all_items["total"] == 1  # 只有 spark 一条

    # 幂等：同一句话重发 dedup=true 零追加
    status, data2 = _post(server, "/api/materials/spark", {"confirm": True, "text": "灵感：给素材库补一条回程。\n第二行"})
    assert status == 200 and data2["dedup"] is True and data2["material"]["id"] == spark["id"]
    assert _materials(server)["total"] == 1


def test_spark_read_side_regression(server, studio_root, _isolate_home) -> None:
    """评分/集合/文件服务三条读侧路径不受 spark 影响（回归断言）。"""
    # kb 评分素材 + 一条 spark
    kb_dir = _isolate_home / "kb" / "13-source-analysis"
    kb_dir.mkdir(parents=True)
    (kb_dir / "doc.md").write_text(
        "---\ntitle: 评分文档\ncategory: 13-source-analysis\ncreated: 2020-01-01\n"
        "tags: [源码分析, Python, 测试, o/r]\n---\n\n## 概述\n\n正文足够长。 " * 20,
        encoding="utf-8",
    )
    from musespiral.services import sources_service as src_svc

    src_svc.register_source({
        "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
        "config": {"root": str(_isolate_home / "kb"), "sources": []},
    })
    ms.add_material(model.make_material(
        mtype="text", source_id="kb", uri="13-source-analysis/doc.md", title="评分文档",
        payload={"local_path": "13-source-analysis/doc.md"}, fetched_at="2026-01-01T00:00:00",
    ))
    status, data = _post(server, "/api/materials/spark", {"confirm": True, "text": "一条普通灵感"})
    spark_id = data["material"]["id"]

    # 评分现算：kb text 素材带分数，spark 无分沉底（None）——spark 不炸评分面
    items = {m["id"]: m for m in _materials(server)["items"]}
    kb_row = next(m for m in items.values() if m["origin"]["source_id"] == "kb")
    assert isinstance(kb_row["score"], int) and kb_row["score"] > 0
    assert items[spark_id]["score"] is None  # spark=普通 text 无分（正典 §1.4 口径零变更）

    # 集合：spark 可入集合（F62 语义不受 sub_type 影响）
    status, data = _post(server, "/api/materials/collections", {"confirm": True, "action": "create", "name": "回程", "color": "jade"})
    assert status == 200, data
    cid = data["collection"]["id"]
    status, data = _post(server, "/api/materials/collections", {"confirm": True, "action": "add", "id": cid, "member_ids": [spark_id]})
    assert status == 200, data
    hits = _materials(server, f"?collection={cid}&sub_type=spark")
    assert hits["total"] == 1 and hits["items"][0]["id"] == spark_id

    # 文件服务：spark（text 类型）恒「无文件面」400——三道门行为零变更
    status, data = _request(server, "GET", f"/api/materials/file?id={spark_id}&variant=full")
    assert status == 400 and "无文件面" in str(data.get("error", ""))


def test_spark_confirm_gate_and_validation(server) -> None:
    """confirm 闸门与 text 必填（与其他动作接口同语义）。"""
    status, data = _post(server, "/api/materials/spark", {"text": "没带 confirm"})
    assert status == 400 and "confirm" in str(data.get("error", ""))
    status, data = _post(server, "/api/materials/spark", {"confirm": True, "text": "   "})
    assert status == 400 and "text" in str(data.get("error", ""))


# ── ② 文章反哺三路径（from-article）────────────────────────────────


def test_from_article_full_and_excerpt_provenance(server, studio_root, _isolate_home) -> None:
    """full/excerpt 各产一条素材，published_from 链贯穿（from_id=slug）。"""
    _seed_article(studio_root)
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "full"})
    assert status == 200 and data["ok"] is True and data["mode"] == "full" and data["dedup"] is False
    full = data["material"]
    assert full["type"] == "text" and full["license"] == "ALL-RIGHTS"
    assert full["provenance"]["chain"] == [{"from_id": "loop3", "op": "published_from"}]
    assert full["payload"]["meta"]["article_slug"] == "loop3"
    assert "飞轮缺回程是结构性问题。" in full["payload"]["text"]
    assert "---" not in full["payload"]["text"]  # frontmatter 已剥离（正文不带 YAML 噪声）

    # 整篇反哺幂等：同 slug 重跑 dedup=true
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "full"})
    assert status == 200 and data["dedup"] is True and data["material"]["id"] == full["id"]

    # excerpt：正文行区间（对剥除 frontmatter 后的行计）+ 同链
    status, data = _post(server, "/api/materials/from-article", {
        "confirm": True, "slug": "loop3", "mode": "excerpt", "start_line": 4, "end_line": 4,
    })
    assert status == 200 and data["ok"] is True
    exc = data["material"]
    assert exc["provenance"]["chain"] == [{"from_id": "loop3", "op": "published_from"}]
    assert exc["payload"]["text"] == "飞轮缺回程是结构性问题。"
    assert exc["payload"]["meta"]["line_start"] == 4 and exc["payload"]["meta"]["line_end"] == 4

    # 校验面：mode 枚举外 / 文章缺失 / 区间非法 → 400
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "nope"})
    assert status == 400 and "mode" in str(data.get("error", ""))
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "ghost", "mode": "full"})
    assert status == 400 and "文章不存在" in str(data.get("error", ""))
    status, data = _post(server, "/api/materials/from-article", {
        "confirm": True, "slug": "loop3", "mode": "excerpt", "start_line": 99, "end_line": 1,
    })
    assert status == 400 and "行区间非法" in str(data.get("error", ""))


def test_from_article_distill_sparks(server, studio_root, _isolate_home, ai_on, monkeypatch) -> None:
    """distill：AI 抽论点 → 多条 spark，每条 distill 链贯穿（from_id=slug）。"""
    _seed_article(studio_root)

    def fake_chat(messages, *, task=None, **kwargs):
        assert task == "material_distill"
        assert "飞轮缺回程" in messages[0]["content"]  # 正文进了模板
        return {"text": "1. 第一条论点\n- 第二条论点\n第三条论点\n\n5. 第五条论点", "cache_hit": False}

    monkeypatch.setattr(ms, "_chat_policed", fake_chat)
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "distill"})
    assert status == 200 and data["ok"] is True and data["mode"] == "distill"
    assert len(data["materials"]) == 4 and data["skipped"] == 0
    sparks = data["materials"]
    for i, spark in enumerate(sparks, 1):
        assert spark["type"] == "text" and spark["payload"]["meta"]["sub_type"] == "spark"
        assert spark["provenance"]["chain"] == [{"from_id": "loop3", "op": "distill"}]
        assert spark["license"] == "AI-GENERATED" and spark["payload"]["meta"]["ai_generated"] is True
        assert spark["payload"]["meta"]["distill_slot"] == i
    assert [s["payload"]["text"] for s in sparks] == [
        "第一条论点", "第二条论点", "第三条论点", "第五条论点",
    ]
    # sub_type 筛选面能看到全部提炼卡
    assert _materials(server, "?sub_type=spark")["total"] == 4

    # 重跑：槽位已有 → skipped=4 零新增（immutable 红线：不覆盖既有条）
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "distill"})
    assert status == 200 and data["materials"] == [] and data["skipped"] == 4


def test_from_article_distill_ai_error_degrades(server, studio_root, _isolate_home, ai_on, monkeypatch) -> None:
    """AI 未配置/调用失败 → 200 {ok:false} 降级面（compose 动作同款，不 500）。"""
    _seed_article(studio_root)
    from musespiral.infra.ai import AIError

    def boom(messages, **kwargs):
        raise AIError("调用 AI 失败（模拟）")

    monkeypatch.setattr(ms, "_chat_policed", boom)
    status, data = _post(server, "/api/materials/from-article", {"confirm": True, "slug": "loop3", "mode": "distill"})
    assert status == 200 and data["ok"] is False and "AI" in str(data.get("error", ""))


# ── ④ 内部源（internal）─────────────────────────────────────────────


def _register_internal(server, studio_root: Path, sid: str = "internal-articles") -> None:
    status, data = _post(server, "/api/sources", {
        "confirm": True, "id": sid, "type": "internal", "label": "我的文章库",
        "config": {"scan_root": str(studio_root), "include_drafts": True},
    })
    assert status == 200, data


def test_internal_source_register_refresh_idempotent(server, studio_root, _isolate_home) -> None:
    """内部源注册 → 刷新产素材 → **连跑两次零重复**（按 slug 去重）。"""
    _seed_article(studio_root, "post1")
    _seed_article(studio_root, "post2")
    _register_internal(server, studio_root)

    status, data = _post(server, "/api/sources/internal/refresh", {"confirm": True, "id": "internal-articles"})
    assert status == 200 and data["ok"] is True
    assert data["added"] == 2 and data["skipped"] == 0 and data["total"] == 2

    rows = _materials(server, "?source=internal-articles")
    assert rows["total"] == 2
    by_slug = {m["origin"]["uri"]: m for m in rows["items"]}
    assert set(by_slug) == {"post1", "post2"}
    for slug, m in by_slug.items():
        assert m["type"] == "text" and m["license"] == "ALL-RIGHTS"
        assert m["payload"]["meta"]["article_slug"] == slug
        assert m["payload"]["meta"]["title"] == "素材闭环三日谈"  # frontmatter title 入 meta（设计 §3.1）
        assert m["payload"]["meta"]["tags"] == ["闭环", "反哺"]
        assert m["payload"]["meta"]["series"] == "闭环系列"
        assert m["provenance"]["chain"] == []  # 原始采集物：血统恒空

    # **幂等硬要求**：连跑两次不产重复素材
    status, data = _post(server, "/api/sources/internal/refresh", {"confirm": True, "id": "internal-articles"})
    assert status == 200 and data["added"] == 0 and data["skipped"] == 2
    assert _materials(server, "?source=internal-articles")["total"] == 2

    # 健康探活：scan_root 存在 → ok（internal 键映射）
    status, data = _post(server, "/api/sources/verify", {"confirm": True, "id": "internal-articles"})
    assert status == 200 and data["health"]["status"] == "ok"

    # 收尾回写：last_refresh 已记（F68 刷新收尾惯例）
    from musespiral.services import sources_service as src_svc

    assert src_svc.get_source("internal-articles")["health"]["last_refresh"]


def test_internal_source_include_drafts_false(server, studio_root, _isolate_home) -> None:
    """include_drafts=False：只收 publish_log 里的 publish 行文章。"""
    _seed_article(studio_root, "published1")
    _seed_article(studio_root, "draft1")
    from musespiral.services.publish_log_service import append_manual, root_log_path

    append_manual("published1", "wechat", "已发的一篇", log_path=root_log_path(studio_root))
    _register_internal(server, studio_root)
    status, data = _post(server, "/api/sources/internal/refresh", {
        "confirm": True, "id": "internal-articles", "include_drafts": False,
    })
    assert status == 200 and data["added"] == 1
    slugs = {m["origin"]["uri"] for m in _materials(server)["items"]}
    assert slugs == {"published1"}


def test_internal_source_gates(server, studio_root, _isolate_home) -> None:
    """注册面：internal 缺 scan_root → 400；刷新非 internal 源 → 400。"""
    status, data = _post(server, "/api/sources", {
        "confirm": True, "id": "bad-internal", "type": "internal", "label": "x", "config": {},
    })
    assert status == 400 and "scan_root" in str(data.get("error", ""))

    _register_internal(server, studio_root)
    status, data = _post(server, "/api/sources/internal/refresh", {"confirm": True, "id": "kb"})
    assert status == 400 and "internal" in str(data.get("error", ""))


def test_internal_source_in_registry_enum() -> None:
    """SOURCE_TYPES 含 internal（注册表枚举扩面，base 拒绝同 kb/dir）。"""
    assert "internal" in registry.SOURCE_TYPES
    row = {"id": "internal-articles", "type": "internal", "label": "x", "config": {"scan_root": "/tmp/x"}}
    registry.validate_source(row)  # 合法
    with pytest.raises(ValueError, match="base"):
        registry.validate_source({**row, "config": {"scan_root": "/tmp/x", "base": "http://127.0.0.1:1"}})


# ── ⑤ 创作工坊「引用自己」─────────────────────────────────────────


def test_compose_cite_own_material_and_article_into_prompt(server, studio_root, _isolate_home) -> None:
    """素材 id 直引与文章 slug 两形态入池 → 两者的正文都进 prompt。"""
    _seed_article(studio_root)
    status, data = _post(server, "/api/materials/spark", {"confirm": True, "text": "回程灵感：素材要能流回创作。"})
    mid = data["material"]["id"]

    status, data = _post(server, "/api/compose/new", {"confirm": True, "slug": "selfcite", "title": "自引一篇"})
    assert status == 200 and data["ok"] is True, data

    # 素材直引
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "selfcite", "material_id": mid})
    assert status == 200 and data["ok"] is True, data
    assert data["path"] == f"material://{mid}" and data["material_id"] == mid

    # 文章 slug 引
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "selfcite", "article_slug": "loop3"})
    assert status == 200 and data["ok"] is True, data
    assert data["path"].endswith("loop3.md")

    session = compose_service._load_session("selfcite", studio_root)[0]
    assert [s.material_id for s in session.sources] == [mid, ""]
    assert session.sources[0].path == f"material://{mid}"
    prompt = compose_service.build_prompt(session)
    assert "回程灵感：素材要能流回创作。" in prompt  # 素材直引进 prompt（硬要求）
    assert "飞轮缺回程是结构性问题。" in prompt  # 文章 slug 引进 prompt
    assert "素材闭环三日谈" in prompt  # 文章 title 作素材标签

    # session.json 持久化形状：material_id 仅素材直引条落键（存量形状不变）
    meta = json.loads((studio_root / "selfcite.compose" / "session.json").read_text(encoding="utf-8"))
    assert meta["sources"][0]["material_id"] == mid
    assert "material_id" not in meta["sources"][1]

    # 撤池：material:// 定位符原样匹配
    status, data = _post(server, "/api/compose/remove_source", {"confirm": True, "slug": "selfcite", "path": f"material://{mid}"})
    assert status == 200 and data["ok"] is True, data
    session = compose_service._load_session("selfcite", studio_root)[0]
    assert len(session.sources) == 1 and session.sources[0].path.endswith("loop3.md")


def test_compose_cite_gates(server, studio_root, _isolate_home) -> None:
    """三选一来源校验：不给/多给 → 400；素材 id 不存在 → ok:false；文章缺 → ok:false。"""
    _post(server, "/api/compose/new", {"confirm": True, "slug": "gate1", "title": "t"})
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "gate1"})
    assert status == 400 and "三选一" in str(data.get("error", ""))
    status, data = _post(server, "/api/compose/add", {
        "confirm": True, "slug": "gate1", "material_id": "abc", "article_slug": "loop3",
    })
    assert status == 400 and "三选一" in str(data.get("error", ""))
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "gate1", "material_id": "deadbeef1234"})
    assert status == 200 and data["ok"] is False and "素材不存在或已删" in str(data.get("error", ""))
    _seed_article(studio_root, "loop3")
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "gate1", "article_slug": "../escape"})
    assert status == 200 and data["ok"] is False  # slug 白名单拒（防路径穿越）
    status, data = _post(server, "/api/compose/add", {"confirm": True, "slug": "gate1", "article_slug": "ghost"})
    assert status == 200 and data["ok"] is False and "文章不存在" in str(data.get("error", ""))


# ── 模型面（枚举/校验）───────────────────────────────────────────────


def test_published_from_and_distill_validate() -> None:
    """published_from/distill 入 PROVENANCE_OPS；原始 text 单链豁免生效，
    多链/双 op 组合照旧拒绝（豁免条五不放宽其余形态）。"""
    assert "published_from" in model.PROVENANCE_OPS and "distill" in model.PROVENANCE_OPS
    assert model.META_SUB_TYPES == ("character", "video_article", "spark")
    assert "spark" not in model.MATERIAL_TYPES  # 裁决：类型枚举不动

    base = {
        "id": "abc123", "type": "text", "license": "ALL-RIGHTS",
        "origin": {"source_id": "article", "uri": "article://loop3", "title": "t"},
        "fetched_at": "2026-11-21T00:00:00", "payload": {"text": "x", "meta": {}},
        "triage": {"read": False, "starred": False, "enabled": True, "tags": []},
    }
    ok = {**base, "provenance": {"chain": [{"from_id": "loop3", "op": "published_from"}], "derived_from": []}}
    assert model.validate_material(ok) == []
    ok2 = {**base, "provenance": {"chain": [{"from_id": "loop3", "op": "distill"}], "derived_from": []}}
    assert model.validate_material(ok2) == []
    # 多链拒绝
    bad = {**ok, "provenance": {"chain": [
        {"from_id": "loop3", "op": "published_from"}, {"from_id": "x", "op": "distill"},
    ], "derived_from": []}}
    assert any("provenance 必须为空" in e for e in model.validate_material(bad))
    # 空 provenance 的原始 text 照旧合法（普通 text 零回归）
    plain = {**base, "provenance": {"chain": [], "derived_from": []}}
    assert model.validate_material(plain) == []


def test_query_sub_type_filter_unit(_isolate_home) -> None:
    """query(sub_type=…) 正交过滤：命中只看 meta.sub_type（服务级直测）。"""
    ms.add_spark("火花一", fetched_at="2026-01-01T00:00:00")
    ms.add_material(model.make_material(
        mtype="text", source_id="kb", uri="a.md", title="普通",
        fetched_at="2026-01-02T00:00:00",
    ))
    sparks = ms.query(sub_type="spark")
    assert len(sparks) == 1 and ms.is_spark(sparks[0])
    assert ms.query(sub_type="character") == []
    # 正交：mtype + sub_type 同时给（AND 语义）
    assert len(ms.query(mtype="text", sub_type="spark")) == 1
