"""FEAT-4 小说家双件套（正典=《musespiral-极限架构与功能扩展总规划-2026-11-21》
§FEAT-4）：人物关系图 + 伏笔追踪。

覆盖面：
  a) **人物关系**（meta.relations）：校验（to 非空/不自指/上限）、归一与渲染
     （`关系：陈默=宿敌` 进 prompt 注入行）、素材面 add_character 带 relations
     落 meta、幂等跨批次宽容（FEAT-4 前存量卡无 relations 键重提不误判异内容）、
     generate prompt 真注入；
  b) **伏笔账本**（session.foreshadowing）：planted/escalated/payoff 三态 +
     CRUD（id 自增/封版拒绝/未知 id 拒绝）+ 存量 session.json 无键零迁移 +
     脏 status 归一 planted + prompt 注入（{{foreshadowing}} 八槽；未回收才进，
     全回收/空账本整节缺席）+ generate 结果回传未回收清单（**提示不拦截**：
     blocks 原样）；
  c) **端点**：/api/compose/novel-summary 三新 action（foreshadow_add/
     foreshadow_update/foreshadow_remove）+ character_create 带 relations ——
     POST 面零新增（契约锁不漂移，test_architecture 兜底）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral.core.composition import (
    Block,
    character_from_meta,
    normalize_character,
    open_foreshadowing,
    parse_foreshadowing,
    render_character,
    render_foreshadowing,
    validate_character,
    validate_foreshadowing,
)
from musespiral.services import compose_service, materials_service

REL_CHARACTER = {
    "name": "林远",
    "role": "研究员",
    "appearance": "瘦高",
    "speech_style": "短句",
    "key_events": ["第3章 初次出场"],
}
RELATIONS = [{"to": "陈默", "relation": "宿敌"}, {"to": "苏眉", "relation": "前妻"}]

REPLY_TWO_CHAPTERS = (
    "<<<block kind:heading>>># 第一章 异变\n\n"
    "<<<block kind:para>>>陈默睁开眼，左眼发烫。\n\n"
    "<<<block kind:heading>>># 第二章 借据\n\n"
    "<<<block kind:para>>>林远递来一份文件。\n"
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir()
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "articles"
    r.mkdir()
    return r


# ══ a) 人物关系（meta.relations）═══════════════════════════════════════


def test_relations_validation() -> None:
    """校验：[{to, relation}] 合法；非数组/缺 to/自指/字段错型 → 错误清单。"""
    assert validate_character({"name": "林远", "relations": RELATIONS}) == []
    assert "relations 必须是数组" in " ".join(
        validate_character({"name": "林远", "relations": "陈默=宿敌"})
    )
    errors = " ".join(validate_character({"name": "林远", "relations": [{"relation": "宿敌"}]}))
    assert "relations[0].to 必须是非空字符串" in errors
    errors = " ".join(
        validate_character({"name": "林远", "relations": [{"to": "林远", "relation": "自己"}]})
    )
    assert "不能是自己" in errors
    errors = " ".join(
        validate_character({"name": "林远", "relations": [{"to": "陈默", "relation": 5}]})
    )
    assert "relations[0].relation 必须是字符串" in errors


def test_relations_normalize_and_render() -> None:
    """归一去空白丢空 to；渲染进 prompt 注入行（`关系：陈默=宿敌`）。"""
    card = normalize_character({
        "name": "林远",
        "relations": [{"to": " 陈默 ", "relation": " 宿敌 "}, {"to": "", "relation": "x"}, "bad"],
    })
    assert card["relations"] == [{"to": "陈默", "relation": "宿敌"}]
    # 缺省（不传 relations）→ 空表（存量卡形态不变）
    assert normalize_character({"name": "林远"})["relations"] == []
    # 渲染：关系缀在注入行末；空关系整段缺席
    line = render_character({"name": "林远", "relations": RELATIONS})
    assert "关系：陈默=宿敌 / 苏眉=前妻" in line
    assert "关系" not in render_character({"name": "林远"})
    # 关系缺描述 → 「关系未注明」占位（不留空等号）
    assert "陈默=关系未注明" in render_character(
        {"name": "林远", "relations": [{"to": "陈默", "relation": ""}]}
    )
    # meta → 卡视图带 relations（读侧零第二套键名）
    view = character_from_meta({"name": "林远", "relations": [{"to": "陈默", "relation": "宿敌"}]})
    assert view["relations"] == [{"to": "陈默", "relation": "宿敌"}]
    assert character_from_meta({"name": "林远"})["relations"] == []
    assert character_from_meta({"name": "林远", "relations": "坏"})["relations"] == []


def test_add_character_with_relations_and_legacy_dedup() -> None:
    """建卡带 relations 落 meta；幂等重提（含 relations）dedup；FEAT-4 前存量
    卡（无 relations 键）重提同内容仍 dedup——不因新增字段误判「内容不同」。"""
    created = materials_service.add_character("林远", role="研究员", relations=RELATIONS)
    meta = created["material"]["payload"]["meta"]
    assert meta["relations"] == RELATIONS
    assert meta["sub_type"] == "character"
    # 幂等重提（同 relations）
    again = materials_service.add_character("林远", role="研究员", relations=RELATIONS)
    assert again["dedup"] is True
    # 异 relations → 拒绝（卡是快照）
    with pytest.raises(ValueError, match="内容不同"):
        materials_service.add_character("林远", role="研究员", relations=[{"to": "陈默", "relation": "挚友"}])
    # 存量卡宽容：手工写入一张无 relations 键的旧卡（模拟 FEAT-4 前）
    legacy = materials_service.add_character("陈默", role="主角")
    legacy_id = legacy["material"]["id"]
    from musespiral.core.materials import model as mat_model

    row = mat_model.make_material(
        mtype="text",
        source_id="novel",
        uri="character://陈默",
        title="陈默",
        license="UNKNOWN",
        payload={"text": "# 陈默", "meta": {"sub_type": "character", "name": "陈默",
                                            "role": "主角", "appearance": "",
                                            "speech_style": "", "key_events": []}},
        fetched_at=legacy["material"]["fetched_at"],
    )
    assert row["id"] == legacy_id
    # 旧卡重提同内容（不传 relations=缺省空表）→ dedup 不误判
    re_submit = materials_service.add_character("陈默", role="主角")
    assert re_submit["dedup"] is True
    # 读面：get_character 带回 relations
    card = materials_service.get_character(created["material"]["id"])
    assert card is not None and card["relations"] == RELATIONS


def test_relations_injected_into_generate_prompt(root: Path) -> None:
    """挂卡带关系 → generate prompt 含 `关系：`（AI 写作知道人物关系网）。"""
    compose_service.new_session("rel1", "关系测试", root=root, kind="novel")
    mid = materials_service.add_character("林远", relations=RELATIONS)["material"]["id"]
    compose_service.add_character("rel1", mid, root=root)
    captured: dict = {}

    def _gen(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return REPLY_TWO_CHAPTERS

    compose_service.generate("rel1", root=root, chat_fn=_gen)
    assert "人物卡" in captured["prompt"]
    assert "关系：陈默=宿敌 / 苏眉=前妻" in captured["prompt"]


# ══ b) 伏笔账本（session.foreshadowing）═══════════════════════════════


def test_foreshadowing_core_validators() -> None:
    """校验/未回收过滤/渲染三态：note 必填；payoff 不进 open；渲染带状态标签。"""
    assert validate_foreshadowing([{"note": "文件内容未交代", "chapter": "第3章"}]) == []
    errors = " ".join(validate_foreshadowing([{"note": "  "}]))
    assert "note 必须是非空字符串" in errors
    errors = " ".join(validate_foreshadowing([{"note": "x", "status": "done"}]))
    assert "status 必须是 planted/escalated/payoff" in errors
    assert validate_foreshadowing("nope") == ["伏笔必须是数组（每项 {note, chapter?, status?}）"]

    items = [
        {"id": "f1", "note": "文件内容未交代", "chapter": "第3章", "status": "planted"},
        {"id": "f2", "note": "左眼异变与旧实验有关", "chapter": "", "status": "escalated"},
        {"id": "f3", "note": "借据真相", "chapter": "第7章", "status": "payoff"},
    ]
    # 未回收 = status != payoff（planted + escalated）
    assert [x["id"] for x in open_foreshadowing(items)] == ["f1", "f2"]
    # 渲染只含未回收；带 [埋设]/[推进] 标签与章节前缀
    text = render_foreshadowing(items)
    assert "[埋设] 第3章：文件内容未交代" in text
    assert "[推进] 左眼异变与旧实验有关" in text
    assert "借据真相" not in text
    # 全回收 / 空账本 → 空串（prompt 整节缺席）
    assert render_foreshadowing([items[2]]) == ""
    assert render_foreshadowing([]) == ""
    # 宽容读：脏条目跳过、脏 status 归一 planted
    parsed = parse_foreshadowing([
        {"id": "f1", "note": "有效", "chapter": "", "status": "escalated"},
        {"id": "f2", "note": ""},              # 空描述丢弃
        "garbage",                              # 非 dict 跳过
        {"id": "f3", "note": "脏态", "status": "done"},  # 脏 status → planted
    ])
    assert [x["id"] for x in parsed] == ["f1", "f3"]
    assert parsed[1]["status"] == "planted"
    assert parse_foreshadowing(None) == [] and parse_foreshadowing("bad") == []


def test_foreshadowing_crud_and_session_roundtrip(root: Path) -> None:
    """CRUD：id 自增 f1/f2、状态推进、删除；session.json 往返；详情下发。"""
    compose_service.new_session("fore1", "伏笔账本", root=root, kind="novel")
    out = compose_service.add_foreshadowing("fore1", "文件内容未交代", "第3章", root=root)
    assert out["added"]["id"] == "f1" and out["added"]["status"] == "planted"
    out = compose_service.add_foreshadowing("fore1", "左眼异变与旧实验有关", "", root=root)
    assert out["added"]["id"] == "f2"  # 删了 f1 后再建不回收 id
    compose_service.remove_foreshadowing("fore1", "f1", root=root)
    out = compose_service.add_foreshadowing("fore1", "第三条", "", root=root)
    assert out["added"]["id"] == "f3"
    # 状态推进：planted → escalated → payoff；全回收后 open 为空
    compose_service.update_foreshadowing("fore1", "f2", root=root, status="escalated")
    compose_service.update_foreshadowing("fore1", "f2", root=root, status="payoff")
    compose_service.update_foreshadowing("fore1", "f3", root=root, status="payoff")
    detail = compose_service.session_detail("fore1", root=root)
    ledger = detail["foreshadowing"]
    assert [x["status"] for x in ledger] == ["payoff", "payoff"]
    assert open_foreshadowing(ledger) == []
    # 手改 note/chapter（只改给出的字段）
    compose_service.update_foreshadowing("fore1", "f3", root=root, note="改描述", chapter="第9章")
    ledger = compose_service.session_detail("fore1", root=root)["foreshadowing"]
    assert ledger[1]["note"] == "改描述" and ledger[1]["chapter"] == "第9章"
    assert ledger[1]["status"] == "payoff"  # 未给 status 不动

    # 错误面：未知 id / 空描述 / 非法状态 / 封版拒绝
    with pytest.raises(compose_service.ComposeError, match="伏笔不存在"):
        compose_service.update_foreshadowing("fore1", "f9", root=root, status="payoff")
    with pytest.raises(compose_service.ComposeError, match="note 必须是非空字符串"):
        compose_service.add_foreshadowing("fore1", "  ", root=root)
    with pytest.raises(compose_service.ComposeError, match="status 必须是"):
        compose_service.update_foreshadowing("fore1", "f3", root=root, status="done")
    compose_service.generate("fore1", root=root, chat_fn=lambda m, **k: REPLY_TWO_CHAPTERS)
    # 采纳封版后账本只读
    detail = compose_service.session_detail("fore1", root=root)
    last = detail["rounds"][-1]
    for b in last["blocks"]:
        compose_service.decide("fore1", last["n"], b["id"], "keep", root=root)
    compose_service.adopt("fore1", root=root)
    with pytest.raises(compose_service.ComposeError, match="已采纳"):
        compose_service.add_foreshadowing("fore1", "封版后", root=root)
    with pytest.raises(compose_service.ComposeError, match="已采纳"):
        compose_service.remove_foreshadowing("fore1", "f2", root=root)


def test_legacy_session_without_foreshadowing_key(root: Path) -> None:
    """存量 session.json 无 foreshadowing 键 → 读侧 []（零迁移零回归），
    写面落盘后键出现；Block 冒烟保 import 兼容。"""
    compose_service.new_session("legacy", "旧会话", root=root, kind="novel")
    d = compose_service.session_dir("legacy", root=root)
    meta = json.loads((d / "session.json").read_text(encoding="utf-8"))
    assert meta["foreshadowing"] == []  # new_session 空态显式落盘
    # 手工剥掉键模拟存量 → 装载归 [] 不崩
    del meta["foreshadowing"]
    (d / "session.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    assert compose_service.session_detail("legacy", root=root)["foreshadowing"] == []
    compose_service.add_foreshadowing("legacy", "旧会话新伏笔", root=root)
    assert compose_service.session_detail("legacy", root=root)["foreshadowing"][0]["note"] == "旧会话新伏笔"
    assert Block("b1", "para", "x").to_dict()["kind"] == "para"


def test_foreshadowing_injected_into_prompt_and_generate_hint(root: Path) -> None:
    """{{foreshadowing}} 八槽注入：未回收清单进 generate prompt；全回收/空
    整节缺席；generate 结果回传未回收清单且 **blocks 原样**（提示不拦截）。"""
    compose_service.new_session("fore2", "注入", root=root, kind="novel")
    captured: dict = {}

    def _gen(messages, **kw):
        captured["prompt"] = messages[0]["content"]
        return REPLY_TWO_CHAPTERS

    # 空账本：整节缺席
    compose_service.generate("fore2", root=root, chat_fn=_gen)
    assert "未回收伏笔" not in captured["prompt"]
    # 埋两条（一 planted 一 escalated）→ 注入且只含未回收
    compose_service.add_foreshadowing("fore2", "文件内容未交代", "第3章", root=root)
    compose_service.add_foreshadowing("fore2", "左眼异变伏笔", "", root=root,
                                      status="escalated")
    out = compose_service.generate("fore2", root=root, chat_fn=_gen)
    assert "【未回收伏笔】" in captured["prompt"]
    assert "[埋设] 第3章：文件内容未交代" in captured["prompt"]
    assert "[推进] 左眼异变伏笔" in captured["prompt"]
    # 生成结果：未回收清单随载荷回传（提示不拦截——blocks 原样）
    assert [x["note"] for x in out["foreshadowing"]] == ["文件内容未交代", "左眼异变伏笔"]
    assert any("陈默睁开眼" in b["text"] for b in out["blocks"])
    # 全回收：整节再次缺席 + 结果清单空
    compose_service.update_foreshadowing("fore2", "f1", root=root, status="payoff")
    compose_service.update_foreshadowing("fore2", "f2", root=root, status="payoff")
    out = compose_service.generate("fore2", root=root, chat_fn=_gen)
    assert "【未回收伏笔】" not in captured["prompt"]
    assert out["foreshadowing"] == []


def test_outline_expand_carries_foreshadowing(root: Path) -> None:
    """大纲逐节展开同链：prompt 注入未回收伏笔 + 结果回传清单（不旁路）。"""
    compose_service.new_session("fore3", "大纲", root=root, kind="novel")
    compose_service.add_foreshadowing("fore3", "旧实验清单", "第1章", root=root)
    calls: list[str] = []

    def _chat(messages, *, task=None, **kw):
        calls.append(messages[0]["content"])
        if len(calls) == 1:
            return "第一节 | 400 | 开篇"
        return "<<<block kind:para>>>正文。\n"

    compose_service.outline_generate("fore3", root=root, chat_fn=_chat)
    compose_service.outline_approve("fore3", "s1", True, root=root)
    out = compose_service.expand_section("fore3", "s1", root=root, chat_fn=_chat)
    assert "【未回收伏笔】" in calls[-1] and "旧实验清单" in calls[-1]
    assert [x["note"] for x in out["foreshadowing"]] == ["旧实验清单"]


# ══ c) Studio 端点（POST 面零新增——action 分流）═══════════════════════


def _server(root: Path):
    from musespiral import studio

    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post(server, path: str, body: dict):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(server, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def test_api_foreshadow_and_relations_forms(root: Path) -> None:
    """三伏笔 action + character_create 带 relations：金路径与 400 分档。"""
    compose_service.new_session("api4", "端点", root=root, kind="novel")
    server, thread = _server(root)
    try:
        # action 枚举外（含旧枚举外的伏笔错拼）
        assert _post(server, "/api/compose/novel-summary",
                     {"slug": "api4", "action": "foreshadow_up", "confirm": True})[0] == 400
        # foreshadow_add：缺 note → 400；金路径 → f1
        assert _post(server, "/api/compose/novel-summary",
                     {"slug": "api4", "action": "foreshadow_add", "confirm": True})[0] == 400
        code, payload = _post(server, "/api/compose/novel-summary", {
            "slug": "api4", "action": "foreshadow_add", "note": "文件内容未交代",
            "chapter": "第3章", "confirm": True,
        })
        assert code == 200 and payload["added"]["id"] == "f1"
        # foreshadow_update：状态推进 + 手改 note；非法 status → ok:false（业务面）
        code, payload = _post(server, "/api/compose/novel-summary", {
            "slug": "api4", "action": "foreshadow_update", "id": "f1",
            "status": "escalated", "confirm": True,
        })
        assert code == 200 and payload["foreshadowing"][0]["status"] == "escalated"
        code, payload = _post(server, "/api/compose/novel-summary", {
            "slug": "api4", "action": "foreshadow_update", "id": "f1",
            "status": "done", "confirm": True,
        })
        assert code == 200 and payload["ok"] is False
        # foreshadow_remove：未知 id → ok:false
        code, payload = _post(server, "/api/compose/novel-summary", {
            "slug": "api4", "action": "foreshadow_remove", "id": "f9", "confirm": True,
        })
        assert code == 200 and payload["ok"] is False
        # 详情面下发账本
        code, detail = _get(server, "/api/compose/session?slug=api4")
        assert detail["foreshadowing"][0]["note"] == "文件内容未交代"
        # character_create 带 relations → 金路径回卡视图
        code, payload = _post(server, "/api/compose/novel-summary", {
            "action": "character_create", "name": "林远",
            "relations": [{"to": "陈默", "relation": "宿敌"}], "confirm": True,
        })
        assert code == 200 and payload["character"]["relations"] == [
            {"to": "陈默", "relation": "宿敌"}
        ]
        # relations 错型（to 非字符串）→ 400
        assert _post(server, "/api/compose/novel-summary", {
            "action": "character_create", "name": "坏卡",
            "relations": [{"to": 5}], "confirm": True,
        })[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_api_generate_job_result_carries_foreshadowing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """job.result 必带 foreshadowing（前端 cmGenerate 读 j.result.foreshadowing
    渲染📌提示条——闭包漏传则前端永不出现，同 conflicts 判例）。"""
    import time as _time

    # Studio 走 on_delta 流式路径（E-4）——假件替换 _chat_stream（与
    # test_generate_job_result_carries_conflicts 同判例：门面 __setattr__
    # 会把名字同步转发到 generate 子模块）。
    monkeypatch.setattr(compose_service, "_chat_stream",
                        lambda messages, **kw: REPLY_TWO_CHAPTERS)

    compose_service.new_session("job4", "任务", root=root, kind="novel")
    compose_service.add_foreshadowing("job4", "文件内容未交代", "第3章", root=root)
    server, thread = _server(root)
    try:
        code, payload = _post(server, "/api/compose/generate",
                              {"slug": "job4", "confirm": True})
        assert code == 200 and payload.get("job_id")
        job = None
        for _ in range(200):  # ≤10s 轮询至终态（job 面无长等待——假件秒回）
            code, job = _get(server, f"/api/jobs/{payload['job_id']}")
            if job.get("status") in ("done", "failed"):
                break
            _time.sleep(0.05)
        assert job and job["status"] == "done", job
        result = job["result"] or {}
        assert [x["note"] for x in result["foreshadowing"]] == ["文件内容未交代"]
        assert any("陈默睁开眼" in b["text"] for b in result["blocks"])  # 提示不拦截
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
