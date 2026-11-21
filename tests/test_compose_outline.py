"""G-T2 大纲模式（正典=《musespiral-调研吸收功能设计-2026-11-18》§三）。

覆盖：
  - 纯数据面 validate/to_json/from_json + 状态机（core/composition/outline）
  - outline CRUD（save/generate/update/approve）+ 裁决 gating（未裁决不可展开）
  - expand_section：prompt 含本节 direction + 已展开节摘要，产物进正常块协议
  - Studio POST /api/compose/outline 四动作 + confirm 闸门 + 契约锁成员
  - 前端结构：右栏 details#cm-outline + outline.js 独立装载
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from musespiral.core.composition.outline import (
    STATUS_APPROVED,
    STATUS_EXPANDED,
    STATUS_PENDING,
    OutlineSection,
    from_json,
    to_json,
    validate_sections,
)

from musespiral.services import compose_service

OUTLINE_GEN = (
    "一、引言 | 120 | 讲背景\n"
    "二、第一律 | 500 | 用对比写法\n"
    "三、结论 | 200 |\n"
)
EXPAND_R1 = "<<<block kind:para>>>第一节正文，讲背景。\n\n<<<block kind:para>>>第一节结尾。"
EXPAND_R2 = "<<<block kind:para>>>第二节正文，按方向对比展开。"


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


class _FakeChat:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, messages, *, task=None, **kw):
        assert task == "compose"
        self.calls.append(messages)
        return self.replies.pop(0)


def _fake(monkeypatch, *replies: str) -> _FakeChat:
    fake = _FakeChat(*replies)
    monkeypatch.setattr(compose_service, "_chat", fake)
    return fake


# ── 纯数据面 ──────────────────────────────────────────────────────────

def test_outline_pure_validation_and_roundtrip() -> None:
    secs = validate_sections([
        {"id": "s1", "title": "引言", "target_chars": 120, "direction": "对比", "status": "approved"},
        {"id": "s2", "title": "第一律", "target_chars": 500},
    ])
    assert [s.status for s in secs] == [STATUS_APPROVED, STATUS_PENDING]
    data = to_json(secs, current_expanding="s2")
    back, current = from_json(data)
    assert current == "s2"
    assert back[0].to_dict()["direction"] == "对比"
    # 非法：空表 / 重复 id / 空 title / 负字数 —— 全拒
    for bad in ([], [{"id": "x", "title": "a"}, {"id": "x", "title": "b"}],
                [{"id": "x", "title": "  "}], [{"id": "x", "title": "a", "target_chars": -1}]):
        with pytest.raises(ValueError):
            validate_sections(bad)
    assert from_json("not-a-dict") == ([], None)  # 宽容读


# ── 服务层 CRUD / 裁决 / gating ────────────────────────────────────────

def test_outline_crud_and_approval(root: Path, monkeypatch) -> None:
    compose_service.new_session("demo", "大纲测试", root=root)
    # 无大纲 → 空 outline
    assert compose_service.outline_get("demo", root=root)["sections"] == []
    # save（手写）
    ol = compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "target_chars": 120, "direction": "", "status": "pending"},
    ], root=root)
    assert len(ol["sections"]) == 1
    # update
    ol = compose_service.outline_update_section("demo", "s1", root=root, direction="改用对比", target_chars=200)
    assert ol["sections"][0]["direction"] == "改用对比"
    assert ol["sections"][0]["target_chars"] == 200
    # approve → status=approved；approve(false) → 回 pending
    ol = compose_service.outline_approve("demo", "s1", True, root=root)
    assert ol["sections"][0]["status"] == STATUS_APPROVED
    ol = compose_service.outline_approve("demo", "s1", False, root=root)
    assert ol["sections"][0]["status"] == STATUS_PENDING
    with pytest.raises(compose_service.ComposeError, match="不存在"):
        compose_service.outline_approve("demo", "nope", True, root=root)
    # 落盘形状（outline.json）
    d = root / "demo.compose"
    assert json.loads((d / "outline.json").read_text(encoding="utf-8"))["sections"][0]["id"] == "s1"
    # 详情随带 outline（零新 GET 端点）
    assert compose_service.session_detail("demo", root=root)["outline"]["sections"][0]["id"] == "s1"


def test_outline_generate_parses_lines(root: Path, monkeypatch) -> None:
    compose_service.new_session("demo", "三律", root=root)
    fake = _fake(monkeypatch, OUTLINE_GEN)
    ol = compose_service.outline_generate("demo", root=root)
    assert len(ol["sections"]) == 3
    assert ol["sections"][0]["title"].startswith("一、引言")
    assert ol["sections"][1]["direction"] == "用对比写法"
    assert ol["sections"][1]["target_chars"] == 500
    assert all(s["status"] == STATUS_PENDING for s in ol["sections"])
    assert "三律" in fake.calls[0][0]["content"]  # 主题进生成 prompt


def test_expand_gating_unapproved_refused(root: Path, monkeypatch) -> None:
    compose_service.new_session("demo", "T", root=root)
    compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "status": "pending"},
    ], root=root)
    _fake(monkeypatch, EXPAND_R1)
    with pytest.raises(compose_service.ComposeError, match="尚未裁决"):
        compose_service.expand_section("demo", "s1", root=root)
    # 裁决后可展开
    compose_service.outline_approve("demo", "s1", True, root=root)
    res = compose_service.expand_section("demo", "s1", root=root)
    assert res["round"] == 1 and len(res["blocks"]) == 2
    # 展开后节变 expanded，再展开被拒
    assert res["outline"]["sections"][0]["status"] == STATUS_EXPANDED
    with pytest.raises(compose_service.ComposeError, match="尚未裁决"):
        compose_service.expand_section("demo", "s1", root=root)


def test_expand_prompt_has_direction_and_prior_summary(root: Path, monkeypatch) -> None:
    compose_service.new_session("demo", "T", root=root)
    compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "direction": "背景先行", "status": "pending"},
        {"id": "s2", "title": "第一律", "direction": "用 CleanYourTV 对比", "status": "pending"},
    ], root=root)
    fake = _fake(monkeypatch, EXPAND_R1, EXPAND_R2)
    compose_service.outline_approve("demo", "s1", True, root=root)
    compose_service.outline_approve("demo", "s2", True, root=root)
    # 展开第一节：prompt 含本节方向
    r1 = compose_service.expand_section("demo", "s1", root=root)
    p1 = fake.calls[0][0]["content"]
    assert "背景先行" in p1 and "引言" in p1
    assert "无——本节为首节" in p1
    # 展开第二节：prompt 含本节 direction + 已展开节摘要（防跑偏核心）
    r2 = compose_service.expand_section("demo", "s2", root=root)
    p2 = fake.calls[1][0]["content"]
    assert "用 CleanYourTV 对比" in p2
    assert r1["summary"] in p2  # 第一节摘要注入第二节 prompt
    assert r2["round"] == 2  # 进正常轮次协议


# ── 集成修复批（2026-11-19）回归面 ─────────────────────────────────────
#
# 五项缺陷的机器门补课：多节 adopt 丢节【高】/ 摘要竞态【中】/ 插槽二次吃
# token【中】/ prompts.d 覆盖键不生效【中】/ 大纲行 lstrip 吃数字标题【中】。


def test_adopt_merges_all_expanded_rounds(root: Path, monkeypatch) -> None:
    """大纲模式采纳 = 按轮序合并**全部**展开轮次（此前只取末轮，正稿丢前序节）。

    正典 §三流程终点：大纲 → 逐节裁决 → 逐节展开 → 采纳出全文。三节全展开
    全 keep 后，正稿必须含三节正文。
    """
    compose_service.new_session("demo", "三节全文", root=root)
    compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "status": "pending"},
        {"id": "s2", "title": "机制", "status": "pending"},
        {"id": "s3", "title": "结论", "status": "pending"},
    ], root=root)
    for sid in ("s1", "s2", "s3"):
        compose_service.outline_approve("demo", sid, True, root=root)
    replies = {
        "s1": "<<<block kind:para>>>第一节正文标记SECONE结尾。",
        "s2": "<<<block kind:para>>>第二节正文标记SECTWO结尾。",
        "s3": "<<<block kind:para>>>第三节正文标记SECTHREE结尾。",
    }
    for sid in ("s1", "s2", "s3"):
        compose_service.expand_section("demo", sid, root=root,
                                       chat_fn=lambda m, _s=sid, **k: replies[_s])
    session, _ = compose_service._load_session("demo", root)
    assert len(session.rounds) == 3
    for r in session.rounds:
        for b in r.blocks:
            compose_service.decide("demo", r.n, b.id, "keep", root=root)
    result = compose_service.adopt("demo", root=root)
    body = (root / "demo.md").read_text(encoding="utf-8")
    assert "SECONE" in body and "SECTWO" in body and "SECTHREE" in body
    assert result["merged_blocks"] == 3


def test_adopt_outline_refuses_undecided_round(root: Path, monkeypatch) -> None:
    """大纲模式采纳的块级人工红线：任一轮整轮未裁决 → 可读拒绝（不静默丢节）。"""
    compose_service.new_session("demo", "T", root=root)
    compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "status": "pending"},
        {"id": "s2", "title": "机制", "status": "pending"},
    ], root=root)
    compose_service.outline_approve("demo", "s1", True, root=root)
    compose_service.outline_approve("demo", "s2", True, root=root)
    for sid in ("s1", "s2"):
        compose_service.expand_section("demo", sid, root=root,
                                       chat_fn=lambda m, **k: "<<<block kind:para>>>节内容。")
    session, _ = compose_service._load_session("demo", root)
    for b in session.rounds[0].blocks:
        compose_service.decide("demo", 1, b.id, "keep", root=root)
    with pytest.raises(compose_service.ComposeError, match="第 2 轮尚无任何裁决"):
        compose_service.adopt("demo", root=root)


def test_outline_parse_digit_titles(root: Path) -> None:
    """标记剥离只认「数字+标点」的列表记号——数字开头标题不被吃。

    旧实现 lstrip("-*0123456789.、 ") 把「3 条铁律」剥成「条铁律」。
    """
    secs = compose_service._parse_outline_lines(
        "3 条铁律 | 500 | 对比\n"
        "2026 年展望 | 400 |\n"
        "1.5 倍增长的陷阱 | 300 | 数据\n"
        "1. 缘起 | 120 | 背景\n"
        "12、机制 | 800 | 拆解\n"
        "- 列表项 | 100 |\n"
        "一、引言 | 120 |"
    )
    assert [s.title for s in secs] == [
        "3 条铁律", "2026 年展望", "1.5 倍增长的陷阱", "缘起", "机制", "列表项", "一、引言",
    ]
    assert secs[0].target_chars == 500 and secs[0].direction == "对比"
    assert secs[3].direction == "背景"  # "1. 缘起" 的有序标记被剥、方向保留


def test_outline_templates_prompts_d_override(
    root: Path, monkeypatch: pytest.MonkeyPatch, _isolate_home: Path
) -> None:
    """prompts.d 覆盖级联对 compose_outline / compose_expand 两键生效。

    代码注释一直承诺此覆盖，但实现此前用硬编码模板（探针判例：override
    applied=False）——本用例锁住接线。
    """
    prompts_dir = _isolate_home / "prompts.d"
    prompts_dir.mkdir()
    (prompts_dir / "compose_outline.txt").write_text(
        "自定义大纲模板 主题={{title}} 源={{sources}}", encoding="utf-8"
    )
    (prompts_dir / "compose_expand.txt").write_text(
        "自定义展开 本节={{title}} 字数={{target_chars}} 方向={{direction}} "
        "前情={{prior}} 摘要={{rolling}} 风格={{style}}",
        encoding="utf-8",
    )
    compose_service.new_session("demo", "覆盖主题", root=root)
    fake = _fake(monkeypatch, "甲节 | 100 | 对比写", "<<<block kind:para>>>节内容。")
    compose_service.outline_generate("demo", root=root)
    gen_prompt = fake.calls[0][0]["content"]
    assert gen_prompt.startswith("自定义大纲模板") and "覆盖主题" in gen_prompt

    compose_service.outline_approve("demo", "s1", True, root=root)
    compose_service.set_rolling_summary("demo", "脉络摘要文字。", root=root)
    compose_service.expand_section("demo", "s1", root=root)
    exp_prompt = fake.calls[1][0]["content"]
    assert exp_prompt.startswith("自定义展开")
    assert "甲节" in exp_prompt and "对比写" in exp_prompt and "脉络摘要文字。" in exp_prompt
    assert "前情=（无——本节为首节）" in exp_prompt  # 插槽照常填充


def test_expand_direction_tokens_stay_literal(root: Path, monkeypatch) -> None:
    """direction 里的 {{token}} 保持字面（单遍填充）——不被后续插槽二次消费。"""
    compose_service.new_session("demo", "T", root=root)
    compose_service.outline_save("demo", [
        {"id": "s1", "title": "引言", "direction": "用 {{prior}} 占位对比", "status": "pending"},
    ], root=root)
    fake = _fake(monkeypatch, EXPAND_R1)
    compose_service.outline_approve("demo", "s1", True, root=root)
    compose_service.expand_section("demo", "s1", root=root)
    prompt = fake.calls[0][0]["content"]
    assert "{{prior}}" in prompt  # 字面保留
    assert prompt.count("（无——本节为首节）") == 1  # 只有真插槽一处填充


# ── Studio 端点 ────────────────────────────────────────────────────────

def test_studio_outline_endpoint_and_confirm(root: Path, monkeypatch) -> None:
    import http.client
    import threading
    from musespiral import studio

    httpd = studio.make_server(root, port=0)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def req(method, path, body=None):
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data)

    try:
        assert req("POST", "/api/compose/new", {"slug": "d", "confirm": True})[0] == 200
        # confirm 闸门：缺 confirm → 400
        code, _ = req("POST", "/api/compose/outline", {"slug": "d", "action": "generate"})
        assert code == 400
        # action 枚举外 → 400
        code, _ = req("POST", "/api/compose/outline", {"slug": "d", "action": "nope", "confirm": True})
        assert code == 400
        # update + approve + expand 经端点（generate 需真 AI，此处用 update 建节）
        code, payload = req("POST", "/api/compose/outline", {
            "slug": "d", "action": "update", "section": "s1", "title": "引言", "confirm": True})
        # 无大纲时 update 找不到节 → 200 ok:false（可读拒绝）
        assert code == 200 and payload["ok"] is False
        # 先 generate（注入假 chat）；studio 走模块级 _chat
        fake = _fake(monkeypatch, OUTLINE_GEN, EXPAND_R1)
        code, payload = req("POST", "/api/compose/outline", {"slug": "d", "action": "generate", "confirm": True})
        assert code == 200 and payload["ok"] and len(payload["outline"]["sections"]) == 3
        sec = payload["outline"]["sections"][0]["id"]
        code, payload = req("POST", "/api/compose/outline", {
            "slug": "d", "action": "approve", "section": sec, "approved": True, "confirm": True})
        assert code == 200 and payload["outline"]["sections"][0]["status"] == STATUS_APPROVED
        code, payload = req("POST", "/api/compose/outline", {
            "slug": "d", "action": "expand", "section": sec, "confirm": True})
        assert code == 200 and payload["ok"] and payload["round"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        t.join(timeout=2)


def test_contract_lock_and_frontend_structure(root: Path) -> None:
    from musespiral.studio.actions import _ACTION_ROUTES
    from tests.test_architecture import CONTRACT_POST_EXACT

    assert "/api/compose/outline" in _ACTION_ROUTES
    assert "/api/compose/outline" in CONTRACT_POST_EXACT
    # 前端：右栏 details#cm-outline + outline.js 独立装载（不塞 core.js）
    shell = (Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "ui.html").read_text(encoding="utf-8")
    assert 'id="cm-outline"' in shell
    assert "cm-outline-gen" in shell and "cm-outline-list" in shell and "cm-outline-expand" in shell
    ol_js = (Path(__file__).resolve().parent.parent / "musespiral" / "studio" / "assets" / "outline.js").read_text(encoding="utf-8")
    assert "/api/compose/outline" in ol_js
    assert "data-ol-approve" in ol_js and "data-ol-dir" in ol_js and "data-ol-expand" in ol_js
