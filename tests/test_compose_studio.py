"""Studio /api/compose/* 契约与安全红线（第十八期 F48-F52；M2 接线，UI 归 M3）。

覆盖：GET 会话列表/详情 / 五动作 confirm 语义 / generate 走 job
（compose_generate，产物在 job.result）/ 业务失败 ok:false（不 500）/
adopt 正稿入队列且会话目录不污染 /api/articles / jobs 侧 compose_generate
kind 与孤儿恢复。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.jobs.store import JobStore, jobs_path
from musespiral.services import compose_service

REPLY_R1 = (
    "<<<block kind:heading>>># 缓存设计实录\n\n"
    "<<<block kind:para>>>正文段，综上所述空值缓存最稳妥。\n\n"
    "<<<block kind:para>>>结尾段。"
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


@pytest.fixture
def source(tmp_path: Path) -> Path:
    p = tmp_path / "kb" / "cache.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text("## 一、概述\n\n概述正文。", encoding="utf-8")
    return p


@pytest.fixture
def server(root: Path) -> studio.ThreadingHTTPServer:
    httpd = studio.make_server(root, port=0)
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
    payload = None
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
    else:
        conn.request(method, path)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, json.loads(data)


def _get(server, path: str):
    code, payload = _request(server, "GET", path)
    return code, payload


def _post(server, path: str, body: dict):
    code, payload = _request(server, "POST", path, body)
    return code, payload


def _wait_job(server, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, job = _get(server, f"/api/jobs/{job_id}")
        assert code == 200
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.02)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


# ── GET 端点契约 ─────────────────────────────────────────────────────


def test_compose_list_empty_then_populated(server, root: Path) -> None:
    code, payload = _get(server, "/api/compose")
    assert code == 200 and payload == {"sessions": []}
    compose_service.new_session("demo", "标题", root=root)
    code, payload = _get(server, "/api/compose")
    assert code == 200
    assert payload["sessions"] == [
        {
            "slug": "demo",
            "title": "标题",
            "status": "composing",
            # E-5 小说模式批（2026-11-19）：清单补 kind（前端徽标区分小说会话）
            "kind": "article",
            "sources": 0,
            "rounds": 0,
            "created": payload["sessions"][0]["created"],
        }
    ]


def test_full_flow_via_http(server, root: Path, source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 全链：new → add → generate(job) → decide → adopt → 队列可见。

    generate 走流式增量入口（E-4 流式骨架批：闭包恒传 on_delta → 服务层
    走 _chat_stream）——假件按块回调 on_delta，兼测 job 行的 partial/phase
    节流发布（轮询方在生成期即见骨架逐块长出）。
    """
    calls: list[list[dict]] = []

    def fake_stream(messages, *, task=None, temperature=0.6, on_delta=None, **kw):
        calls.append(messages)
        # 保留 \n\n 分隔的发射（真流式里定界换行随 delta 到达——丢了它们
        # partial 解析会并成一块）
        chunks = REPLY_R1.split("\n\n")
        emissions = [c + "\n\n" for c in chunks[:-1]] + chunks[-1:]
        for piece in emissions:
            if on_delta is not None:
                on_delta(piece)
        return REPLY_R1

    monkeypatch.setattr(compose_service, "_chat_stream", fake_stream)

    # 动作必须带 confirm:true（server 层人审闸门）
    code, payload = _post(server, "/api/compose/new", {"slug": "demo", "title": "T"})
    assert code == 400 and "confirm" in payload["error"]

    code, payload = _post(
        server, "/api/compose/new", {"slug": "demo", "title": "缓存三解法", "confirm": True}
    )
    assert code == 200 and payload["ok"] and payload["slug"] == "demo"

    # 素材：绝对路径入池（章节勾选）
    code, payload = _post(
        server,
        "/api/compose/add",
        {"slug": "demo", "path": str(source), "sections": ["一、概述"], "confirm": True},
    )
    assert code == 200 and payload["ok"]
    assert payload["known_sections"] == ["一、概述"]  # M3：章节勾选树数据面（动作层透传）
    code, payload = _post(
        server, "/api/compose/add", {"slug": "demo", "path": str(source), "sections": ["坏章"], "confirm": True}
    )
    assert code == 200 and payload["ok"] is False and "章节不存在" in payload["error"]

    # 清单与详情
    code, payload = _get(server, "/api/compose")
    assert code == 200 and payload["sessions"][0]["slug"] == "demo"
    code, payload = _get(server, "/api/compose/session?slug=demo")
    assert code == 200 and payload["status"] == "composing"
    assert payload["sources"][0]["sections"] == ["一、概述"]
    assert Path(payload["sources"][0]["path"]).name == "cache.md"  # 绝对路径不透出
    code, payload = _get(server, "/api/compose/session")
    assert code == 400  # 缺 slug

    # generate：长动作走 job（kind=compose_generate）
    # §四前端行验收点：AI 产出不经「采纳」绝不入 draft.md（字节级红线）
    draft_before = (root / "demo.compose" / "draft.md").read_bytes()
    code, payload = _post(server, "/api/compose/generate", {"slug": "demo", "confirm": True})
    assert code == 200 and payload["ok"] and payload["job_id"]
    job = _wait_job(server, payload["job_id"])
    assert job["status"] == "done" and job["kind"] == "compose_generate"
    assert job["result"]["round"] == 1
    ids = [b["id"] for b in job["result"]["blocks"]]
    assert ids == ["b1", "b2", "b3"]
    # E-4 流式骨架批：生成期 partial 已节流发布进 job 行（display 面
    # [{kind,text}] 无 id；终态 result 不受影响——行内并存零干扰）
    assert job.get("phase") == "生成中 3 块"
    assert [b["kind"] for b in job["partial"]] == ["heading", "para", "para"]
    assert all("id" not in b for b in job["partial"])
    assert (root / "demo.compose" / "draft.md").read_bytes() == draft_before  # generate 后不动
    assert calls and calls[0][0]["role"] == "user"  # chat 走 task=compose 的消息面

    # decide：三态
    code, payload = _post(
        server,
        "/api/compose/decide",
        {"slug": "demo", "round": 1, "block": "b1", "action": "keep", "confirm": True},
    )
    assert code == 200 and payload["ok"]
    code, payload = _post(
        server,
        "/api/compose/decide",
        {
            "slug": "demo", "round": 1, "block": "b2", "action": "adopt_edited",
            "text": "人工定稿：更口语，总的来说这样更顺。", "confirm": True,
        },
    )
    assert code == 200 and payload["ok"]
    code, payload = _post(
        server,
        "/api/compose/decide",
        {"slug": "demo", "round": 1, "block": "b3", "action": "discard", "direction": "冗余", "confirm": True},
    )
    assert code == 200 and payload["ok"]
    # 坏裁决：未知块 → ok:false（不 500）
    code, payload = _post(
        server,
        "/api/compose/decide",
        {"slug": "demo", "round": 1, "block": "zz", "action": "keep", "confirm": True},
    )
    assert code == 200 and payload["ok"] is False and "块不存在" in payload["error"]

    # adopt：正稿入队列 + 归因
    assert (root / "demo.compose" / "draft.md").read_bytes() == draft_before  # 裁决全程仍不动
    code, payload = _post(server, "/api/compose/adopt", {"slug": "demo", "confirm": True})
    assert code == 200 and payload["ok"]
    assert payload["article"] == "demo.md"
    assert payload["merged_blocks"] == 2 and payload["habits_learned"] >= 1
    assert (root / "demo.compose" / "draft.md").read_bytes() != draft_before  # 唯有 adopt 落盘

    code, payload = _get(server, "/api/articles")
    assert [a["path"] for a in payload["articles"]] == ["demo.md"]  # 会话目录不进队列
    detail_code, detail = _get(server, "/api/compose/session?slug=demo")
    assert detail_code == 200 and detail["status"] == "adopted" and detail["final_article"] == "demo.md"


def test_remove_source_endpoint_and_confirm_gate(server, root: Path, source: Path) -> None:
    """F-C 素材撤池：confirm 闸门 / 撤池成功回勾选章节 / 不在池→ok:false 不 500。"""
    compose_service.new_session("demo", "T", root=root)
    compose_service.add_source("demo", source, ["一、概述"], root=root)

    # confirm 闸门（server 动作层统一强制）
    code, payload = _post(server, "/api/compose/remove_source", {"slug": "demo", "path": str(source)})
    assert code == 400 and "confirm" in payload["error"]
    # 缺参 400
    code, payload = _post(server, "/api/compose/remove_source", {"confirm": True})
    assert code == 400

    # 成功：删条目 + 返回被移除源勾选章节（连坐文案数据面）
    code, payload = _post(
        server, "/api/compose/remove_source", {"slug": "demo", "path": str(source), "confirm": True}
    )
    assert code == 200 and payload["ok"] is True
    assert payload["slug"] == "demo" and payload["path"] == str(source.resolve())
    assert payload["sections"] == ["一、概述"] and payload["sources_left"] == 0
    code, detail = _get(server, "/api/compose/session?slug=demo")
    assert code == 200 and detail["sources"] == []  # 详情面已无该条

    # 不在池（二次撤同源）→ 200 {ok:false} 可读，不 500
    code, payload = _post(
        server, "/api/compose/remove_source", {"slug": "demo", "path": str(source), "confirm": True}
    )
    assert code == 200 and payload["ok"] is False and "素材不在池中" in payload["error"]


def test_generate_job_failure_is_readable(server, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compose_service.new_session("bad", "T", root=root)

    def boom(messages, *, task=None, on_delta=None, **kw):
        raise RuntimeError("AI 未配置（base_url/model 缺失）")

    monkeypatch.setattr(compose_service, "_chat_stream", boom)
    code, payload = _post(server, "/api/compose/generate", {"slug": "bad", "confirm": True})
    assert code == 200 and payload["ok"]
    job = _wait_job(server, payload["job_id"])
    assert job["status"] == "failed" and "AI 未配置" in job["error"]


def test_adopt_without_decisions_returns_ok_false(server, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compose_service, "_chat_stream",
        lambda messages, *, task=None, on_delta=None, **kw: REPLY_R1,
    )
    _post(server, "/api/compose/new", {"slug": "demo", "title": "T", "confirm": True})
    code, payload = _post(server, "/api/compose/generate", {"slug": "demo", "confirm": True})
    _wait_job(server, payload["job_id"])
    code, payload = _post(server, "/api/compose/adopt", {"slug": "demo", "confirm": True})
    assert code == 200 and payload["ok"] is False and "裁决" in payload["error"]


def test_compose_generate_job_kind_and_orphan_recovery(
    root: Path, _isolate_home: Path
) -> None:
    """jobs 面：compose_generate 合法 kind；遗留 queued/running 行孤儿恢复。"""
    from musespiral.jobs.runner import JobRunner

    store = JobStore()
    job = store.create("compose_generate", {"slug": "x"})
    assert job["kind"] == "compose_generate" and jobs_path().is_file()
    runner = JobRunner()
    try:
        assert runner.startup() == 1  # 遗留 queued → failed(interrupted)
        got = store.get(job["id"])
        assert got["status"] == "failed" and "interrupted" in got["error"]
    finally:
        runner.shutdown(wait=True)
