"""编排服务（Phase D F75 执行面 + F76 双采纳目标）测试。

覆盖 §五用例矩阵两组：
  - **F75-8**：agent_service 走 jobs（mock gateway）——job kind=agent_run、
    done 后 result 只含计数（**无 out_text 键**）、run 目录 run.json +
    steps/*.json 落盘形状=§1.1。
  - **F76-1..6**：展平边界（2/8 篇、1 篇/9 篇拒）、无正文面构造期拒绝
    （**零 job 零 run 目录**）、采纳 derived 血统与 license 最严者胜两组、
    compose 桥块锚、采纳前零写、API 契约幂等。

测试档位（§五「三档纲领」）：本文件全是 ②mock chat 集成——真 job/真文件/
真路由（MUSESPIRAL_HOME 沙箱），chat 经 `chat_fn` 参数注入替代真实网络。构造期
拒绝的纯函数面（`build_synthesis` 边界）已在 `test_agent_plans_builtin.py`
锁定，此处不重复，只测**服务层的拒绝路径与副作用面**（零 job 零目录）。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.services import agent_service, compose_service
from musespiral.services import materials_service as ms
from musespiral.services import sources_service as src_svc

# 测试用的脚本化 chat_fn（替换真实 client.chat；签名对齐 chat_policed 的 chat_fn 位）
Reply = str


def _scripted_chat(reply: str = "综合稿正文"):
    """chat_fn：gateway 把 messages 传进来，返字符串即可（gateway 记 chars）。"""
    calls: list[dict] = []

    def _fn(messages, *, task=None, temperature=0.6, timeout=120, profile=None):
        calls.append({"task": task, "chars": sum(len(m.get("content") or "") for m in messages)})
        return f"{reply}｜{task}"

    _fn.calls = calls
    return _fn


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


def _seed_materials(env: Path, n: int = 2, *, mtype: str = "crawled",
                    license: str | None = None) -> list[str]:
    """造 n 条有正文素材，返回**本次新建**的 n 个 id（按调用序）。

    `license` 缺省按 CC-BY / UNKNOWN / ALL-RIGHTS 循环（便于「最严者胜」用例
    取到不同组合）；给定则全部同值。返回 id 由 (source_id, type, uri) 稳定
    散列，故 uri 里带序号——重复调用同一 n 会重造同 uri（幂等返回既有行）。
    """
    licenses = ("CC-BY", "UNKNOWN", "ALL-RIGHTS")
    ids: list[str] = []
    for i in range(1, n + 1):
        row = model.make_material(
            mtype=mtype,
            source_id="manual",
            uri=f"https://example.test/{i}",
            title=f"素材{i}",
            license=license or licenses[(i - 1) % 3],
            payload={"text": f"素材{i}正文" * 40,
                     "meta": {"license_note": f"note-{i}"}},
            fetched_at=f"2026-11-17T09:0{i}:00",
        )
        saved = ms.add_material(row)
        ids.append(saved["id"])
    return ids


def _kb_seed(env: Path) -> list[str]:
    """kb 源 + 2 篇有 local_path 的 text 素材（走 KB 根读取路径）。"""
    root = env / "kb"
    root.mkdir()
    for i in (1, 2):
        (root / f"doc{i}.md").write_text(f"---\ntitle: 原文{i}\n---\n\n知识库文档{i}正文。\n" * 3,
                                         encoding="utf-8")
    src_svc.register_source({
        "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
        "config": {"root": str(root), "sources": []},
        "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
    })
    ids: list[str] = []
    for i in (1, 2):
        row = model.make_material(
            mtype="text", source_id="kb", uri=f"doc{i}.md", title=f"原文{i}", license="CC-BY",
            payload={"local_path": f"doc{i}.md", "meta": {"license_note": f"CC-BY doc{i}"}},
            fetched_at=f"2026-11-17T09:1{i}:00",
        )
        ms.add_material(row)
        ids.append(row["id"])
    return ids


def _runs_dirs(env: Path) -> list[Path]:
    root = env / "agent_runs"
    return [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []


def _jobs(env: Path) -> list[dict]:
    path = env / "jobs.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class _FakeRunner:
    """最小 runner 替身：submit 只记录参数并返回固定 job_id（不跑线程池）。"""

    def __init__(self) -> None:
        self.kind = self.params = self.fn = None

    def submit(self, kind, params, fn):
        self.kind, self.params, self.fn = kind, params, fn
        return "job00001"


# ── F75-8：agent_service 走 jobs（result 只计数、run 目录形状）────────


def test_agent_service_submits_jobs_and_result_has_no_body(ai_on, tmp_path) -> None:
    ids = _seed_materials(ai_on, 2)
    runner = _FakeRunner()
    out = agent_service.submit_to_runner(
        runner, "synthesis_v1", ids, chat_fn=_scripted_chat()
    )
    assert runner.kind == "agent_run"
    assert runner.params["run_id"] == out["run_id"]
    assert runner.params["material_ids"] == ids
    result = runner.fn("job00001")
    # result 只含四键计数，**绝不含 out_text/prompt**（设计 §1.1）
    assert set(result) == {"run_id", "status", "steps_done", "chars_out"}
    assert "out_text" not in json.dumps(result)
    assert result["status"] == "done" and result["steps_done"] == 3


def test_run_dir_shape_matches_design_section_1_1(ai_on) -> None:
    ids = _seed_materials(ai_on, 2)
    runner = _FakeRunner()
    out = agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=_scripted_chat())
    runner.fn("j")
    directory = agent_service.run_dir(out["run_id"])
    run_json = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert set(run_json) >= {"id", "plan", "status", "started", "finished",
                            "inputs", "budget", "steps"}
    assert run_json["status"] == "done"
    assert run_json["plan"] == "synthesis_v1"
    assert run_json["inputs"]["material_ids"] == ids
    assert [s["id"] for s in run_json["steps"]] == ["extract_1", "extract_2", "synthesize"]
    # steps/<n>.json：每步 {id, task, status, out_text}
    step_files = sorted(p.name for p in (directory / "steps").glob("*.json"))
    assert step_files == ["1.json", "2.json", "3.json"]
    step3 = json.loads((directory / "steps" / "3.json").read_text(encoding="utf-8"))
    assert step3["id"] == "synthesize" and step3["task"] == "agent_synthesize"
    assert step3["out_text"].startswith("综合稿正文")
    # run.json 与步骤文件里**不含 prompt 全文**（设计 §1.1 防敏感溢出）
    assert "素材1正文" not in json.dumps(run_json, ensure_ascii=False)


def test_run_failed_maps_to_job_failed_with_readable_error(ai_on, monkeypatch) -> None:
    """run failed → job failed，error 含步 id（§四 Q2 run→job 终态映射）。"""
    ids = _seed_materials(ai_on, 2)

    def _boom(messages, **kw):
        raise RuntimeError("网络断了")

    runner = _FakeRunner()
    out = agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=_boom)
    with pytest.raises(RuntimeError, match="extract_1"):
        runner.fn("j")
    meta = agent_service.read_run(out["run_id"])
    assert meta["status"] == "failed"
    assert "extract_1" in meta["error"]


def test_run_budget_stopped_is_job_done_not_failed(ai_on) -> None:
    """budget_stopped → job done（result.status 区分，非 failed）；已完步存证保留。"""
    ids = _seed_materials(ai_on, 2)
    runner = _FakeRunner()
    out = agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=_scripted_chat())
    # 手工调低头一步后的 out 预算：2 步各 2000 → 上限 2000 只够第一步
    built = agent_service.build_run("synthesis_v1", ids)
    plan = json.loads(json.dumps(built["plan"]))
    plan["budget"]["out_chars"] = plan["steps"][0]["max_out"]
    meta = agent_service._execute(
        out["run_id"], plan, built["ctx"], built["material_ids"],
        chat_fn=_scripted_chat(),
    )
    assert meta["status"] == "budget_stopped"
    assert meta.get("error") is None  # 守门不是错误（run.json 不写 error 键）
    assert len(meta["steps"]) == 1  # 第一步完整存证
    # 映射：budget_stopped 走 job done（_job_result 而非抛错）
    result = agent_service._job_result(meta)
    assert result["status"] == "budget_stopped" and result["steps_done"] == 1


# ── F76-1：展平边界（服务层拒绝路径）────────────────────────────────


def test_build_run_two_and_eight_materials(ai_on) -> None:
    """2 篇（下界）与 8 篇（上界）都通过：R3.5 批 RUN_STEPS 8→16 后 9 步
    计划不再撞步数上限（原「8 篇执行期被拒」的张力已消解，见 budgets 注）。"""
    ids2 = _seed_materials(ai_on, 2)
    built = agent_service.build_run("synthesis_v1", ids2)
    assert len(built["plan"]["steps"]) == 3

    ids8 = _seed_materials(ai_on, 8)  # 追加 6 条
    built8 = agent_service.build_run("synthesis_v1", ids8)
    assert len(built8["plan"]["steps"]) == 9


def test_build_run_one_and_nine_materials_rejected(ai_on) -> None:
    _seed_materials(ai_on, 9)
    all_ids = [m["id"] for m in ms.list_materials()]
    with pytest.raises(agent_service.AgentServiceError, match="2-8 篇素材，实际 1 篇"):
        agent_service.build_run("synthesis_v1", all_ids[:1])
    with pytest.raises(agent_service.AgentServiceError, match="2-8 篇素材，实际 9 篇"):
        agent_service.build_run("synthesis_v1", all_ids[:9])


def test_build_run_unknown_plan_rejected(ai_on) -> None:
    _seed_materials(ai_on, 2)
    ids = [m["id"] for m in ms.list_materials()]
    with pytest.raises(agent_service.AgentServiceError, match="未知计划"):
        agent_service.build_run("nope_plan", ids)


# ── F76-2：无正文面构造期拒绝（零 job 零 run 目录）────────────────────


def test_radar_signal_rejected_at_construction_zero_side_effects(ai_on) -> None:
    """radar_signal 素材 → 构造抛可读错误含 type 与 id；**零 job 零 run 目录**。"""
    ids = _seed_materials(ai_on, 2)
    radar = model.make_material(
        mtype="radar_signal", source_id="radarhub", uri="https://github.com/a/b",
        title="a/b", license="UNKNOWN",
        payload={"url": "https://github.com/a/b", "meta": {"grade": "S", "score": 90}},
        fetched_at="2026-11-17T09:30:00",
    )
    ms.add_material(radar)
    before_jobs = len(_jobs(ai_on))
    before_runs = len(_runs_dirs(ai_on))
    with pytest.raises(agent_service.AgentServiceError, match="radar_signal") as err:
        agent_service.build_run("synthesis_v1", [ids[0], radar["id"]])
    assert radar["id"] in str(err.value)
    assert len(_jobs(ai_on)) == before_jobs
    assert len(_runs_dirs(ai_on)) == before_runs


def test_missing_and_deleted_material_rejected(ai_on) -> None:
    ids = _seed_materials(ai_on, 2)
    with pytest.raises(agent_service.AgentServiceError, match="不存在或已删"):
        agent_service.build_run("synthesis_v1", [ids[0], "nope00000000"])
    ms.delete_material(ids[1])
    with pytest.raises(agent_service.AgentServiceError, match="不存在或已删"):
        agent_service.build_run("synthesis_v1", [ids[0], ids[1]])
    assert not _runs_dirs(ai_on)


# ── F76-3：采纳 derived 血统与 license（最严者胜两组）─────────────────


def _run_synthesis(ai_on, ids, reply="## 共同点\n综合稿正文"):
    runner = _FakeRunner()
    out = agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=_scripted_chat(reply))
    runner.fn("j")
    assert agent_service.read_run(out["run_id"])["status"] == "done"
    return out["run_id"]


def test_adopt_material_lineage_and_uri(ai_on) -> None:
    ids = _seed_materials(ai_on, 2)
    run_id = _run_synthesis(ai_on, ids)
    out = agent_service.adopt(run_id, target="material", title="我的综合")
    derived = ms.get_material(out["id"])
    assert derived["type"] == "derived"
    assert derived["origin"]["title"] == "我的综合"
    assert derived["provenance"]["chain"] == [
        {"from_id": ids[0], "op": "synthesize"},
        {"from_id": ids[1], "op": "synthesize"},
    ]
    assert derived["provenance"]["derived_from"] == []
    assert derived["origin"]["uri"].startswith(f"synthesis://{run_id}/")
    assert derived["origin"]["source_id"] == "synthesis"
    assert derived["payload"]["text"].startswith("## 共同点")
    # license_note 是**各源声明列表**（形状恒定为列表，单源亦然）
    assert derived["payload"]["meta"]["license_note"] == ["note-1", "note-2"]
    assert derived["payload"]["meta"]["material_ids"] == ids
    # 血统引用状态：两源均未删
    detail = ms.get_detail(out["id"])
    assert [r["from_id"] for r in detail["references"]] == ids
    assert all(r["deleted"] is False for r in detail["references"])


def test_license_strictest_wins_ccby_plus_allrights(ai_on) -> None:
    """CC-BY + ALL-RIGHTS → ALL-RIGHTS（有一个 ALL-RIGHTS 即 ALL-RIGHTS）。"""
    a = _seed_materials(ai_on, 1, license="CC-BY")[0]
    # 第二条用独立 uri（同 n 同 uri 会被幂等合并）
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://example.test/allrights",
        title="版权保留素材", license="ALL-RIGHTS",
        payload={"text": "有版权正文" * 40, "meta": {"license_note": "版权所有"}},
        fetched_at="2026-11-17T10:00:00",
    )
    b = ms.add_material(row)["id"]
    run_id = _run_synthesis(ai_on, [a, b])
    out = agent_service.adopt(run_id, target="material")
    assert ms.get_material(out["id"])["license"] == "ALL-RIGHTS"


def test_license_strictest_wins_ccby_plus_unknown(ai_on) -> None:
    """CC-BY + UNKNOWN → UNKNOWN（无 ALL-RIGHTS 时有 UNKNOWN 即 UNKNOWN）。"""
    a = _seed_materials(ai_on, 1, license="CC-BY")[0]
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://example.test/unknown",
        title="未知许可素材", license="UNKNOWN",
        payload={"text": "未知许可正文" * 40},
        fetched_at="2026-11-17T10:01:00",
    )
    b = ms.add_material(row)["id"]
    run_id = _run_synthesis(ai_on, [a, b])
    out = agent_service.adopt(run_id, target="material")
    assert ms.get_material(out["id"])["license"] == "UNKNOWN"


def test_license_all_ccby_stays_ccby(ai_on) -> None:
    """全 CC-BY 才 CC-BY。"""
    a = _seed_materials(ai_on, 1, license="CC-BY")[0]
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://example.test/ccby2",
        title="CC-BY 素材二", license="CC-BY",
        payload={"text": "CC-BY 正文" * 40, "meta": {"license_note": "CC-BY 4.0"}},
        fetched_at="2026-11-17T10:02:00",
    )
    b = ms.add_material(row)["id"]
    run_id = _run_synthesis(ai_on, [a, b])
    out = agent_service.adopt(run_id, target="material")
    assert ms.get_material(out["id"])["license"] == "CC-BY"


def test_strictest_license_pure_matrix() -> None:
    """纯函数矩阵（含零源与脏值）。"""
    assert agent_service.strictest_license([{"license": "ALL-RIGHTS"}]) == "ALL-RIGHTS"
    assert agent_service.strictest_license([{"license": "CC-BY"}, {"license": "UNKNOWN"}]) == "UNKNOWN"
    assert agent_service.strictest_license([{"license": "CC-BY"}]) == "CC-BY"
    assert agent_service.strictest_license([]) == "UNKNOWN"
    assert agent_service.strictest_license([{"license": "BSD-3"}]) == "UNKNOWN"


# ── F76-4：compose 桥（块锚齐全、sources 恒空）────────────────────────


def test_adopt_compose_seeds_draft_and_keeps_sources_empty(ai_on, tmp_path) -> None:
    ids = _seed_materials(ai_on, 2)
    run_id = _run_synthesis(ai_on, ids, reply="# 标题\n\n第一段\n\n第二段\n")
    root = tmp_path / "articles"
    root.mkdir()
    out = agent_service.adopt(run_id, target="compose", root=str(root))
    assert out["ok"] is True and out["slug"].startswith("synthesis-")
    d = compose_service.session_dir(out["slug"], root=str(root))
    draft = (d / compose_service.DRAFT_FILE).read_text(encoding="utf-8")
    # 块锚齐全（每块前置 <!--b:id-->）
    from musespiral.core.composition import blocks_from_markdown
    blocks = blocks_from_markdown(draft)
    assert len(blocks) >= 2
    assert all(b.id for b in blocks)
    for b in blocks:
        assert f"<!--b:{b.id}-->" in draft
    meta = json.loads((d / compose_service.META_FILE).read_text(encoding="utf-8"))
    assert meta["sources"] == []  # **不自动挂 sources**（设计 §二-F76.5）
    assert meta["status"] == compose_service.STATUS_COMPOSING


def test_adopt_compose_twice_business_error(ai_on, tmp_path) -> None:
    ids = _seed_materials(ai_on, 2)
    run_id = _run_synthesis(ai_on, ids, reply="# 标题\n\n正文段\n")
    root = tmp_path / "articles"
    root.mkdir()
    agent_service.adopt(run_id, target="compose", root=str(root))
    with pytest.raises(agent_service.AgentServiceError, match="会话已存在"):
        agent_service.adopt(run_id, target="compose", root=str(root))


# ── F76-5：采纳前零写（materials / 正稿 / compose draft.md 三处字节级）──


def test_no_writes_before_adopt(ai_on, tmp_path) -> None:
    """run done 后未 adopt → 设计 §七红线三处**字节级**不变：

    ① materials.jsonl（素材库）；② 正稿（root 下既有 *.md）；③ 既有 compose
    会话的 draft.md。run 目录与 jobs 的产物不在这三面内（非「外部写面」）。
    """
    ids = _seed_materials(ai_on, 2)
    materials_path = ai_on / "materials" / "materials.jsonl"
    before_materials = materials_path.read_bytes()

    root = tmp_path / "articles"
    root.mkdir()
    # ② 既有正稿（文章队列根下的 .md——run 不得触碰）
    article = root / "既有文章.md"
    article.write_text("---\ntitle: 既有文章\nstatus: draft\n---\n\n既有正文，字节快照对象。\n",
                       encoding="utf-8")
    before_article = article.read_bytes()
    # ③ 既有 compose 会话 + 其 draft.md（字节级而非「目录名集合」级）
    compose_service.new_session("preexisting", "既有会话", root=str(root))
    draft = (compose_service.session_dir("preexisting", root=str(root))
             / compose_service.DRAFT_FILE)
    draft.write_text("<!--b:pre-1-->\n既有草稿正文，字节快照对象。\n", encoding="utf-8")
    before_draft = draft.read_bytes()
    before_root_files = {p.name for p in root.iterdir()}

    run_id = _run_synthesis(ai_on, ids)
    # run 目录与 jobs 有产物，但两者都不是「外部写面」
    assert materials_path.read_bytes() == before_materials, "run 执行不得改动素材库一个字节"
    assert article.read_bytes() == before_article, "run 执行不得改动正稿一个字节"
    assert draft.read_bytes() == before_draft, "run 执行不得改动既有 compose 会话的 draft.md"
    assert {p.name for p in root.iterdir()} == before_root_files, "run 执行不得建新文件/会话"
    # 采纳后才变（material 目标改素材库；compose 目标只会新建会话不动既有）
    agent_service.adopt(run_id, target="material")
    assert materials_path.read_bytes() != before_materials
    agent_service.adopt(run_id, target="compose", root=str(root))
    assert draft.read_bytes() == before_draft, "采纳 compose 也不得改写既有会话的 draft.md"


def test_run_materials_bytes_unchanged_across_whole_run(ai_on) -> None:
    """更严的零写断言：整个 run（含 3 步 chat）期间全部素材行内容逐字节相同。"""
    ids = _seed_materials(ai_on, 2)
    rows_before = [json.loads(x) for x in
                   (ai_on / "materials" / "materials.jsonl").read_text(encoding="utf-8").splitlines()
                   if x.strip()]
    _run_synthesis(ai_on, ids)
    rows_after = [json.loads(x) for x in
                  (ai_on / "materials" / "materials.jsonl").read_text(encoding="utf-8").splitlines()
                  if x.strip()]
    assert rows_before == rows_after


# ── F76-6：API 契约（400/200{ok:false}/404/幂等）─────────────────────


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        from musespiral.studio.actions import _reset_runner
        _reset_runner()


def _request(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
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


def _wait_job(server, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _code, job = _request(server, "GET", f"/api/jobs/{job_id}")
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.2)
    return job


def _patch_chat(monkeypatch, reply: str = "## 共同点\n综合稿"):
    """把 gateway 的真实网络出口换掉（真 job 路径不注入 chat_fn 参数）。"""
    import musespiral.infra.ai.gateway as gw

    def _fake(messages, *, task=None, temperature=0.6, timeout=120, profile=None):
        return f"{reply}｜{task}"

    monkeypatch.setattr(gw, "_real_chat", _fake)


def test_api_run_validation_400_and_business_200(server, ai_on, monkeypatch) -> None:
    httpd, root = server
    ids = _seed_materials(ai_on, 2)
    _patch_chat(monkeypatch)
    # 缺 confirm → 400 全局闸门
    code, body = _request(httpd, "POST", "/api/agent/run",
                          {"plan": "synthesis_v1", "material_ids": ids})
    assert code == 400 and "confirm" in body["error"]
    # 篇数 1 → 400
    code, body = _request(httpd, "POST", "/api/agent/run",
                          {"plan": "synthesis_v1", "material_ids": ids[:1], "confirm": True})
    assert code == 400 and "2-8" in body["error"]
    # plan 枚举外 → 400
    code, _ = _request(httpd, "POST", "/api/agent/run",
                       {"plan": "nope", "material_ids": ids, "confirm": True})
    assert code == 400
    # material_ids 非 list[str] → 400
    code, _ = _request(httpd, "POST", "/api/agent/run",
                       {"plan": "synthesis_v1", "material_ids": "x", "confirm": True})
    assert code == 400
    # AI 未配置 → 200 {ok:false}
    (ai_on / "ai.json").unlink()
    code, body = _request(httpd, "POST", "/api/agent/run",
                          {"plan": "synthesis_v1", "material_ids": ids, "confirm": True})
    assert code == 200 and body["ok"] is False and "AI 未配置" in body["error"]


def test_api_run_then_adopt_full_path(server, ai_on, monkeypatch) -> None:
    httpd, root = server
    ids = _seed_materials(ai_on, 2)
    _patch_chat(monkeypatch)
    code, out = _request(httpd, "POST", "/api/agent/run",
                        {"plan": "synthesis_v1", "material_ids": ids, "confirm": True})
    assert code == 200 and out["ok"] is True and out["job_id"] and out["run_id"]
    job = _wait_job(httpd, out["job_id"])
    assert job["status"] == "done"
    result = job["result"]
    assert set(result) == {"run_id", "status", "steps_done", "chars_out"}
    # runs 清单 & 详情
    code, listing = _request(httpd, "GET", "/api/agent/runs")
    assert code == 200 and listing["runs"][0]["id"] == out["run_id"]
    code, detail = _request(httpd, "GET", f"/api/agent/runs?id={out['run_id']}")
    assert code == 200 and detail["ok"] is True and len(detail["run"]["steps"]) == 3
    # 404 / 400
    assert _request(httpd, "GET", "/api/agent/runs?id=missing")[0] == 404
    assert _request(httpd, "GET", "/api/agent/runs?id=")[0] == 400
    # adopt material：403? 否——缺 confirm → 400
    code, _ = _request(httpd, "POST", "/api/agent/adopt",
                       {"run_id": out["run_id"], "target": "material"})
    assert code == 400
    # 正常采纳 + 幂等
    code, first = _request(httpd, "POST", "/api/agent/adopt",
                           {"run_id": out["run_id"], "target": "material", "confirm": True})
    assert code == 200 and first["ok"] is True
    rows_before = len(ms.list_materials())
    code, second = _request(httpd, "POST", "/api/agent/adopt",
                            {"run_id": out["run_id"], "target": "material", "confirm": True})
    assert code == 200 and second["id"] == first["id"]
    assert len(ms.list_materials()) == rows_before  # 零新增行
    # target 枚举外 → 400
    code, _ = _request(httpd, "POST", "/api/agent/adopt",
                       {"run_id": out["run_id"], "target": "doc", "confirm": True})
    assert code == 400


def test_api_adopt_undone_run_business_error(server, ai_on, monkeypatch) -> None:
    """run 未完成（running/budget_stopped）→ 200 {ok:false}（不是 400）。"""
    httpd, root = server
    ids = _seed_materials(ai_on, 2)
    built = agent_service.build_run("synthesis_v1", ids)
    run_id = agent_service._make_run_id("synthesis_v1")
    directory = agent_service.run_dir(run_id)
    directory.mkdir(parents=True)
    # 手工落一个 running run.json（不跑完）
    (directory / "run.json").write_text(json.dumps(
        agent_service._run_meta(run_id, built["plan"], ids,
                                status="running", started="2026-11-17T09:00:00")),
        encoding="utf-8")
    code, body = _request(httpd, "POST", "/api/agent/adopt",
                          {"run_id": run_id, "target": "material", "confirm": True})
    assert code == 200 and body["ok"] is False and "不可采纳" in body["error"]
    # 不存在 → 200 {ok:false}（业务拒绝）
    code, body = _request(httpd, "POST", "/api/agent/adopt",
                          {"run_id": "nope-run", "target": "material", "confirm": True})
    assert code == 200 and body["ok"] is False and "run 不存在" in body["error"]


# ── prune 与 interrupted 读侧标记 ────────────────────────────────────


def test_prune_keeps_lru(ai_on) -> None:
    ids = _seed_materials(ai_on, 2)
    for _ in range(4):
        runner = _FakeRunner()
        out = agent_service.submit_to_runner(runner, "synthesis_v1", ids,
                                             chat_fn=_scripted_chat())
        runner.fn("j")
    assert len(_runs_dirs(ai_on)) == 4
    result = agent_service.prune(keep=2)
    assert result["removed"] == 2 and result["kept"] == 2
    assert len(_runs_dirs(ai_on)) == 2


def test_interrupted_marking_on_read(ai_on) -> None:
    """run.json 仍 running 但对应 job 已 failed(interrupted) → 读侧标 interrupted。"""
    ids = _seed_materials(ai_on, 2)
    built = agent_service.build_run("synthesis_v1", ids)
    run_id = agent_service._make_run_id("synthesis_v1")
    directory = agent_service.run_dir(run_id)
    directory.mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps(
        agent_service._run_meta(run_id, built["plan"], ids,
                                status="running", started="2026-11-17T09:00:00")),
        encoding="utf-8")
    from musespiral.jobs.store import JobStore
    store = JobStore()
    store.create("agent_run", {"run_id": run_id})
    jobs = store.list(limit=None)
    store.update(jobs[0]["id"], status="failed",
                 error="interrupted（进程重启，任务中断；可在任务面板重发）")
    detail = agent_service.read_run(run_id)
    assert detail["status"] == agent_service.STATUS_INTERRUPTED
    rows = agent_service.list_runs()
    assert rows[0]["status"] == agent_service.STATUS_INTERRUPTED


# ── CLI 三路径（plans / run / runs / adopt）──────────────────────────


def test_cli_run_runs_adopt_end_to_end(ai_on, monkeypatch) -> None:
    """`agent run` → `agent runs <id>` → `agent adopt`：进度行带真实计数。"""
    from click.testing import CliRunner
    from musespiral.cli import cli

    _patch_chat(monkeypatch)
    ids = _seed_materials(ai_on, 2)
    runner = CliRunner()
    args = ["agent", "run", "synthesis_v1"]
    for mid in ids:
        args += ["--material", mid]
    res = runner.invoke(cli, args, prog_name="musespiral")
    assert res.exit_code == 0, res.output
    lines = res.output.splitlines()
    run_id = next(l.split(": ", 1)[1] for l in lines if l.startswith("run_id:"))
    # 步级进度行带真实计数（budget 键是 in_chars/out_chars，不是 chars_in/out）
    assert any("[1/3] extract_1" in l and "字" in l for l in lines)
    assert any("chars_out=30" in l or "chars_out=" in l for l in lines)
    detail = runner.invoke(cli, ["agent", "runs", run_id], prog_name="musespiral")
    assert detail.exit_code == 0 and "status: done" in detail.output
    listed = runner.invoke(cli, ["agent", "runs"], prog_name="musespiral")
    assert listed.exit_code == 0 and run_id in listed.output
    adopted = runner.invoke(cli, ["agent", "adopt", run_id, "--target", "material"],
                            prog_name="musespiral")
    assert adopted.exit_code == 0 and "已存为素材" in adopted.output


def test_cli_run_construction_error_exits_1(ai_on) -> None:
    """构造期错误 → 退出码 1、可读消息、零 run 目录。"""
    from click.testing import CliRunner
    from musespiral.cli import cli

    ids = _seed_materials(ai_on, 1)
    res = CliRunner().invoke(
        cli, ["agent", "run", "synthesis_v1", "--material", ids[0]], prog_name="musespiral"
    )
    assert res.exit_code == 1 and "2-8" in res.output
    assert not _runs_dirs(ai_on)


def test_cli_runs_prune(ai_on) -> None:
    from click.testing import CliRunner
    from musespiral.cli import cli

    ids = _seed_materials(ai_on, 2)
    for _ in range(3):
        runner = _FakeRunner()
        out = agent_service.submit_to_runner(runner, "synthesis_v1", ids,
                                             chat_fn=_scripted_chat())
        runner.fn("j")
    res = CliRunner().invoke(cli, ["agent", "runs", "--prune", "--keep", "1"],
                             prog_name="musespiral")
    assert res.exit_code == 0 and "保留 1 个" in res.output
    assert len(_runs_dirs(ai_on)) == 1


# ── 复核修复批（2026-11-17）：run_id 形状门 / license 快照 / 覆盖级联 ────


def test_run_dir_rejects_traversal_ids(ai_on) -> None:
    """咽喉层：穿越/分隔符/绝对路径形态的 run_id 在 run_dir 即拒（可读错误）。"""
    for bad in ("../sneak", "../../etc", "/abs/path", "..", ".", "a/b", "a\\b",
                "..%2Fsneak", "x/../../y", "", None):
        with pytest.raises(agent_service.AgentServiceError, match="非法 run_id"):
            agent_service.run_dir(bad)  # type: ignore[arg-type]
    # 合法形态放行（本模块自产形态天然在白名单内）
    assert agent_service.run_dir("synthesis_v1-20261117-090000-ab12").name == \
        "synthesis_v1-20261117-090000-ab12"


def test_read_run_traversal_returns_none_no_echo(ai_on) -> None:
    """读侧兜底：home 下真摆着同名 run.json（如复制的 ai.json），穿越 id 也
    读不出一个字节——密钥零回显红线不可经 run 详情面破出。"""
    (ai_on / "sneak").mkdir()
    (ai_on / "sneak" / "run.json").write_text(
        json.dumps({"profiles": {"t": {"api_key": "sk-CANARY-123"}}}), encoding="utf-8"
    )
    for bad in ("../sneak", "../../sneak", "sneak/../.."):
        assert agent_service.read_run(bad) is None


def test_adopt_traversal_rejected_zero_write(ai_on, tmp_path) -> None:
    """采纳面：home 外伪 run 目录（穿越 id 指向）不可被采纳为 derived 素材。"""
    outside = ai_on.parent / "OUTSIDE-fake"
    outside.mkdir()
    (outside / "run.json").write_text(json.dumps(
        {"id": "fake", "plan": "synthesis_v1", "status": "done",
         "inputs": {"material_ids": []}, "steps": []}), encoding="utf-8")
    (outside / "steps").mkdir()
    (outside / "steps" / "1.json").write_text(json.dumps(
        {"id": "synthesize", "task": "agent_synthesize", "status": "done",
         "out_text": "外部伪造综合稿"}), encoding="utf-8")
    materials_path = ai_on / "materials" / "materials.jsonl"
    materials_path.parent.mkdir(parents=True, exist_ok=True)
    materials_path.write_text("", encoding="utf-8")
    with pytest.raises(agent_service.AgentServiceError):
        agent_service.adopt("../../OUTSIDE-fake", target="material")
    assert materials_path.read_bytes() == b"", "穿越采纳不得落库一个字节"


def test_agent_runs_id_traversal_400(server) -> None:
    """API 面：GET /api/agent/runs?id=../sneak → 400（形状门，契约 §3.6-4）。"""
    httpd, _root = server
    for bad in ("../sneak", "../../sneak", "/etc/passwd"):
        code, body = _request(httpd, "GET", f"/api/agent/runs?id={bad}")
        assert code == 400, f"{bad!r} 应 400，实际 {code}"
        assert b"sk-" not in (json.dumps(body).encode() if isinstance(body, dict) else body)


def test_license_uses_run_snapshot_after_source_deleted(ai_on) -> None:
    """license 存证规则：run 后删除 ALL-RIGHTS 源，采纳仍按 run.json 快照取
    最严（删源不得让综合稿许可变宽松——复核实测的回归锚）。"""
    a = _seed_materials(ai_on, 1, license="CC-BY")[0]
    row = model.make_material(
        mtype="crawled", source_id="manual", uri="https://example.test/strict",
        title="版权保留素材", license="ALL-RIGHTS",
        payload={"text": "版权正文" * 40, "meta": {"license_note": "版权所有"}},
        fetched_at="2026-11-17T11:00:00",
    )
    b = ms.add_material(row)["id"]
    run_id = _run_synthesis(ai_on, [a, b])
    # run.json 落盘了构造期许可快照（存证形状）
    meta = json.loads(
        (ai_on / "agent_runs" / run_id / "run.json").read_text(encoding="utf-8"))
    assert [r["license"] for r in meta["inputs"]["licenses"]] == ["CC-BY", "ALL-RIGHTS"]
    # 删掉最严源再采纳 → 仍 ALL-RIGHTS；license_note 仍是两源快照（选材序）
    ms.delete_material(b)
    out = agent_service.adopt(run_id, target="material")
    derived = ms.get_material(out["id"])
    assert derived["license"] == "ALL-RIGHTS", "删源不得放松综合稿许可"
    assert derived["payload"]["meta"]["license_note"] == ["note-1", "版权所有"]
    # chain 仍保留被删源 id（存证全集）
    assert [c["from_id"] for c in derived["provenance"]["chain"]] == [a, b]


def test_adopt_licenses_legacy_run_without_snapshot(ai_on) -> None:
    """防御路径：run.json 无 licenses 快照（手改/legacy）→ 按 id 全集重建，
    已删源按 UNKNOWN 参与最严判定（不因快照缺席而变宽松）。"""
    ids = _seed_materials(ai_on, 2)  # CC-BY / UNKNOWN
    run_id = _run_synthesis(ai_on, ids)
    run_path = ai_on / "agent_runs" / run_id / "run.json"
    meta = json.loads(run_path.read_text(encoding="utf-8"))
    del meta["inputs"]["licenses"]
    run_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    ms.delete_material(ids[1])  # 删掉 UNKNOWN 源 → 重建行取 UNKNOWN
    out = agent_service.adopt(run_id, target="material")
    assert ms.get_material(out["id"])["license"] == "UNKNOWN"


def test_agent_prompt_override_cascade_applied(ai_on, monkeypatch) -> None:
    """F47 覆盖级联对 agent 步生效：prompts.d/<task>.txt 存在即整体覆盖该步
    内置模板（此前字面量入 step.inputs，覆盖文件零作用——复核实测回归锚）。"""
    from musespiral.infra.ai.prompts import save_override
    from musespiral.core.agent import plans_builtin

    ids = _seed_materials(ai_on, 2)
    save_override("agent_extract", "覆盖后的抽取指令：输出覆盖标记行。")
    built = agent_service.build_run("synthesis_v1", ids)
    extract = built["plan"]["steps"][0]["inputs"]["instruction"]
    synthesize = built["plan"]["steps"][-1]["inputs"]["instruction"]
    assert extract == "覆盖后的抽取指令：输出覆盖标记行。"
    assert synthesize == plans_builtin.SYNTHESIZE_INSTRUCTION, "未覆盖任务保持内置"
    assert built["plan"]["steps"][0]["task"] == "agent_extract"
    # 端到端：chat 收到的 prompt 确含覆盖文本（级联真实生效，非仅数据面）
    seen: list[str] = []
    calls = _scripted_chat()
    original = calls

    def _spy(messages, **kw):
        seen.append(messages[0]["content"])
        return original(messages, **kw)

    runner = _FakeRunner()
    agent_service.submit_to_runner(runner, "synthesis_v1", ids, chat_fn=_spy)
    runner.fn("j")
    assert any("覆盖后的抽取指令" in p for p in seen), "覆盖文本未进实际 prompt"
    # 覆盖文本变更也使缓存 key 变（gateway template 参吃的是生效模板）
    templates = agent_service.step_templates(built["plan"])
    assert templates["agent_extract"] == "覆盖后的抽取指令：输出覆盖标记行。"
    assert templates["agent_synthesize"] == plans_builtin.SYNTHESIZE_INSTRUCTION
