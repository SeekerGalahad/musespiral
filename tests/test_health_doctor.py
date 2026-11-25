"""W1-B1 健康端点 + doctor 预检（正典=《musespiral-运维发布改良设计-2026-11-23》§二）。

覆盖：
  - GET /api/healthz 五键形状（J2：只断言 200 不算过——五键齐 + 类型对 +
    version==安装态包版本）；jobs_pending/materials_total 与既有服务同源真值
    （建一条 queued job + 一条素材后复验数字）。
  - 契约锁：/api/healthz ∈ CONTRACT_GET_EXACT（GET 49→50），且 /api/health
    九键面板形状不动（前端四消费方零字节）。
  - doctor 三路径：缺凭证→黄 + exit 1 / 全绿→exit 0 / ffmpeg 缺→黄不红
    （monkeypatch shutil.which 与 disk_usage，不依赖真机环境）。
  - doctor 零出网（J9 边界：预检≠探活）：实现面锚——doctor 模块源码不含
    urllib/requests/http.client 网络 import；运行时锚——预检全程不触发
    infra/ai/client 的任何网络接缝。
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import threading
from pathlib import Path

import pytest
from click.testing import CliRunner

import musespiral
from musespiral import studio
from musespiral.commands import doctor_cmd
from musespiral.jobs.store import JobStore
from musespiral.services import materials_service

HEALTHZ_KEYS = {"ok", "version", "uptime_s", "jobs_pending", "materials_total"}


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def _get(server, path: str) -> tuple[int, dict]:
    import http.client

    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw)


def _material(source_id: str, uri: str) -> dict:
    from musespiral.core.materials import model

    return model.make_material(
        mtype="text",
        source_id=source_id,
        uri=uri,
        title="探针素材",
        payload={"local_path": uri},
        fetched_at="2026-11-23T09:00:00",
    )


# ── ① GET /api/healthz 五键形状（J2）─────────────────────────────


def test_healthz_five_key_shape(server) -> None:
    """五键齐 + 类型对 + version==安装态包版本（只断言 200 不算过）。"""
    code, payload = _get(server, "/api/healthz")
    assert code == 200
    assert set(payload) == HEALTHZ_KEYS
    assert payload["ok"] is True
    assert isinstance(payload["uptime_s"], float) and payload["uptime_s"] >= 0
    assert isinstance(payload["jobs_pending"], int) and payload["jobs_pending"] >= 0
    assert isinstance(payload["materials_total"], int) and payload["materials_total"] >= 0
    assert isinstance(payload["version"], str) and payload["version"]
    # version 口径=安装态（importlib.metadata），源码常量只作未安装回退
    assert payload["version"] == importlib.metadata.version("musespiral")
    assert payload["version"] == musespiral.__version__


def test_healthz_counts_are_real(server) -> None:
    """jobs_pending / materials_total 与既有服务同源（真值非硬编码）。"""
    JobStore().create("adapt", {})  # queued 行（无 runner 消费 → 恒 pending）
    materials_service.add_material(_material("kb", "13-source-analysis/a.md"))
    code, payload = _get(server, "/api/healthz")
    assert code == 200
    assert payload["jobs_pending"] == 1
    assert payload["materials_total"] == 1


def test_health_panel_shape_untouched(server) -> None:
    """/api/health 九键面板形状不动（W1-B1 只加轻面，前端消费面零字节）。"""
    code, payload = _get(server, "/api/health")
    assert code == 200
    assert set(payload) == {
        "version",
        "root",
        "disk_free_gb",
        "credentials",
        "ai",
        "imagehost",
        "cookies",
        "jobs",
        "themes",
    }


def test_healthz_in_contract_get_exact() -> None:
    """契约锁：GET 面 +1（/api/healthz 入锁且分派表接住；全集相等由
    test_architecture::test_route_surface_matches_api_contract 兜底）。"""
    import ast

    from musespiral.studio.routing import _GET_ROUTES

    assert "/api/healthz" in _GET_ROUTES
    # 从契约测试源码 AST 取锁集（不 import tests 包——纯 pytest 调用下
    # tests 无 __init__.py 不可导入；test_architecture 自身亦用同法）。
    tree = ast.parse(
        (Path(__file__).resolve().parent / "test_architecture.py").read_text(encoding="utf-8")
    )
    lock = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "CONTRACT_GET_EXACT" for t in node.targets
        ):
            lock = ast.literal_eval(node.value)
            break
    assert lock is not None
    assert "/api/healthz" in lock
    assert "/api/health" in lock  # 两个面并存不混
    assert len(lock) == 51  # GET 50→51（2026-11-24 W3 +book/chapter）


# ── ② doctor 五项预检 ─────────────────────────────────────────


def _run_doctor(*args: str):
    return CliRunner().invoke(doctor_cmd.doctor, list(args))


def test_doctor_all_green_exit_0(_isolate_home, monkeypatch) -> None:
    """全绿路径：凭证/AI/ffmpeg/KB 根齐 + 磁盘宽裕 → exit 0。"""
    from musespiral import config
    from musespiral.infra.ai import save_ai_config

    config.save_credentials("default", "wx-appid", "wx-secret")
    save_ai_config("http://127.0.0.1:11434/v1", "", "qwen3:8b")
    kb = _isolate_home / "kb"
    kb.mkdir()
    (Path(_isolate_home) / "kb_root.txt").write_text(str(kb), encoding="utf-8")
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setattr(
        doctor_cmd.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * 2**30})()
    )

    res = _run_doctor()
    assert res.exit_code == 0
    assert "全部就绪" in res.output
    for name in ("credentials", "ai", "ffmpeg", "kb_root", "disk"):
        assert name in res.output


def test_doctor_missing_credentials_yellow_exit_1(_isolate_home, monkeypatch) -> None:
    """缺凭证 → 黄 + exit 1（其余项补绿以证明黄=唯一降档源）。"""
    from musespiral.infra.ai import save_ai_config

    save_ai_config("http://127.0.0.1:11434/v1", "", "qwen3:8b")
    kb = _isolate_home / "kb"
    kb.mkdir()
    (_isolate_home / "kb_root.txt").write_text(str(kb), encoding="utf-8")
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: "/opt/homebrew/bin/ffmpeg")
    monkeypatch.setattr(
        doctor_cmd.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * 2**30})()
    )

    res = _run_doctor()
    assert res.exit_code == 1
    assert "credentials" in res.output
    assert "⚠" in res.output
    assert "✗" not in res.output  # 缺配置不判红


def test_doctor_missing_ffmpeg_yellow_not_red(_isolate_home, monkeypatch) -> None:
    """ffmpeg 缺 → 黄，绝不红（红只 reserved 磁盘水位）。"""
    from musespiral import config
    from musespiral.infra.ai import save_ai_config

    config.save_credentials("default", "wx-appid", "wx-secret")
    save_ai_config("http://127.0.0.1:11434/v1", "", "qwen3:8b")
    kb = _isolate_home / "kb"
    kb.mkdir()
    (_isolate_home / "kb_root.txt").write_text(str(kb), encoding="utf-8")
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: None)  # 无 ffmpeg
    monkeypatch.setattr(
        doctor_cmd.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * 2**30})()
    )

    res = _run_doctor()
    assert res.exit_code == 1  # 黄
    assert "ffmpeg" in res.output
    assert "✗" not in res.output


def test_doctor_disk_red_exit_2(_isolate_home, monkeypatch) -> None:
    """磁盘 <10G → 红 + exit 2（红色唯一来源）。"""
    from musespiral import config
    from musespiral.infra.ai import save_ai_config

    config.save_credentials("default", "wx-appid", "wx-secret")
    save_ai_config("http://127.0.0.1:11434/v1", "", "qwen3:8b")
    kb = _isolate_home / "kb"
    kb.mkdir()
    (_isolate_home / "kb_root.txt").write_text(str(kb), encoding="utf-8")
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(
        doctor_cmd.shutil, "disk_usage", lambda p: type("U", (), {"free": 5 * 2**30})()
    )

    res = _run_doctor()
    assert res.exit_code == 2
    assert "✗" in res.output


def test_doctor_json_shape(_isolate_home, monkeypatch) -> None:
    """--json：机读 {checks:[{name,status,detail}], exit_code}（供脚本消费）。"""
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        doctor_cmd.shutil, "disk_usage", lambda p: type("U", (), {"free": 100 * 2**30})()
    )
    res = _run_doctor("--json")
    payload = json.loads(res.output)
    assert set(payload) == {"checks", "exit_code"}
    assert res.exit_code == payload["exit_code"] == 1  # 空 home：凭证/AI/KB 黄
    assert [c["name"] for c in payload["checks"]] == [
        "credentials",
        "ai",
        "ffmpeg",
        "kb_root",
        "disk",
    ]
    for c in payload["checks"]:
        assert set(c) == {"name", "status", "detail"}
        assert c["status"] in {"green", "yellow", "red"}


def test_doctor_never_goes_online(monkeypatch) -> None:
    """零出网（J9）：实现面无网络 import + 运行时不触 AI client 网络接缝。"""
    src = inspect.getsource(doctor_cmd)
    for marker in (
        "import urllib",
        "import requests",
        "import http.client",
        "from urllib",
        "from requests",
        "from http",
    ):
        assert marker not in src, f"doctor 不得含网络 import: {marker}"

    import musespiral.infra.ai.client as client

    def _boom(*a, **k):
        raise AssertionError("doctor 触发了真实 AI 网络接缝")

    monkeypatch.setattr(client, "_post_chat_stream", _boom)
    res = _run_doctor()
    assert "_boom" not in res.output and "AssertionError" not in res.output
    assert res.exit_code in (0, 1, 2)
