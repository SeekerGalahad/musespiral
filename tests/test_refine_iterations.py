"""P2 素材再加工循环测试（2026-11-20，正典=《musespiral-格式生态与素材流转能力
分析-2026-11-20》§3.3）。

覆盖：adopt 缺省 as_iteration=True 的迭代链三键（iteration 递增 / 链根
一致与继承 / iteration_of 指上一版）/ 从中间版再迭代 / as_iteration=False
向后兼容（不写迭代键）/ list_iterations 版本列表形状 / diff_iterations 复用
core/version/diff（增删行数正确）/ 跨链拒绝 / Studio 两 GET 端点回环
（/api/materials/iterations 与 /iterations/diff）。MUSESPIRAL_HOME 隔离同
test_refine_service.py 惯例；服务层测试零 AI 依赖（adopt 不调 chat）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.services import materials_service as ms
from musespiral.services import refine_service as svc


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


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


def _request(server, method: str, path: str):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(data)
    except json.JSONDecodeError:
        return resp.status, data


def _src_material(env: Path) -> dict:
    """带正文的源素材（payload.text 面，无需 KB 根/文件）。"""
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/iter", title="迭代源",
        license="CC-BY", payload={"text": "源素材的完整正文。"},
        fetched_at="2026-11-20T09:00:00",
    )
    ms.add_material(row)
    return row


def _iter_meta(material_id: str) -> dict:
    m = ms.get_material(material_id)
    payload = m.get("payload") or {}
    meta = payload.get("meta")
    return meta if isinstance(meta, dict) else {}


# ── 服务层：迭代链三键 ──────────────────────────────────────────────


def test_iteration_keys_constant_in_model() -> None:
    assert model.META_ITERATION_KEYS == ("iteration", "iteration_root", "iteration_of")


def test_adopt_three_times_builds_chain(env: Path) -> None:
    """源素材 adopt 三次 → v1/v2/v3：iteration 递增、root 一致、of 指上一版。"""
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "第一版正文")
    v2 = svc.adopt(src["id"], "第二版正文")
    v3 = svc.adopt(src["id"], "第三版正文")
    for vid, expect_iter, expect_of in (
        (v1["id"], 1, None), (v2["id"], 2, v1["id"]), (v3["id"], 3, v2["id"]),
    ):
        meta = _iter_meta(vid)
        assert meta["iteration"] == expect_iter
        assert meta["iteration_root"] == src["id"]
        assert meta["iteration_of"] == expect_of
    # provenance.chain 仍指各自直接源（血统准确性不破）
    assert ms.get_material(v1["id"])["provenance"]["chain"] == [
        {"from_id": src["id"], "op": "summarize"}
    ]


def test_adopt_from_mid_version_inherits_root(env: Path) -> None:
    """从 v2 再 adopt → v3：iteration=3 且链根不变（继承 v2 的 iteration_root）。"""
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "第一版正文")
    v2 = svc.adopt(src["id"], "第二版正文")
    v3 = svc.adopt(v2["id"], "第三版正文（基于 v2 改 prompt）")
    meta = _iter_meta(v3["id"])
    assert meta["iteration"] == 3
    assert meta["iteration_root"] == src["id"]  # root 不变
    assert meta["iteration_of"] == v2["id"]
    # chain 指向 v2（采纳时传的 material_id——直接源准确）
    assert ms.get_material(v3["id"])["provenance"]["chain"] == [
        {"from_id": v2["id"], "op": "summarize"}
    ]


def test_adopt_as_iteration_false_keeps_legacy_shape(env: Path) -> None:
    """向后兼容：as_iteration=False → meta 不写任何迭代键（旧行为）。"""
    src = _src_material(env)
    out = svc.adopt(src["id"], "旧式采纳正文", as_iteration=False)
    payload = ms.get_material(out["id"])["payload"]
    meta = payload.get("meta")
    if meta is not None:  # 源无 license_note 时连 meta 都不写（旧行为）
        for key in model.META_ITERATION_KEYS:
            assert key not in meta
    # 该素材自身成为新链根（无 iteration_root → 自身即根），版本列表为空
    listing = svc.list_iterations(out["id"])
    assert listing == {"root": out["id"], "versions": []}


def test_adopt_writes_iteration_alongside_license_note(env: Path) -> None:
    """license_note 继承与迭代键共存（meta 合并不互斥）。"""
    src = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/lic", title="带声明源",
        license="ALL-RIGHTS",
        payload={"text": "正文", "meta": {"license_note": "声明原文"}},
        fetched_at="2026-11-20T09:10:00",
    )
    ms.add_material(src)
    out = svc.adopt(src["id"], "采纳正文")
    meta = _iter_meta(out["id"])
    assert meta["license_note"] == "声明原文"
    assert meta["iteration"] == 1
    assert meta["iteration_root"] == src["id"]
    assert meta["iteration_of"] is None


# ── 服务层：版本列表 ────────────────────────────────────────────────


def test_list_iterations_ascending_with_chars(env: Path) -> None:
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "一版")
    svc.adopt(src["id"], "第二版正文更长一些")
    listing = svc.list_iterations(src["id"])
    assert listing["root"] == src["id"]
    assert [v["iteration"] for v in listing["versions"]] == [1, 2]
    first = listing["versions"][0]
    assert first["id"] == v1["id"]
    assert first["chars"] == len("一版")
    assert first["title"].startswith("摘要 · 迭代源")
    assert first["ts"]  # fetched_at 非空
    # 用链中任一成员查询同链同果（root_or_any_id 语义）
    assert svc.list_iterations(v1["id"])["root"] == src["id"]


def test_list_iterations_missing_material_rejected(env: Path) -> None:
    with pytest.raises(svc.RefineError, match="不存在"):
        svc.list_iterations("nope00000000")


# ── 服务层：两版对比 ────────────────────────────────────────────────


def test_diff_iterations_added_removed(env: Path) -> None:
    """两版明显差异 → diff_result 统计增删正确（v1 删两行 v2 增三行）。"""
    src = _src_material(env)
    text_a = "保留行一\n删除行甲\n删除行乙\n保留行二"
    text_b = "保留行一\n新增行丙\n新增行丁\n新增行戊\n保留行二"
    v1 = svc.adopt(src["id"], text_a)
    v2 = svc.adopt(src["id"], text_b)
    out = svc.diff_iterations(v1["id"], v2["id"])
    assert out["a"] == {"id": v1["id"], "iteration": 1, "title": ms.get_material(v1["id"])["origin"]["title"]}
    assert out["b"]["id"] == v2["id"]
    assert out["diff"]["removed"] == 2
    assert out["diff"]["added"] == 3
    assert out["diff"]["identical"] is False
    kinds = {row["kind"] for hunk in out["diff"]["hunks"] for row in hunk["rows"]}
    assert {"del", "ins"} <= kinds  # 红删绿增都在（ctx 上下文行随 diff_result 缺省存在）
    # 服务端渲染 html 红删绿增（E-6 同款产物面）
    assert 'class="dl-del"' in out["diff"]["html"]
    assert 'class="dl-add"' in out["diff"]["html"]
    assert "删除行甲" in out["diff"]["html"]


def test_diff_iterations_identical_versions(env: Path) -> None:
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "完全相同的正文")
    v2 = svc.adopt(src["id"], "完全相同的正文")
    out = svc.diff_iterations(v1["id"], v2["id"])
    assert out["diff"]["identical"] is True
    assert out["diff"]["hunks"] == []


def test_diff_iterations_cross_chain_rejected(env: Path) -> None:
    """跨链 diff 拒绝：不同 root 的两个素材（两个独立源各采一版）。"""
    src_a = _src_material(env)
    src_b = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/other", title="另一源",
        payload={"text": "另一源正文"}, fetched_at="2026-11-20T09:20:00",
    )
    ms.add_material(src_b)
    v_a = svc.adopt(src_a["id"], "链 A 的 v1")
    v_b = svc.adopt(src_b["id"], "链 B 的 v1")
    with pytest.raises(svc.RefineError, match="跨迭代链"):
        svc.diff_iterations(v_a["id"], v_b["id"])


def test_diff_iterations_missing_material_rejected(env: Path) -> None:
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "一版")
    with pytest.raises(svc.RefineError, match="不存在"):
        svc.diff_iterations(v1["id"], "nope00000000")


# ── Studio 端点回环（真实 HTTP 服务器）───────────────────────────────


def test_endpoint_iterations_list_and_diff(server, env: Path) -> None:
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "保留行\n删除行")
    v2 = svc.adopt(src["id"], "保留行\n新增行")
    # 列表
    status, body = _request(server, "GET", f"/api/materials/iterations?id={src['id']}")
    assert status == 200
    assert body["ok"] is True
    assert body["root"] == src["id"]
    assert [v["iteration"] for v in body["versions"]] == [1, 2]
    assert {v["id"] for v in body["versions"]} == {v1["id"], v2["id"]}
    # 用版本 id 查同链
    status, body_v = _request(server, "GET", f"/api/materials/iterations?id={v1['id']}")
    assert status == 200 and body_v["root"] == src["id"]
    # diff
    status, body_d = _request(
        server, "GET",
        f"/api/materials/iterations/diff?a={v1['id']}&b={v2['id']}",
    )
    assert status == 200
    assert body_d["diff"]["removed"] == 1
    assert body_d["diff"]["added"] == 1
    assert 'class="dl-del"' in body_d["diff"]["html"]


def test_endpoint_iterations_param_errors(server, env: Path) -> None:
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "一版")
    # 缺 id / 缺 a、b / 只给一侧
    assert _request(server, "GET", "/api/materials/iterations")[0] == 400
    assert _request(server, "GET", "/api/materials/iterations/diff")[0] == 400
    assert _request(server, "GET", f"/api/materials/iterations/diff?a={v1['id']}")[0] == 400
    # 不存在 → 404
    assert _request(server, "GET", "/api/materials/iterations?id=nope00000000")[0] == 404
    status, _ = _request(
        server, "GET", f"/api/materials/iterations/diff?a={v1['id']}&b=nope00000000"
    )
    assert status == 404


def test_endpoint_diff_cross_chain_rejected_400(server, env: Path) -> None:
    src_a = _src_material(env)
    src_b = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/other2", title="另一源",
        payload={"text": "另一源正文"}, fetched_at="2026-11-20T09:30:00",
    )
    ms.add_material(src_b)
    v_a = svc.adopt(src_a["id"], "链 A 的 v1")
    v_b = svc.adopt(src_b["id"], "链 B 的 v1")
    status, body = _request(
        server, "GET", f"/api/materials/iterations/diff?a={v_a['id']}&b={v_b['id']}"
    )
    assert status == 400
    assert "跨迭代链" in body["error"]


def test_endpoint_adopt_via_refine_action_writes_iteration(server, env: Path) -> None:
    """既有 POST /api/action/refine adopt 零改动即写迭代键（POST 零新增契约）。"""
    src = _src_material(env)
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", "/api/action/refine",
        body=json.dumps({
            "confirm": True, "action": "adopt", "id": src["id"], "text": "经端点采纳的正文",
        }, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    assert resp.status == 200 and out["ok"] is True
    meta = _iter_meta(out["id"])
    assert meta["iteration"] == 1
    assert meta["iteration_root"] == src["id"]
    assert meta["iteration_of"] is None


# ── 回退进链（P2 验收红线：回退=旧版正文重新采纳为新版，链上追加原版保留）──


def test_rollback_readopt_appends_new_version(env: Path) -> None:
    """v1/v2/v3 后回退 v1 → v4：iteration=4、iteration_of=v3、正文==v1 原文。

    回退的写路径就是 adopt（iterations.js revert 的服务层语义）：拿 v1 的
    正文再采纳——同链根判定使新行落在同一链上（回退也进链），且 v1..v3
    原行原样保留（追加式，无重写/无删除）。
    """
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "第一版正文")
    v2 = svc.adopt(v1["id"], "第二版正文")
    v3 = svc.adopt(v2["id"], "第三版正文")
    v1_text = (ms.get_material(v1["id"])["payload"] or {}).get("text") or ""
    v4 = svc.adopt(v1["id"], v1_text)  # 回退 v1：以 v1 为源重新采纳 v1 正文
    meta = _iter_meta(v4["id"])
    assert meta["iteration"] == 4
    assert meta["iteration_root"] == src["id"]
    assert meta["iteration_of"] == v3["id"]  # 接在链尾（v3），不是接在 v1 后
    assert (ms.get_material(v4["id"])["payload"] or {}).get("text") == "第一版正文"
    # 原版保留：v1..v3 三行仍在且内容未动
    assert (ms.get_material(v1["id"])["payload"] or {}).get("text") == "第一版正文"
    assert (ms.get_material(v3["id"])["payload"] or {}).get("text") == "第三版正文"
    # 链上四版升序；回退产物与 v1 内容一致（diff identical）
    listing = svc.list_iterations(src["id"])
    assert [v["iteration"] for v in listing["versions"]] == [1, 2, 3, 4]
    assert listing["versions"][-1]["id"] == v4["id"]
    assert svc.diff_iterations(v4["id"], v1["id"])["diff"]["identical"] is True


def test_endpoint_rollback_via_frontend_composition(server, env: Path) -> None:
    """端点级回退进链：按 iterations.js revert 的原样两步组合——
    GET /api/materials/detail 取旧版正文 → POST /api/action/refine adopt
    重新采纳 → GET /api/materials/iterations 见 4 版（回退进链）。"""
    src = _src_material(env)
    v1 = svc.adopt(src["id"], "回退链 v1")
    v2 = svc.adopt(v1["id"], "回退链 v2")
    v3 = svc.adopt(v2["id"], "回退链 v3")
    # 第一步：前端 revert() 的 detail 读取（payload.material.payload.text）
    status, detail = _request(server, "GET", f"/api/materials/detail?id={v1['id']}")
    assert status == 200
    text = ((detail.get("material") or {}).get("payload") or {}).get("text") or ""
    assert text == "回退链 v1"
    # 第二步：adopt 重新采纳（同 iterations.js 的请求体形状）
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(
        "POST", "/api/action/refine",
        body=json.dumps({
            "confirm": True, "action": "adopt", "id": v1["id"], "text": text,
        }, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    assert resp.status == 200 and out["ok"] is True
    # 第三步：列表见 4 版、末版 iteration=4
    status, listing = _request(server, "GET", f"/api/materials/iterations?id={src['id']}")
    assert status == 200
    assert [v["iteration"] for v in listing["versions"]] == [1, 2, 3, 4]
    assert listing["versions"][-1]["id"] == out["id"]
