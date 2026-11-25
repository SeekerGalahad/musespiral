"""Studio /api/action/refine + /api/sources 契约测试（第二十期 F57/F53，M4）。

沿 test_studio_materials.py 惯例：真实回环服务器 + http.client，tmp_path 与
MUSESPIRAL_HOME 全隔离。覆盖：预览/采纳两形态（confirm 全局闸门、AI 起草零落盘、
采纳后素材视图可见 derived 血统）/参数与越界 400/密钥零回显（AIError 面
经 _error_payload 脱敏——api_key 形态值不得出现在任何响应里）/GET·POST
/api/sources（源清单形状 + 注册校验：kebab/类型完备/回环钉死）。
"""
from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.core.materials import model
from musespiral.infra.ai import AIError
from musespiral.services import materials_service as ms
from musespiral.services import refine_service as svc
from musespiral.services import sources_service as src_svc


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch) -> Path:
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


def _seed(env: Path) -> str:
    """kb 源 + 一条带正文的 kb text 素材；返回素材 id。"""
    root = env / "kb"
    root.mkdir()
    (root / "doc.md").write_text("---\ntitle: 原文\n---\n\n知识库文档正文。\n", encoding="utf-8")
    src_svc.register_source({
        "id": "kb", "type": "kb", "label": "知识库", "enabled": True,
        "config": {"root": str(root), "sources": []},
        "health": {"status": "unknown", "last_ok": None}, "subscriptions": [],
    })
    row = model.make_material(
        mtype="text", source_id="kb", uri="doc.md", title="原文", license="CC-BY",
        payload={"local_path": "doc.md", "meta": {"license_note": "CC-BY 4.0"}},
        fetched_at="2026-11-16T09:00:00",
    )
    ms.add_material(row)
    return row["id"]


def _ai_on(env: Path, api_key: str = "") -> None:
    (env / "ai.json").write_text(
        json.dumps({
            "default": "t",
            "profiles": {"t": {"base_url": "http://127.0.0.1:9/v1", "api_key": api_key, "model": "m"}},
            "routing": {},
        }),
        encoding="utf-8",
    )


def _mock_chat(monkeypatch, reply: str = "· 预览草稿", error: Exception | None = None):
    def fake_chat(messages, *, task=None, **kwargs):
        if error is not None:
            raise error
        return reply

    monkeypatch.setattr(svc, "_chat", fake_chat)


def _lines(env: Path) -> list[dict]:
    path = env / "materials" / "materials.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ── /api/action/refine：预览/采纳两形态 ─────────────────────────────


def test_refine_preview_then_adopt_roundtrip(server, studio_root, env, monkeypatch) -> None:
    mid = _seed(env)
    _ai_on(env)
    _mock_chat(monkeypatch)
    # 预览：AI 起草同步返回，零落盘
    code, payload = _request(
        server, "POST", "/api/action/refine",
        {"action": "preview", "id": mid, "confirm": True},
    )
    assert code == 200 and payload["ok"] is True
    assert payload["text"] == "· 预览草稿" and payload["op"] == "summarize"
    assert len(_lines(env)) == 1  # 预览零落盘（不建 job、不写素材库）
    # 采纳：人工「存为素材」→ derived 血统落库
    code, adopted = _request(
        server, "POST", "/api/action/refine",
        {"action": "adopt", "id": mid, "text": "· 预览草稿", "confirm": True},
    )
    assert code == 200 and adopted["ok"] is True
    _, listing = _request(server, "GET", "/api/materials")
    derived = next(m for m in listing["items"] if m["type"] == "derived")
    assert derived["license"] == "CC-BY"  # license 继承
    _, detail = _request(server, "GET", f"/api/materials/detail?id={derived['id']}")
    assert detail["material"]["references"] == [
        {"from_id": mid, "op": "summarize", "deleted": False}
    ]


def test_refine_confirm_gate_and_validation(server, studio_root, env, monkeypatch) -> None:
    mid = _seed(env)
    _ai_on(env)
    _mock_chat(monkeypatch)
    # confirm 全局闸门：缺省拒绝
    assert _request(
        server, "POST", "/api/action/refine", {"action": "preview", "id": mid}
    )[0] == 400
    # action 枚举 / id / text 必填
    assert _request(
        server, "POST", "/api/action/refine", {"id": mid, "confirm": True}
    )[0] == 400
    assert _request(
        server, "POST", "/api/action/refine", {"action": "bogus", "id": mid, "confirm": True}
    )[0] == 400
    assert _request(
        server, "POST", "/api/action/refine", {"action": "preview", "confirm": True}
    )[0] == 400
    assert _request(
        server, "POST", "/api/action/refine", {"action": "adopt", "id": mid, "confirm": True}
    )[0] == 400  # 缺 text
    # 越界：不存在的素材 id
    code, payload = _request(
        server, "POST", "/api/action/refine",
        {"action": "preview", "id": "nope00000000", "confirm": True},
    )
    assert code == 400 and "不存在" in payload["error"]
    assert len(_lines(env)) == 1  # 以上全部零落盘


def test_refine_preview_ai_unconfigured_readable(server, studio_root, env) -> None:
    mid = _seed(env)  # 无 ai.json
    code, payload = _request(
        server, "POST", "/api/action/refine",
        {"action": "preview", "id": mid, "confirm": True},
    )
    assert code == 200 and payload["ok"] is False and "AI 未配置" in payload["error"]


def test_refine_error_never_echoes_api_key(server, studio_root, env, monkeypatch) -> None:
    """密钥零回显：即使异常文本携带密钥，响应也经 _error_payload 脱敏。"""
    mid = _seed(env)
    _ai_on(env, api_key="sk-TEST-SECRET-999")
    _mock_chat(monkeypatch, error=AIError("调用 AI 失败: api_key=sk-TEST-SECRET-999 被拒绝"))
    code, payload = _request(
        server, "POST", "/api/action/refine",
        {"action": "preview", "id": mid, "confirm": True},
    )
    assert code == 200 and payload["ok"] is False
    assert "sk-TEST-SECRET-999" not in json.dumps(payload, ensure_ascii=False)
    assert "api_key=***" in payload["error"]


# ── /api/sources：GET 清单 + POST 注册（F53 两段式接入写面）─────────


def test_sources_get_shape(server, studio_root, env) -> None:
    _seed(env)
    code, payload = _request(server, "GET", "/api/sources")
    assert code == 200
    assert payload["total_materials"] == 1
    assert payload["counts"] == {"kb": 1}
    row = next(s for s in payload["list"] if s["id"] == "kb")
    assert row["type"] == "kb" and row["enabled"] is True


def test_sources_register_validation(server, studio_root, env) -> None:
    # 必填面：type/config（id 缺省=自动生成——W1 A5 语义，返回 200 而非 400）
    assert _request(server, "POST", "/api/sources", {"type": "dir", "config": {"path": "/tmp"}, "confirm": True})[0] == 200
    assert _request(server, "POST", "/api/sources", {"id": "d1", "config": {"path": "/tmp"}, "confirm": True})[0] == 400
    assert _request(server, "POST", "/api/sources", {"id": "d1", "type": "dir", "confirm": True})[0] == 400
    # 类型完备：dir 必带 path
    code, payload = _request(
        server, "POST", "/api/sources",
        {"id": "d1", "type": "dir", "label": "目录源", "config": {}, "confirm": True},
    )
    assert code == 400 and "config.path" in payload["error"]
    # 回环钉死（SSRF 纪律延伸）：非 127.0.0.1 的 radar_hub base 拒收
    code, payload = _request(
        server, "POST", "/api/sources",
        {"id": "hub2", "type": "radar_hub", "config": {"base": "http://10.0.0.5:8787"}, "confirm": True},
    )
    assert code == 400 and "127.0.0.1" in payload["error"]


def test_sources_register_and_upsert(server, studio_root, env) -> None:
    code, payload = _request(
        server, "POST", "/api/sources",
        {"id": "d1", "type": "dir", "label": "目录源",
         "config": {"path": str(env)}, "confirm": True},
    )
    assert code == 200 and payload["ok"] is True
    assert payload["source"]["id"] == "d1" and payload["source"]["enabled"] is True
    _, listing = _request(server, "GET", "/api/sources")
    assert any(s["id"] == "d1" for s in listing["list"])
    # 同 id upsert：改 label 覆盖，不新增条目
    code, payload = _request(
        server, "POST", "/api/sources",
        {"id": "d1", "type": "dir", "label": "改名", "config": {"path": str(env)}, "confirm": True},
    )
    assert code == 200
    _, listing = _request(server, "GET", "/api/sources")
    rows = [s for s in listing["list"] if s["id"] == "d1"]
    assert len(rows) == 1 and rows[0]["label"] == "改名"
