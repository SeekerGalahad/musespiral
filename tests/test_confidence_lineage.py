"""N-4 置信度 + 血统全链测试（2026-11-21，正典=《mdpub-优化重构与后续开发总体方案-
2026-11-19.md》§二 N-4 +《mdpub-API契约-2026-11-16.md》§3.23）。

四块：
  1. parse_ai_confidence 解析容错（标记行识别/剥行/百分数回退/缺行不崩——
     「AI 不返回该字段时不阻断面」契约）；
  2. triage.verified / triage.confidence 两字段落库读写（triage_patch 五键
     窄门、confidence 不走人工窄门、normalize 宽容归一、老形状行幂等回归）；
  3. 提炼/综合三路径 confidence 落库（refine preview/adopt、agent synthesis
     adopt、distill sparks——AI 只起草红线不变）；
  4. 血统双向（正向 references / 反向 cascade）+ verify 端点与
     GET /api/materials?confidence=low 回环（真实回环服务器）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import confidence as confidence_mod
from musespiral.core.materials import model, triage
from musespiral.services import agent_service
from musespiral.services import materials_service as ms
from musespiral.services import refine_service


# ── fixtures（test_agent_service / test_studio_materials 同款隔离纪律）──


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(env: Path) -> Path:
    (env / "ai.json").write_text(
        json.dumps({
            "default": "t",
            "profiles": {"t": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "m"}},
            "routing": {},
        }),
        encoding="utf-8",
    )
    return env


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


def _material(env: Path, uri: str = "https://example.test/a", **kw) -> dict:
    row = model.make_material(
        mtype="crawled", source_id="manual", uri=uri, title="素材甲",
        license="CC-BY", payload={"text": "这是一段足够长的素材正文。", "meta": {}},
        fetched_at="2026-11-21T10:00:00", **kw,
    )
    return ms.add_material(row)


# ── 1. parse_ai_confidence 解析容错 ──────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("正文\nCONFIDENCE: 0.8", ("正文", 0.8)),           # 半角冒号
    ("正文\nCONFIDENCE：0.82", ("正文", 0.82)),          # 全角冒号
    ("正文\n置信度: 0.5", ("正文", 0.5)),                # 中文标签
    ("正文\nconfidence: 1", ("正文", 1.0)),              # 边界值 1（大小写不敏感）
    ("正文\nCONFIDENCE: 0", ("正文", 0.0)),              # 边界值 0
    ("正文\nCONFIDENCE: 85%", ("正文", 0.85)),           # 百分数回退形态
    ("正文\nCONFIDENCE: 85", ("正文", 0.85)),            # 裸百分数回退
    ("正文\n\n\nCONFIDENCE: 0.3\n\n", ("正文\n\n\n", 0.3)),  # 尾部空行跳过（只剥标记行）
    ("正文\nconfIDENCE: 0.44", ("正文", 0.44)),          # 大小写不敏感
])
def test_parse_confidence_recognized(raw, expected) -> None:
    assert confidence_mod.parse_ai_confidence(raw) == expected


@pytest.mark.parametrize("raw", [
    "",                                                  # 空串
    "正文只有一段",                                      # 无标记行
    "正文\nCONFIDENCE: high",                            # 非数值（设计文档旧协议）
    "正文\nCONFIDENCE:",                                 # 缺值
    "正文\nCONFIDENCE: 1.5",                             # >1 且 >100 越界
    "正文\nCONFIDENCE: -0.2",                            # 负值
    "正文\n置信度 0.8",                                  # 无冒号
    "正文\nmid CONFIDENCE: 0.8 提及",                    # 行中提及不算
])
def test_parse_confidence_tolerates(raw) -> None:
    """容错契约：解析失败 → (原文原样, None)——没读懂的内容不剥、不崩、不阻断。"""
    assert confidence_mod.parse_ai_confidence(raw) == (raw, None)


def test_parse_confidence_non_string_passthrough() -> None:
    assert confidence_mod.parse_ai_confidence(None) == (None, None)


def test_parse_confidence_strips_only_marker_line() -> None:
    raw = "第一行\n第二行\n\nCONFIDENCE: 0.9"
    out, conf = confidence_mod.parse_ai_confidence(raw)
    assert conf == 0.9
    assert out == "第一行\n第二行\n"  # 只剥标记行，前置空行保留


def test_low_threshold_single_source() -> None:
    assert confidence_mod.CONFIDENCE_LOW_THRESHOLD == 0.5


# ── 2. triage verified / confidence 落库读写 ─────────────────────────


def test_triage_patch_verified_and_confidence_rejected() -> None:
    base = {"read": False, "starred": False, "enabled": True, "tags": [],
            "verified": False, "confidence": None}
    out = triage.triage_patch(base, {"verified": True})
    assert out["verified"] is True
    with pytest.raises(ValueError, match="verified"):
        triage.triage_patch(base, {"verified": "yes"})  # 非布尔拒
    with pytest.raises(ValueError, match="confidence"):
        triage.triage_patch(base, {"confidence": 0.8})  # AI 字段不走人工窄门
    with pytest.raises(ValueError, match="五键"):
        triage.triage_patch(base, {"mood": True})


def test_triage_matches_verified_filter() -> None:
    m = model.make_material(mtype="crawled", source_id="s", uri="u", fetched_at="2026-11-21T10:00:00")
    m = model.normalize_material(m)
    assert triage.matches(m, verified=True) is False  # 缺省未核验
    m["triage"]["verified"] = True
    assert triage.matches(m, verified=True) is True


def test_normalize_defaults_and_dirty_confidence() -> None:
    row = {"id": "x", "type": "crawled", "license": "UNKNOWN", "fetched_at": "t",
           "origin": {"source_id": "s", "uri": "u", "title": ""},
           "provenance": {"chain": [], "derived_from": []}, "payload": {},
           "triage": {"confidence": "high", "verified": "yes"}}  # 双脏
    out = model.normalize_material(row)
    assert out["triage"]["verified"] is False
    assert out["triage"]["confidence"] is None
    row2 = dict(row, triage={"confidence": 0.82, "verified": True})
    out2 = model.normalize_material(row2)
    assert out2["triage"]["confidence"] == 0.82
    assert out2["triage"]["verified"] is True


def test_validate_material_confidence_shape() -> None:
    row = model.make_material(mtype="crawled", source_id="s", uri="u", fetched_at="t")
    assert model.validate_material(row) == []
    bad = model.make_material(mtype="crawled", source_id="s", uri="u2", fetched_at="t",
                              triage={"confidence": 1.5})
    assert any("confidence" in e for e in model.validate_material(bad))
    bad2 = model.make_material(mtype="crawled", source_id="s", uri="u3", fetched_at="t",
                               triage={"verified": "yes"})
    assert any("verified" in e for e in model.validate_material(bad2))


def test_add_material_old_shape_row_idempotent(env) -> None:
    """回归守卫：库内**老四键 triage 行**与重采的六键 make_material 行在归一
    形态下幂等收敛——N-4 扩键不得让存量素材重采误报 immutable 冲突。"""
    legacy = {  # 手造 N-4 之前的老形状行（triage 只有四键）
        "id": "legacy000001", "type": "crawled", "license": "CC-BY",
        "fetched_at": "2026-01-01T00:00:00",
        "origin": {"source_id": "manual", "uri": "https://old.example/1", "title": "老行"},
        "provenance": {"chain": [], "derived_from": []},
        "payload": {"text": "老行正文"},
        "triage": {"read": False, "starred": False, "enabled": True, "tags": []},
    }
    ms._append(legacy)  # 直写文件（绕过 add_material 的归一入口）
    rebuilt = model.make_material(  # 重采路径：新默认六键 triage
        mtype="crawled", source_id="manual", uri="https://old.example/1",
        title="老行", license="CC-BY", payload={"text": "老行正文"},
        fetched_at="2026-01-01T00:00:00", material_id="legacy000001",
    )
    saved = ms.add_material(rebuilt)  # 不抛 immutable 冲突
    path = ms.materials_path()
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 1  # 零追加
    assert saved["triage"]["verified"] is False and saved["triage"]["confidence"] is None


def test_add_material_conflicting_payload_still_rejected(env) -> None:
    first = _material(env)
    conflict = model.make_material(
        mtype="crawled", source_id="manual", uri="https://example.test/a",
        title="素材甲改", license="CC-BY", payload={"text": "被改写"},
        fetched_at="2026-11-21T10:00:00", material_id=first["id"],
    )
    with pytest.raises(ValueError, match="immutable"):
        ms.add_material(conflict)


def test_verified_roundtrip_and_zero_append(env) -> None:
    row = _material(env)
    path = ms.materials_path()
    def _lines() -> int:
        return len(path.read_text(encoding="utf-8").strip().splitlines())
    before = _lines()
    out = ms.update_triage(row["id"], {"verified": True})
    assert out["triage"]["verified"] is True
    assert _lines() == before + 1  # 一次追加
    again = ms.update_triage(row["id"], {"verified": True})
    assert _lines() == before + 1  # 幂等零追加
    assert again["triage"]["verified"] is True
    off = ms.update_triage(row["id"], {"verified": False})
    assert off["triage"]["verified"] is False
    assert _lines() == before + 2


# ── 3. 提炼/综合三路径 confidence 落库 ───────────────────────────────


def test_refine_preview_and_adopt_confidence(env, ai_on, monkeypatch) -> None:
    row = _material(env)

    def fake_chat(messages, *, task=None, **kwargs):
        return "· 关键点一\n· 关键点二\n> 摘要\nCONFIDENCE: 0.8"

    monkeypatch.setattr(refine_service, "_chat", fake_chat)
    out = refine_service.preview(row["id"])
    assert out["confidence"] == 0.8
    assert "CONFIDENCE" in out["text"]  # preview 不剥行（随可编辑预览呈现）

    saved = refine_service.adopt(row["id"], out["text"])  # 原样回传（带标记行）
    derived = ms.get_material(saved["id"])
    assert derived["triage"]["confidence"] == 0.8
    assert "CONFIDENCE" not in (derived["payload"] or {}).get("text", "")  # 入库正文已剥
    assert "关键点一" in derived["payload"]["text"]


def test_refine_adopt_without_marker_confidence_none(env, ai_on, monkeypatch) -> None:
    """容错契约：AI 不返回该字段时不崩、不阻断面——confidence=None 照常落库。"""
    row = _material(env)
    monkeypatch.setattr(refine_service, "_chat", lambda m, **k: "· 只有摘要没有自评")
    out = refine_service.preview(row["id"])
    assert out["confidence"] is None
    saved = refine_service.adopt(row["id"], out["text"])
    assert ms.get_material(saved["id"])["triage"]["confidence"] is None


def test_refine_adopt_explicit_confidence_and_invalid(env, ai_on) -> None:
    row = _material(env)
    saved = refine_service.adopt(row["id"], "· 摘要", confidence=0.55)
    assert ms.get_material(saved["id"])["triage"]["confidence"] == 0.55
    with pytest.raises(refine_service.RefineError, match="0-1"):
        refine_service.adopt(row["id"], "· 摘要", confidence=1.5)
    with pytest.raises(refine_service.RefineError, match="0-1"):
        refine_service.adopt(row["id"], "· 摘要", confidence=True)


def test_refine_adopt_marker_only_body_rejected(env, ai_on) -> None:
    row = _material(env)
    with pytest.raises(refine_service.RefineError, match="无正文"):
        refine_service.adopt(row["id"], "CONFIDENCE: 0.9")


def _distill_fake(monkeypatch, reply: str, calls: list) -> None:
    import musespiral.services.materials_service as M

    def fake_chat_policed(messages, *, task, cache=True):
        calls.append(task)
        return {"text": reply}

    monkeypatch.setattr(M, "_chat_policed", fake_chat_policed)


def test_distill_sparks_confidence_and_tolerance(env, ai_on, tmp_path, monkeypatch) -> None:
    article = tmp_path / "a1.md"
    article.write_text("---\ntitle: 反哺文\n---\n\n正文段落。\n", encoding="utf-8")
    calls: list = []
    _distill_fake(monkeypatch, "论点甲一句话\n论点乙一句话\nCONFIDENCE: 0.7", calls)
    out = ms.add_from_article("a1", "distill", articles_root=tmp_path)
    assert out["points"] == ["论点甲一句话", "论点乙一句话"]  # 标记行不混进论点
    sparks = out["materials"]
    assert sparks and all(s["triage"]["confidence"] == 0.7 for s in sparks)
    assert all(s["provenance"]["chain"] == [{"from_id": "a1", "op": "distill"}] for s in sparks)

    # 容错：无标记行 → confidence=None，sparks 照常落库（不阻断面）
    article2 = tmp_path / "a2.md"
    article2.write_text("---\ntitle: 反哺文二\n---\n\n正文。\n", encoding="utf-8")
    _distill_fake(monkeypatch, "论点丙一句话\n论点丁一句话", calls)
    out2 = ms.add_from_article("a2", "distill", articles_root=tmp_path)
    assert out2["materials"] and all(s["triage"]["confidence"] is None for s in out2["materials"])


class _FakeRunner:
    def __init__(self):
        self.fn = None

    def submit(self, kind, params, fn):
        self.fn = fn
        return "j1"


def _run_synthesis(ai_on, ids, reply: str) -> str:
    runner = _FakeRunner()

    def scripted(messages, *, task=None, temperature=0.6, timeout=120, profile=None):
        return reply  # 原样（不拼 task 后缀——末行标记必须在末尾）

    out = agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=scripted)
    runner.fn("j")
    assert agent_service.read_run(out["run_id"])["status"] == "done"
    return out["run_id"]


def test_synthesis_adopt_confidence_and_strip(env, ai_on) -> None:
    ids = [_material(env, f"https://example.test/s{i}")["id"] for i in (1, 2)]
    run_id = _run_synthesis(ai_on, ids, "## 共同点\n综合稿正文\n\nCONFIDENCE: 0.9")
    out = agent_service.adopt(run_id, target="material")
    derived = ms.get_material(out["id"])
    assert derived["type"] == "derived"
    assert derived["triage"]["confidence"] == 0.9
    assert "CONFIDENCE" not in derived["payload"]["text"]
    assert derived["payload"]["text"].rstrip().endswith("综合稿正文")  # 只剥标记行


def test_synthesis_adopt_without_marker(env, ai_on) -> None:
    ids = [_material(env, f"https://example.test/t{i}")["id"] for i in (1, 2)]
    run_id = _run_synthesis(ai_on, ids, "## 共同点\n没有自评的综合稿")
    out = agent_service.adopt(run_id, target="material")
    assert ms.get_material(out["id"])["triage"]["confidence"] is None


# ── 4. 血统双向 + verify 端点 + confidence=low 回环 ──────────────────


def test_lineage_forward_and_reverse(env) -> None:
    parent = _material(env, "https://example.test/parent")
    child = refine_service.adopt(parent["id"], "· 衍生摘要", confidence=0.6)
    detail = ms.get_detail(child["id"])
    refs = detail["references"]
    assert [(r["from_id"], r["op"], r["deleted"]) for r in refs] == [
        (parent["id"], "summarize", False)]  # 正向：血统链指直接源
    cascade = ms.cascade_preview(parent["id"])
    assert [c["id"] for c in cascade] == [child["id"]]  # 反向：谁引用了这条
    second = refine_service.adopt(parent["id"], "· 第二版摘要", confidence=0.4)
    assert sorted(c["id"] for c in ms.cascade_preview(parent["id"])) == sorted(
        [child["id"], second["id"]])
    ms.delete_material(parent["id"])
    after = ms.get_detail(child["id"])["references"]
    assert after[0]["deleted"] is True  # 原料已删 → 就地徽章语义（非静默失效）


def test_verify_endpoint_roundtrip(server, env) -> None:
    row = _material(env)
    code, payload = _request(server, "POST", "/api/materials/verify",
                             {"id": row["id"], "confirm": True})
    assert code == 200 and payload["ok"] is True and payload["verified"] is True
    code2, body2 = _request(server, "GET", f"/api/materials/detail?id={row['id']}")
    assert body2["material"]["triage"]["verified"] is True
    # 取消核验（同端点 verified=false）
    code3, payload3 = _request(server, "POST", "/api/materials/verify",
                               {"id": row["id"], "verified": False, "confirm": True})
    assert code3 == 200 and payload3["verified"] is False
    code4, body4 = _request(server, "GET", f"/api/materials/detail?id={row['id']}")
    assert body4["material"]["triage"]["verified"] is False


def test_verify_endpoint_validation(server, env) -> None:
    row = _material(env)
    assert _request(server, "POST", "/api/materials/verify", {"confirm": True})[0] == 400
    assert _request(server, "POST", "/api/materials/verify",
                    {"id": row["id"], "verified": "yes", "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/materials/verify",
                    {"id": "nope00000000", "confirm": True})[0] == 400


def test_get_materials_confidence_low_filter(server, env) -> None:
    low = model.make_material(mtype="crawled", source_id="manual", uri="u-low",
                              license="CC-BY", payload={"text": "低"},
                              triage={"confidence": 0.4},
                              fetched_at="2026-11-21T10:00:00")
    low_verified = model.make_material(mtype="crawled", source_id="manual", uri="u-lv",
                                       license="CC-BY", payload={"text": "低但已核验"},
                                       triage={"confidence": 0.3},
                                       fetched_at="2026-11-21T10:03:00")
    high = model.make_material(mtype="crawled", source_id="manual", uri="u-high",
                               license="CC-BY", payload={"text": "高"},
                               triage={"confidence": 0.9},
                               fetched_at="2026-11-21T10:01:00")
    unrate = model.make_material(mtype="crawled", source_id="manual", uri="u-none",
                                 license="CC-BY", payload={"text": "无"},
                                 fetched_at="2026-11-21T10:02:00")
    for row in (low, low_verified, high, unrate):
        ms.add_material(row)
    ms.update_triage(low_verified["id"], {"verified": True})  # 低分但人工已核验 → 出低置信面

    code, body = _request(server, "GET", "/api/materials?confidence=low")
    ids = {m["id"] for m in body["items"]}
    assert code == 200 and ids == {low["id"]}  # 只中「有低分且未核验」（三态互斥）
    assert body["total"] == 1

    code2, _ = _request(server, "GET", "/api/materials?confidence=bogus")
    assert code2 == 400

    code3, body3 = _request(server, "GET", "/api/materials")
    assert len(body3["items"]) == 4  # 缺省不过滤


def test_lineage_js_frontend_literals_have_backend_routes() -> None:
    """血统 tab 前端消费面 ⊆ 后端服务面（lineage.js 独立产物不在 ui.js 字面量
    扫描域——test_architecture 扫 ui.js，本测补 lineage.js 的对拍）。"""
    import re as _re

    from musespiral.studio.actions import _ACTION_ROUTES
    from musespiral.studio.routing import _GET_ROUTES

    src = (Path(__file__).resolve().parent.parent / "musespiral" / "studio"
           / "assets" / "lineage.js").read_text(encoding="utf-8")
    literals = {lit.split("?", 1)[0] for lit in _re.findall(r"'(/api/[^']+)'", src)}
    assert literals == {"/api/materials/detail", "/api/materials/verify"}
    assert "/api/materials/detail" in _GET_ROUTES
    assert "/api/materials/verify" in _ACTION_ROUTES


def test_frontend_confidence_wiring() -> None:
    """前端接线结构锚（N-4）：三态徽/筛选 chip/血统 tab/核验端点四处全接。"""
    studio_dir = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
    core_js = (studio_dir / "assets" / "core.js").read_text(encoding="utf-8")
    mat_js = (studio_dir / "assets" / "views" / "material.js").read_text(encoding="utf-8")
    lineage = (studio_dir / "assets" / "lineage.js").read_text(encoding="utf-8")
    # 三态徽挂在 licBadge 出口（三处展示点零改动全带上）
    assert "function confBadge(m)" in core_js
    assert "confBadge(m);" in core_js and "+confBadge(m)" not in mat_js  # 调用点在 core 内部
    assert "✓ 已核验" in core_js and "待核验" in core_js and "低置信" in core_js
    # 低置信筛选 chip（core fillMatType 建，matQS 带 confidence 参数，loadMaterials 同步激活态）
    assert "'mat-lowconf'" in core_js and "confidence='+state.mconf" in core_js
    # 血统 tab：material.js 只挂 repaint 钩子（byte 纪律），逻辑全在 lineage.js
    assert "__mdpubMatLineage.repaint" in mat_js
    assert "window.__mdpubMatLineage" in lineage and "data-mver" in lineage
    assert "references" in lineage and "cascade" in lineage  # 正向/反向数据面
    # 独立产物进启动装载清单
    assert "'assets/lineage.js'" in core_js
