"""选题雷达测试（Phase D §五 F78 行，6 用例 + CLI 金路径）。

覆盖：F78-1 聚合确定性（candidates() 两次 json.dumps 逐字节相等）；
F78-2 已写过排除边界（相似度 0.60 含等号剔除+excluded 携带 matched_title/
similarity，0.59 保留）；F78-3 hub down 降级（radar 腿空+down_sources 注记，
KB/star 腿照跑、快照仍产出、materials 零回写）；F78-4 排序并列确定性
（同分跨 kind 按 kb<radar<star、同分同 kind 按 title 字典序）；F78-5 AI
增强单项失败跳过（第 2 项 AIError → 其余 4 项保留 ai_angle、ai_enhanced_n=4、
job 仍 done 且 result 只含计数）；F78-6 快照史（21 次→恰 20 份最旧删+
latest=最新）与 GET /api/topic_radar 空态 snapshot:null。
MUSESPIRAL_HOME monkeypatch 到 tmp 隔离；AI 假件经 chat_fn 注入（gateway 设计
注入点，不 mock HTTP）。相似度边界串取值经 SequenceMatcher 实测锚定
（"abcde"vs"abcfg"=0.6；len200 共前缀 59=0.59）。
"""
from __future__ import annotations

import difflib
import http.client
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral import studio
from musespiral.cli import cli
from musespiral.core.discovery import topic_radar
from musespiral.infra.ai import AIError
from musespiral.infra.sources import registry
from musespiral.jobs.runner import JobRunner
from musespiral.services import sources_service
from musespiral.services import topic_radar_service as svc

# 相似度边界串（模块级实测锚：写入前先在本文件 docstring 声明，用例内再断言）
_PUB_60 = "abcfg"
_CAND_60 = "abcde"
_PUB_59 = "a" * 59 + "c" * 41
_CAND_59 = "a" * 59 + "b" * 41


@pytest.fixture
def env(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "musespiral-home"
    home.mkdir(parents=True)
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def ai_on(env: Path) -> Path:
    """配置一个本地无 key 档案（discover_suggest 未配路由→走 default）。"""
    (env / "ai.json").write_text(
        json.dumps({
            "default": "local",
            "profiles": {"local": {"base_url": "http://127.0.0.1:9/v1", "api_key": "", "model": "mock"}},
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


# ── F78-1 聚合确定性（单元）────────────────────────────────────────


def test_f78_1_aggregation_deterministic_bytes() -> None:
    """固定三源输入 → candidates() 两次输出 json.dumps 逐字节相等。"""
    radar = [
        {"material_id": "r1", "title": "Rust 流处理引擎", "score": 90, "grade": "S", "url": "https://x/1"},
        {"material_id": "r2", "title": "向量数据库选型", "score": 70, "grade": "A", "url": ""},
    ]
    kb = [
        {"path": "/kb/a.md", "title": "向量数据库选型笔记", "score": 85},
        {"path": "/kb/b.md", "title": "RTOS 调度器解读", "score": 60},
    ]
    stars = [{"material_id": "s1", "title": "Rust 流处理引擎", "url": "https://x/1"}]
    published = ["已发：数据管道实战"]
    first = topic_radar.candidates(radar, kb, stars, published)
    second = topic_radar.candidates(radar, kb, stars, published)
    b1 = json.dumps(first, ensure_ascii=False).encode("utf-8")
    b2 = json.dumps(second, ensure_ascii=False).encode("utf-8")
    assert b1 == b2  # 逐字节一致（确定性验收线）
    # 形状 sanity：候选行五键齐全；excluded 行（如有）在尾部
    for row in first:
        assert set(row) <= {"kind", "title", "refs", "score", "reasons", "excluded", "ai_angle"}
        assert {"kind", "title", "refs", "score", "reasons"} <= set(row)
    kept_kinds = [r["kind"] for r in first if not r.get("excluded")]
    assert kept_kinds == ["radar", "kb", "radar", "kb", "star"]  # 90r / 85kb / 70r / 60kb / 40star
    tail = [r for r in first if r.get("excluded")]
    if tail:
        assert first[-len(tail):] == tail  # 被剔行（如有）整体在尾部


# ── F78-2 已写过排除边界（单元）────────────────────────────────────


def test_f78_2_exclude_boundary_inclusive() -> None:
    """相似度 0.60（含等号）→剔除进 excluded（含 matched_title+similarity）；
    0.59→保留。边界串比值先实测锚定再断言行为。"""
    # 前置锚：边界串的 SequenceMatcher 比值恰为 0.6 / 0.59（浮点等值实测）
    assert difflib.SequenceMatcher(None, _CAND_60, _PUB_60).ratio() == 0.6
    assert difflib.SequenceMatcher(None, _CAND_59, _PUB_59).ratio() == 0.59

    kb = [
        {"path": "/kb/exact.md", "title": _CAND_60, "score": 80},
        {"path": "/kb/keep.md", "title": _CAND_59, "score": 80},
    ]
    rows = topic_radar.candidates([], kb, [], [_PUB_60, _PUB_59])
    excluded = [r for r in rows if r.get("excluded")]
    kept = [r for r in rows if not r.get("excluded")]
    assert [r["title"] for r in excluded] == [_CAND_60]  # 0.60 含等号 → 剔除
    assert excluded[0]["excluded"] == {"matched_title": _PUB_60, "similarity": 0.6}
    assert [r["title"] for r in kept] == [_CAND_59]  # 0.59 → 保留
    assert "excluded" not in kept[0]
    # 被剔行仍在 candidates 数组内（§1.4 形状：excluded 是行上可选键）
    assert len(rows) == 2


# ── F78-3 hub down 降级（集成）─────────────────────────────────────


def test_f78_3_hub_down_degraded_snapshot(env: Path, tmp_path: Path, monkeypatch) -> None:
    """radar_signal 空 + hub down 注记 → KB/star 腿照跑、快照仍产出
    （inputs.down_sources 非空）；快照零回写 materials（红线）。"""
    from musespiral.services import materials_service as ms
    from musespiral.core.materials import model

    sources_service.register_source(registry.default_radarhub_source())
    sources_service.mark_health("radarhub", "down")  # hub 宕机（快照回写，非本线探测）

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "fresh.md").write_text(
        "---\ntitle: RTOS 调度器解读\ncategory: 13-source-analysis\n"
        "created: 2026-11-10\ntags: [源码分析, C, RTOS, owner/repo]\n---\n\n"
        "## 架构总览\n正文一百行以上。\n" + "内容行。\n" * 120,
        encoding="utf-8",
    )
    (env / "kb_root.txt").write_text(str(kb_dir), encoding="utf-8")

    star = model.make_material(
        mtype="crawled", source_id="manual", uri="https://x/star",
        title="Rust 流处理引擎", payload={"text": "正文", "meta": {"url": "https://x/star"}},
        triage={"read": False, "starred": True, "enabled": True, "tags": []},
        fetched_at="2026-11-16T09:00:00",
    )
    ms.add_material(star)

    materials_file = ms.materials_path()
    before = materials_file.read_bytes()
    now = "2026-11-17T10:30:00"
    snapshot = svc.run_snapshot(use_ai=False, now=now)
    after = materials_file.read_bytes()
    assert before == after  # 快照零回写 materials

    kinds = {c["kind"] for c in snapshot["candidates"]}
    assert kinds == {"kb", "star"}  # radar 腿空（无 radar_signal 素材），另两腿照跑
    assert snapshot["inputs"]["down_sources"] == ["radarhub"]  # 灰灯注记在案
    assert snapshot["inputs"]["radar_n"] == 0 and snapshot["inputs"]["kb_n"] >= 1
    assert snapshot["generated_at"] == now
    assert svc.latest() == snapshot  # latest.json 已落（读面恒指此）

    # 同输入两次快照 → candidates 逐字节一致（F78 验收行；generated_at 相同
    # → 历史文件同名覆写同字节）
    again = svc.run_snapshot(use_ai=False, now=now)
    assert json.dumps(again["candidates"], ensure_ascii=False) == json.dumps(
        snapshot["candidates"], ensure_ascii=False
    )


# ── F78-4 排序并列确定性（单元）────────────────────────────────────


def test_f78_4_sort_ties_deterministic() -> None:
    """同分不同 kind→kind 字典序（kb<radar<star）；同分同 kind→title 字典序。"""
    radar = [
        {"material_id": "r2", "title": "beta 信号", "score": 70, "grade": "A", "url": ""},
        {"material_id": "r1", "title": "alpha 信号", "score": 70, "grade": "A", "url": ""},
    ]
    kb = [{"path": "/kb/z.md", "title": "zzz", "score": 70}, {"path": "/kb/a.md", "title": "aaa", "score": 70}]
    stars = [{"material_id": "s1", "title": "星标题", "url": ""}]
    rows = topic_radar.candidates(radar, kb, stars, [])
    assert [(r["kind"], r["title"]) for r in rows] == [
        ("kb", "aaa"), ("kb", "zzz"),  # 同分同 kind → title 字典序
        ("radar", "alpha 信号"), ("radar", "beta 信号"),
        ("star", "星标题"),            # 同分跨 kind → kb < radar < star
    ]


# ── F78-5 AI 增强失败跳过（集成）───────────────────────────────────


def test_f78_5_ai_failure_skip_job_done(env: Path, ai_on: Path) -> None:
    """top5 第 2 项 gateway 抛 AIError → 其余 4 项增强保留、ai_enhanced_n=4、
    job 仍 done；result 只含计数（snapshot_path/candidates_n/ai_enhanced_n）。"""
    from musespiral.services import materials_service as ms
    from musespiral.core.materials import model

    scores = [95, 90, 85, 80, 75]
    for i, score in enumerate(scores, 1):
        ms.add_material(model.make_material(
            mtype="radar_signal", source_id="radarhub", uri=f"https://github.com/x/r{i}",
            title=f"雷达素材{i}号",
            payload={"meta": {"grade": "A", "score": score, "url": f"https://github.com/x/r{i}"}},
            fetched_at="2026-11-16T09:00:00",
        ))

    calls: list[str] = []

    def fake_chat(messages, *, task, temperature, timeout, profile):
        calls.append(task)
        if len(calls) == 2:
            raise AIError("连接失败 @ 第 2 项（脚本化假件）")
        return "角度：从工程取舍切入写一篇对比稿"

    runner = JobRunner(max_workers=1)
    try:
        job_id = svc.submit_to_runner(runner, use_ai=True, chat_fn=fake_chat)
        deadline = time.monotonic() + 30
        while True:
            job = runner.get(job_id) or {}
            if job.get("status") in ("done", "failed"):
                break
            assert time.monotonic() < deadline, "job 轮询超时"
            time.sleep(0.05)
    finally:
        runner.shutdown(wait=True)

    assert job["status"] == "done"  # 单项失败不中断，job 仍 done
    assert set(calls) == {"discover_suggest"} and len(calls) == 5  # top5 逐项尝试
    result = job["result"]
    assert set(result) == {"snapshot_path", "candidates_n", "ai_enhanced_n"}  # 计数无正文
    assert result["candidates_n"] == 5 and result["ai_enhanced_n"] == 4

    snapshot = svc.latest()
    assert snapshot["ai_enhanced_n"] == 4
    kept = [c for c in snapshot["candidates"] if not c.get("excluded")]
    assert [c["score"] for c in kept] == scores  # score 降序
    assert "ai_angle" not in kept[1]  # 第 2 项失败 → 该行缺省
    assert [c.get("ai_angle") for i, c in enumerate(kept) if i != 1] == ["角度：从工程取舍切入写一篇对比稿"] * 4


# ── F78-6 快照史与读面（集成）──────────────────────────────────────


def test_f78_6_history_cap_and_empty_state_api(env: Path, server) -> None:
    """连续 21 次快照→历史恰 20 份（最旧删除）+latest=最新；
    GET /api/topic_radar 无快照→{ok:true, snapshot:null}，有快照→回其内容。"""
    base = datetime(2026, 11, 17, 8, 0, 0)
    for i in range(21):
        svc.run_snapshot(use_ai=False, now=(base + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%S"))
    root = svc.snapshot_root()
    history = sorted(p.name for p in root.glob("*.json") if p.name != svc.LATEST_FILE)
    assert len(history) == 20  # 超限删最旧
    assert history[0] == "20261117-080100.json"  # 第 1 份（08:00）已删，最旧=08:01
    latest = svc.latest()
    assert latest is not None and latest["generated_at"] == "2026-11-17T08:20:00"  # latest=最新

    # 读面（有快照）：GET /api/topic_radar 回 latest 内容；空态另见
    # test_get_topic_radar_empty_state。
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.request("GET", "/api/topic_radar")
    resp = json.loads(conn.getresponse().read().decode("utf-8"))
    conn.close()
    assert resp["ok"] is True
    assert resp["snapshot"]["generated_at"] == "2026-11-17T08:20:00"


def test_get_topic_radar_empty_state(env: Path, server) -> None:
    """GET /api/topic_radar 无快照 → 200 {ok:true, snapshot:null} 空态（不 404 不 500）。"""
    assert svc.latest() is None
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    conn.request("GET", "/api/topic_radar")
    raw = conn.getresponse()
    body = json.loads(raw.read().decode("utf-8"))
    conn.close()
    assert raw.status == 200
    assert body == {"ok": True, "snapshot": None}


# ── CLI 金路径（F78.1 M0 交互流；横切面：topic_radar kind 四态走通）──


def test_cli_topic_radar_golden_path(env: Path, tmp_path: Path) -> None:
    """无快照首跑自动重算（建 job→done→候选表+排除注记）；二次无 --refresh
    直读 latest 不再建 job；--refresh 强制重算入史。"""
    from musespiral.core.materials import model
    from musespiral.jobs.store import JobStore
    from musespiral.services import materials_service as ms

    kb_dir = tmp_path / "kb"
    kb_dir.mkdir()
    (kb_dir / "fresh.md").write_text(
        "---\ntitle: 数据管道实战笔记\ncategory: 13-source-analysis\n"
        "created: 2026-11-10\ntags: [源码分析, Python, 数据管道, owner/repo]\n---\n\n"
        "## 架构总览\n正文。\n" + "内容行。\n" * 120,
        encoding="utf-8",
    )
    (env / "kb_root.txt").write_text(str(kb_dir), encoding="utf-8")
    ms.add_material(model.make_material(
        mtype="radar_signal", source_id="radarhub", uri="https://github.com/x/vec",
        title="向量数据库选型",
        payload={"meta": {"grade": "S", "score": 92, "url": "https://github.com/x/vec"}},
        fetched_at="2026-11-16T09:00:00",
    ))
    sources_service.register_source(registry.default_radarhub_source())
    sources_service.mark_health("radarhub", "down")

    runner = CliRunner()
    first = runner.invoke(cli, ["topic-radar", "--no-ai"])
    assert first.exit_code == 0, first.output
    assert "job_id:" in first.output  # 建 job（F78.1 步骤 1）
    assert "向量数据库选型" in first.output and "已写过排除" not in first.output
    assert "外部腿缺失: radarhub" in first.output  # 灰灯行（hub 宕机时）
    jobs = JobStore().list(limit=None)
    assert [j["kind"] for j in jobs] == ["topic_radar"] and jobs[0]["status"] == "done"
    assert set(jobs[0]["result"]) == {"snapshot_path", "candidates_n", "ai_enhanced_n"}

    second = runner.invoke(cli, ["topic-radar"])  # 无 --refresh：直读 latest，零新 job
    assert second.exit_code == 0, second.output
    assert "job_id:" not in second.output
    assert "向量数据库选型" in second.output
    assert len(JobStore().list(limit=None)) == 1  # 未建新 job

    third = runner.invoke(cli, ["topic-radar", "--refresh", "--no-ai"])
    assert third.exit_code == 0, third.output
    assert "job_id:" in third.output
    assert svc.latest() is not None  # --refresh 强制重算后 latest 已更新
    history = sorted(p.name for p in svc.snapshot_root().glob("*.json") if p.name != svc.LATEST_FILE)
    assert history  # 重算已入史（文件名秒级粒度，连续触发可同名覆写；
    #                 「21 次→恰 20 份最旧删」由 test_f78_6 注入时钟确定性覆盖）
