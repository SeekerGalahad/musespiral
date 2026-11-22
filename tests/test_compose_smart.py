"""FEAT-2 智能组稿测试（services/compose/smart + 端点 + 前端向导）。

正典=docs/项目方案/musespiral-极限架构与功能扩展总规划-2026-11-21.md §FEAT-2。
测试纪律（沿 tests/test_search_service.py）：**不真调 Ollama 也不真调 LLM**——
embedding 全走 monkeypatch（词表同义簇替身，让「语义命中」可确定断言），
chat 全走 monkeypatch（services.compose.smart._chat 模块属性）。MUSESPIRAL_HOME
指 tmp 隔离；端点面真实回环服务器 + http.client（confirm 闸门同面覆盖）。

前端面（三步向导可回退）：沿 test_studio_ui 的源字面量断言惯例——ui.js 是
构建源（conftest 每会话先 build），断言其上的向导结构/回退/绑定。
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
from musespiral.services.compose import smart as smart_mod

# 假 embedding 词表（与 test_search_service 同款：「向量/语义」同义簇一维）
VOCAB = ("嵌入", "检索", "视频", "标签")


def fake_vec(text: str) -> list[float]:
    syn = text.count("向量") + text.count("语义")
    vec = [float(syn)] + [float(text.count(w)) for w in VOCAB]
    return [x + 0.01 for x in vec]


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    monkeypatch.setenv("MUSESPIRAL_INDEX_HOOK", "sync")  # 建索引钩子同步跑（语义路有向量可用）
    monkeypatch.delenv("MUSESPIRAL_EMBED_MODEL", raising=False)
    svc.reset_ready_cache()
    yield home
    svc.reset_ready_cache()


@pytest.fixture
def embed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding, "embed_available", lambda timeout=5: True)
    monkeypatch.setattr(embedding, "embed", lambda texts: [fake_vec(t) for t in texts])


class _FakeChat:
    """记录调用消息、回放固定回复（或抛错模拟 AI 不可用）。"""

    def __init__(self, reply: str | None = None) -> None:
        self.calls: list[list[dict]] = []
        self.reply = reply

    def __call__(self, messages, **_kw):
        self.calls.append(messages)
        if self.reply is None:
            raise RuntimeError("AI 未配置")
        return self.reply


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


# 素材正文带可断言的域内专名（防「空泛大纲」假阳性——测试 14 的判据锚）
TEXT_VEC = "自注意力用 Query-Key-Value 三矩阵实现并行化，取代 RNN 的顺序依赖。"
TEXT_RNN = "RNN 时代长距离依赖难以捕获，LSTM 门控只是缓解。"
TEXT_VIDEO = "视频转写与裁段的生产流程。"

# 假 chat 回复：三段式，要点/序号都指向真实素材内容（1=vec 2=rnn 3=video）
AI_OUTLINE = (
    "开篇·从顺序到并行 | RNN 长距离依赖难以捕获；LSTM 门控只是缓解 | 400 | 2\n"
    "主体·注意力机制 | Query-Key-Value 三矩阵实现并行化 | 800 | 1\n"
    "收尾·工程启示 | 并行化换取全局视野 | 300 | 1,2"
)


def _seed_materials() -> list[str]:
    return [
        ms.add_material(row)["id"]
        for row in (
            _material("vec.md", TEXT_VEC, tags=["检索理论"]),
            _material("rnn.md", TEXT_RNN),
            _material("video.md", TEXT_VIDEO),
        )
    ]


# ── ① 语义搜索返回真实相关素材（FEAT-2 步骤 2 数据面）────────────────


def test_suggest_materials_returns_real_related(_isolated_home, embed_on) -> None:
    ids = _seed_materials()
    hits = smart_mod.suggest_materials("语义检索的工程实践", limit=10)
    assert hits, "语义路（同义簇替身）应召回词面不含「语义」的 vec.md"
    assert hits[0]["id"] in (ids[0], ids[1])  # 相关素材置顶，无关视频沉底/缺席
    assert hits[0]["similarity"] is not None and hits[0]["similarity"] > 0
    assert hits[0]["source_id"] == "kb"
    assert "Query-Key-Value" in hits[0]["preview"]  # 摘要预览来自真实正文
    assert "payload" not in hits[0]  # 裁剪面：正文不整块回传


def test_suggest_materials_rejects_empty_topic(_isolated_home, embed_on) -> None:
    with pytest.raises(Exception, match="主题描述不能为空"):
        smart_mod.suggest_materials("   ")


# ── ② smart_start：素材全部入池 + AI 大纲基于素材内容（非空泛）────────


def test_smart_start_pools_all_and_outline_grounded(
    _isolated_home, embed_on, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_materials()
    fake = _FakeChat(AI_OUTLINE)
    monkeypatch.setattr(smart_mod, "_chat", fake)
    root = tmp_path / "articles"
    root.mkdir()

    res = smart_mod.smart_start("注意力机制演进科普", ids, root)
    slug = res["slug"]
    assert res["attached"] == 3 and res["material_ids"] == ids
    assert res["outline_source"] == "ai"

    # 素材全部入池：GET session 断言（material://<id> 直引形态）
    from musespiral.services import compose_service as cs

    detail = cs.session_detail(slug, root)
    pooled = {s.get("material_id") for s in detail["sources"]}
    assert pooled == set(ids)

    # 大纲：三段式，每段标题 + 要点 + 引用素材 id，要点来自真实素材内容
    sections = detail["outline"]["sections"]
    assert len(sections) == 3
    joined_points = "；".join("；".join(s.get("points") or []) for s in sections)
    for keyword in ("长距离依赖", "Query-Key-Value"):
        assert keyword in joined_points, "要点必须含素材正文专名（非空泛）"
    for s in sections:
        assert s["title"].strip()
        assert s.get("material_ids"), "每段必须引用至少一条素材 id"
        assert set(s["material_ids"]) <= set(ids)  # 只认真实 id（幻觉序号被滤）
    # 素材序号映射：末段声明 1,2 → 两 id
    assert set(sections[2]["material_ids"]) == {ids[0], ids[1]}

    # 大纲建议确实以素材内容为底（喂给 AI 的 prompt 含真实正文）
    prompt = fake.calls[0][0]["content"]
    assert "Query-Key-Value 三矩阵" in prompt and TEXT_RNN in prompt


def test_smart_start_fallback_when_ai_down(
    _isolated_home, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_materials()
    monkeypatch.setattr(smart_mod, "_chat", _FakeChat(None))  # AI 抛错
    root = tmp_path / "articles"
    root.mkdir()

    res = smart_mod.smart_start("注意力机制演进科普", ids, root)
    assert res["outline_source"] == "fallback"  # 诚实标注，不冒充 AI
    sections = res["outline"]["sections"]
    assert len(sections) == 3
    assert all(s.get("material_ids") for s in sections)  # 兜底也引用真实素材
    assert any("Query-Key-Value" in (s.get("points") or [""])[0] for s in sections)


def test_smart_start_validation_faces(_isolated_home, tmp_path: Path) -> None:
    root = tmp_path / "articles"
    root.mkdir()
    ids = _seed_materials()
    with pytest.raises(Exception, match="主题描述不能为空"):
        smart_mod.smart_start("  ", ids, root)
    with pytest.raises(Exception, match="material_ids 必须是非空数组"):
        smart_mod.smart_start("t", [], root)
    with pytest.raises(Exception, match="素材不存在或已删"):
        smart_mod.smart_start("t", ["deadbeef0000"], root)  # 建会话前早失败
    from musespiral.services import compose_service as cs

    assert cs.list_sessions(root) == []  # 无半成品会话残留


def test_smart_start_without_outline(_isolated_home, tmp_path: Path) -> None:
    ids = _seed_materials()
    root = tmp_path / "articles"
    root.mkdir()
    res = smart_mod.smart_start("t", ids[:1], root, generate_outline=False)
    assert res["outline"]["sections"] == [] and res["outline_source"] is None
    assert res["attached"] == 1


# ── ③ 端点全链（真实回环服务器：探测 → 确认 → GET session 断言）───────


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
    if "?" in path:
        base, _, qs = path.partition("?")
        path = base + "?" + quote(qs, safe="=&")
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data)


def test_endpoint_full_chain_search_confirm_and_session(
    server, studio_root, _isolated_home, embed_on, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全链（任务书测试 12/13/14）：探测搜素材 → 确认建会话 → GET session 断言。"""
    monkeypatch.setattr(smart_mod, "_chat", _FakeChat(AI_OUTLINE))
    ids = _seed_materials()

    # 步骤 2 数据面：语义搜候选（相似度 + 来源 + 摘要预览）
    status, data = _request(
        server, "POST", "/api/compose/smart-start", {"topic": "语义检索的工程实践", "confirm": True}
    )
    assert status == 200 and data["ok"] is True and data["semantic_ready"] is True
    assert data["suggestions"] and data["suggestions"][0]["id"] in ids
    assert "Query-Key-Value" in data["suggestions"][0]["preview"]

    # 步骤 3 确认：建会话 + 素材全部入池 + AI 三段式大纲
    status, data = _request(
        server,
        "POST",
        "/api/compose/smart-start",
        {"topic": "注意力机制演进科普", "material_ids": ids, "confirm": True},
    )
    assert status == 200 and data["ok"] is True
    slug = data["slug"]
    assert data["attached"] == 3 and data["outline_source"] == "ai"
    sections = data["outline"]["sections"]
    assert len(sections) == 3
    joined = "；".join("；".join(s.get("points") or []) for s in sections)
    assert "Query-Key-Value" in joined and "长距离依赖" in joined

    # GET session：素材全部入池（material://<id>）
    status, data = _request(server, "GET", "/api/compose/session?slug=" + slug)
    assert status == 200  # 详情载荷无 ok 键（错误面走 _error_payload）
    assert {s.get("material_id") for s in data["sources"]} == set(ids)
    assert {s["path"] for s in data["sources"]} == {f"material://{i}" for i in ids}
    assert [s["material_ids"] for s in data["outline"]["sections"]]  # 大纲引用随详情下发


def test_endpoint_validation_and_confirm_gate(server, studio_root, _isolated_home) -> None:
    status, _ = _request(server, "POST", "/api/compose/smart-start", {"confirm": True})
    assert status == 400  # 缺 topic
    status, _ = _request(server, "POST", "/api/compose/smart-start", {"topic": "t"})
    assert status == 400  # 缺 confirm（人审闸门）
    _seed_materials()
    status, data = _request(
        server,
        "POST",
        "/api/compose/smart-start",
        {"topic": "t", "material_ids": [], "confirm": True},
    )
    assert status == 200 and data["ok"] is False and "material_ids" in data["error"]
    status, data = _request(
        server,
        "POST",
        "/api/compose/smart-start",
        {"topic": "t", "material_ids": ["deadbeef0000"], "confirm": True},
    )
    assert status == 200 and data["ok"] is False and "素材不存在" in data["error"]


def test_endpoint_outline_replace_edits_smart_outline(
    server, studio_root, _isolated_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    """向导「进入工坊」的整表覆写面：改标题/删段/调序经 action=replace 落盘。"""
    monkeypatch.setattr(smart_mod, "_chat", _FakeChat(AI_OUTLINE))
    ids = _seed_materials()
    _, data = _request(
        server,
        "POST",
        "/api/compose/smart-start",
        {"topic": "注意力机制", "material_ids": ids, "confirm": True},
    )
    slug = data["slug"]
    sections = data["outline"]["sections"]
    # 前端编辑动作的等价载荷：删末段 + 剩余两段调序 + 改首段标题/要点
    edited = [
        dict(sections[1], title="改后的主体段", points=["并行化换取全局视野"], direction="并行化换取全局视野"),
        dict(sections[0]),
    ]
    status, data = _request(
        server,
        "POST",
        "/api/compose/outline",
        {"slug": slug, "action": "replace", "sections": edited, "confirm": True},
    )
    assert status == 200 and data["ok"] is True
    status, data = _request(server, "GET", "/api/compose/session?slug=" + slug)
    assert status == 200
    secs = data["outline"]["sections"]
    assert [s["title"] for s in secs] == ["改后的主体段", sections[0]["title"]]
    assert secs[0]["points"] == ["并行化换取全局视野"]
    assert secs[1]["material_ids"] == sections[0]["material_ids"]  # 编辑保留引用
    # 空表 / 非对象数组 → 400
    status, _ = _request(
        server, "POST", "/api/compose/outline",
        {"slug": slug, "action": "replace", "sections": [], "confirm": True},
    )
    assert status == 200  # 走服务层校验


# ── ④ 前端三步向导（结构 + 可回退；源字面量断言沿 test_studio_ui 惯例）──


def _ui_js() -> str:
    return (Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.js").read_text(
        encoding="utf-8"
    )


def _compose_html() -> str:
    base = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
    return (base / "ui.views" / "compose.html").read_text(encoding="utf-8")


def test_frontend_wizard_three_steps_and_rollback() -> None:
    src = _ui_js()
    html = _compose_html()
    # 入口 + 三步骨架（步骤条 + 三个步骤面板）
    assert 'id="cm-smart-btn"' in html and "✨ 智能组稿" in html
    assert html.count('class="sc-pane"') == 3
    assert 'id="sc-steps"' in html and html.count('<span class="sc-step">') == 3
    # 可回退：scBack 只在 step>1 且未确认时回退一步；back 按钮接线
    assert "function scBack(){if(SC.step>1&&!SC.done)scGoto(SC.step-1);}" in src
    assert "$('sc-back').addEventListener('click',scBack);" in src
    assert "$('sc-back').hidden=SC.step===1||SC.done;" in src
    # 三步推进：1 搜索 / 2 确认建会话 / 3 进入工坊
    assert "$('sc-next').textContent=SC.step===1?'下一步：搜索素材':(SC.done?'进入工坊':'确认并生成');" in src
    assert "scGoto(2)" in src and "SC.done=true" in src
    # 步骤 2 数据面：全选/取消全选/相似度排序 + 追加搜索
    for needle in ("$('sc-all')", "$('sc-none')", "$('sc-sort')", "$('sc-search-btn')"):
        assert needle in src
    # 端点消费（契约锁同步面）
    assert "postJSON('/api/compose/smart-start',{topic:q,limit:10,confirm:true})" in src
    assert (
        "postJSON('/api/compose/smart-start',{topic:SC.topic,"
        "material_ids:ids,generate_outline:true,confirm:true})" in src
    )
    # 大纲就地编辑（改标题/删段/调序）+ 进入工坊整表覆写
    assert "data-sc-title" in src and "data-sc-pts" in src
    assert "data-sc-up" in src and "data-sc-dn" in src and "data-sc-del" in src
    assert "action:'replace',sections:secs,confirm:true" in src
    # 确认后跳转工坊
    assert "location.hash='#compose?s='+encodeURIComponent(SC.slug);" in src
