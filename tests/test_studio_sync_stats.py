"""F39 Studio 回采入口 — HTTP 契约 + job 层测试（正典 §五「F39」行）。

覆盖：/api/action/sync_stats confirm 闸门（缺/假 → 400）；confirm:true →
200 {ok, job_id} 且 jobs.jsonl 出现 kind=sync_stats 行（走既有 job 层）；
job 执行体调 analytics.sync_reads（CLI stats --sync 同一实现）并以
scan._log_path(root)（root 自带优先，缺省 home）为回填目标；AnalyticsError
→ job failed（错误脱敏入库）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra.wechat.analytics import AnalyticsError
from musespiral.jobs.store import KINDS, JobStore


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def studio_root(tmp_path: Path) -> Path:
    root = tmp_path / "articles"
    root.mkdir()
    (root / "sample.md").write_text(
        "---\ntitle: 样例\ncover: cover.png\ndigest: 摘要。\n---\n\n正文一段。\n",
        encoding="utf-8",
    )
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


def _post(server: studio.ThreadingHTTPServer, path: str, body: dict | None) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    conn.request("POST", path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _get(server: studio.ThreadingHTTPServer, path: str) -> tuple[int, dict]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return resp.status, data


def _wait_job(server: studio.ThreadingHTTPServer, job_id: str, timeout: float = 10.0) -> dict:
    import time

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        _, job = _get(server, f"/api/jobs/{job_id}")
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


def test_sync_stats_kind_whitelisted() -> None:
    """jobs KINDS 白名单含 sync_stats（store.create 校验面）。"""
    assert "sync_stats" in KINDS


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": "true"}])
def test_sync_stats_requires_confirm_true(server, body: dict) -> None:
    code, payload = _post(server, "/api/action/sync_stats", body)
    assert code == 400
    assert payload["ok"] is False
    assert "confirm" in payload["error"]


def test_sync_stats_creates_job_and_runs_sync(
    server, studio_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    from musespiral.infra.wechat import analytics

    def fake_sync_reads(log_path):
        seen.append(Path(log_path))
        return {"candidates": 3, "matched": 2, "updated": 2, "windows": 1, "fetched": 5}

    monkeypatch.setattr(analytics, "sync_reads", fake_sync_reads)

    code, payload = _post(server, "/api/action/sync_stats", {"confirm": True})
    assert code == 200
    assert payload["ok"] is True and payload["job_id"]

    job = _wait_job(server, payload["job_id"])
    assert job["kind"] == "sync_stats"
    assert job["status"] == "done"
    assert job["result"]["updated"] == 2  # sync_reads 返回值入 result
    # 回填目标 = scan._log_path(root)：root 无自带日志 → home 侧 publish_log.jsonl
    assert len(seen) == 1 and seen[0].name == "publish_log.jsonl"
    # jobs.jsonl 落盘行 kind 断言（既有 job 层，非旁路）
    kinds = [j["kind"] for j in JobStore().list(limit=None)]
    assert "sync_stats" in kinds


def test_sync_stats_analytics_error_fails_job(
    server, monkeypatch: pytest.MonkeyPatch
) -> None:
    from musespiral.infra.wechat import analytics

    def boom(log_path):
        raise AnalyticsError("接口不可用（errcode=48001）")

    monkeypatch.setattr(analytics, "sync_reads", boom)
    code, payload = _post(server, "/api/action/sync_stats", {"confirm": True})
    assert code == 200
    job = _wait_job(server, payload["job_id"])
    assert job["status"] == "failed"
    assert "接口不可用" in job["error"]
