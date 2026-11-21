"""Studio 源连接面契约测试（第二十期 M3：GET /api/settings sources 域 + 四端点）。

覆盖：sources 域形状（清单+每源素材计数+radar-hub 读侧预置不落盘）/手动验证
回写健康三态/启停窄门/ingest 任务（单源=kind:ingest job；radarhub 预置首动作
固化 sources.json）/方法语义（GET-only 端点 405）。真实回环服务器 + tmp_path
与 MUSESPIRAL_HOME 全隔离（radarhub 探测走 127.0.0.1 回环客户端且断言双态兼容：
本机 8787 在跑则 ok、不在则 down 灰灯——down 是正常返回值不是失败）。
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
from musespiral.services import materials_service as ms
from musespiral.services import sources_service as src_svc


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> Path:
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
        from musespiral.studio.actions import _reset_runner

        _reset_runner()


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


def _seed_kb(env: Path) -> None:
    (env / "kb").mkdir(parents=True)
    src_svc.register_source(
        {
            "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
            "config": {"root": str(env / "kb"), "sources": []},
            "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
        }
    )
    ms.add_material(
        model.make_material(
            mtype="text", source_id="kb", uri="doc.md", title="文档",
            payload={"local_path": "doc.md"}, fetched_at="2026-11-16T09:00:00",
        )
    )


def test_settings_sources_domain_shape(server, studio_root, _isolate_home) -> None:
    """sources 域：kb 在册 + radarhub 读侧预置（不落盘）+ 每源素材计数。"""
    _seed_kb(_isolate_home)
    before = (_isolate_home / "sources.json").read_text(encoding="utf-8")  # 种子 kb 的写入
    code, payload = _request(server, "GET", "/api/settings")
    assert code == 200
    domain = payload["sources"]
    ids = [s["id"] for s in domain["list"]]
    assert ids == ["kb", "radarhub"]  # 预置一条追加在读侧
    assert domain["counts"] == {"kb": 1} and domain["total_materials"] == 1
    radar = domain["list"][1]
    assert radar["type"] == "radar_hub" and radar["enabled"] is True
    assert radar["config"]["base"].startswith("http://127.0.0.1")  # 回环钉死
    # 读侧预置零写入：GET 后 sources.json 逐字节不变（radarhub 未被固化）
    assert (_isolate_home / "sources.json").read_text(encoding="utf-8") == before
    assert "radarhub" not in before


def test_sources_verify_writes_health(server, studio_root, _isolate_home) -> None:
    _seed_kb(_isolate_home)
    code, payload = _request(server, "POST", "/api/sources/verify", {"id": "kb", "confirm": True})
    assert code == 200 and payload["health"]["status"] == "ok"
    assert payload["health"]["last_ok"]  # ok 回写时间戳
    _, again = _request(server, "GET", "/api/settings")
    assert again["sources"]["list"][0]["health"]["status"] == "ok"
    # 缺 id / 未知源 → 400；GET 语义 405（动作接口仅 POST）
    assert _request(server, "POST", "/api/sources/verify", {"confirm": True})[0] == 400
    assert _request(server, "POST", "/api/sources/verify", {"id": "nope", "confirm": True})[0] == 400
    assert _request(server, "GET", "/api/sources/verify")[0] == 405


def test_sources_enabled_toggle(server, studio_root, _isolate_home) -> None:
    _seed_kb(_isolate_home)
    code, payload = _request(server, "POST", "/api/sources/enabled", {"id": "kb", "enabled": False, "confirm": True})
    assert code == 200 and payload["enabled"] is False
    assert src_svc.get_source("kb")["enabled"] is False
    assert _request(server, "POST", "/api/sources/enabled", {"id": "kb", "enabled": "x", "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/sources/enabled", {"id": "nope", "enabled": True, "confirm": True})[0] == 400


def test_radarhub_preset_first_action_solidifies(server, studio_root, _isolate_home) -> None:
    """预置 radarhub 首次「验证/启停/刷新」→ install_radarhub 固化 sources.json 再执行。"""
    code, payload = _request(server, "POST", "/api/sources/verify", {"id": "radarhub", "confirm": True})
    assert code == 200  # 固化成功（本机 8787 真实在跑则 ok；不在则 down——两者都 200）
    assert src_svc.get_source("radarhub") is not None
    assert (_isolate_home / "sources.json").exists()
    assert payload["health"]["status"] in ("ok", "down")


def test_ingest_action_creates_job(server, studio_root, _isolate_home) -> None:
    """单步刷新此源：POST /api/action/ingest {source_id} → kind:ingest job → 结果可见。"""
    _seed_kb(_isolate_home)
    code, payload = _request(server, "POST", "/api/action/ingest", {"source_id": "kb", "confirm": True})
    assert code == 200 and payload["ok"] is True and payload["job_id"]
    for _ in range(50):
        _, jobs = _request(server, "GET", "/api/jobs")
        job = next((j for j in jobs["jobs"] if j["id"] == payload["job_id"]), None)
        if job and job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["kind"] == "ingest" and job["status"] == "done"
    assert job["result"]["source_id"] == "kb" and job["result"]["status"] == "ok"
    assert job["result"]["added"] == 0  # 同 id 等值幂等跳过（已采过）
    # 参数窄门：缺 source_id 且无 all → 400；GET 405
    assert _request(server, "POST", "/api/action/ingest", {"confirm": True})[0] == 400
    assert _request(server, "POST", "/api/action/ingest", {"source_id": "nope", "confirm": True})[0] == 200  # job 内 failed（可读错误）


def test_ingest_all_and_method_semantics(server, studio_root, _isolate_home) -> None:
    _seed_kb(_isolate_home)
    code, payload = _request(server, "POST", "/api/action/ingest", {"all": True, "confirm": True})
    assert code == 200 and payload["job_id"]
    for _ in range(50):
        _, jobs = _request(server, "GET", "/api/jobs")
        job = next((j for j in jobs["jobs"] if j["id"] == payload["job_id"]), None)
        if job and job["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert job["status"] == "done"
    results = {r["source_id"]: r["status"] for r in job["result"]["results"]}
    assert results.get("kb") == "ok"
    assert _request(server, "GET", "/api/action/ingest")[0] == 405
    assert _request(server, "GET", "/api/materials/triage")[0] == 405
    assert _request(server, "GET", "/api/materials/delete")[0] == 405
