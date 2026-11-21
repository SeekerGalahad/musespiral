"""radar-hub 只读客户端测试（第二十期 §五第 3 行）：infra/sources/radarhub。

覆盖：四端点真实回环 HTTP mock（本机 127.0.0.1 + 系统分配端口，零外网）/
宕机（连接拒绝）与超时（慢响应）返 down 不抛/回环钉死（非 127.0.0.1
构造即拒）/radar_id 非法形拒绝/只读断言（服务端记录到的请求全部是 GET）。
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from musespiral.infra.sources import radarhub


# ── 测试用回环 hub（真实 HTTP，记录方法）───────────────────────────


class _HubState:
    """服务端观测面：收到的方法与路径。"""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.slow_paths: set[str] = set()


class _HubHandler(BaseHTTPRequestHandler):
    """四端点钉死形状的假 hub；slow_paths 命中则挂起打超时路径。"""

    state: _HubState = _HubState()

    def _serve(self) -> None:
        self.state.requests.append((self.command, self.path))
        if self.path in self.state.slow_paths:
            import time

            time.sleep(1.0)
            self.send_response(200)
            self.end_headers()
            return
        routes = {
            "/api/health": {"ok": True, "time": 1794756122.0},
            "/api/overview": {"summary": {"total": 10}, "radars": []},
            "/api/radars": {"radars": [{"id": "embedded_github_radar"}], "errors": []},
            "/api/radars/embedded_github_radar/outputs": {
                "outputs": [
                    {"id": "curated", "files": [{"name": "scored.json", "path": "/tmp/x/scored.json"}]}
                ]
            },
        }
        body = routes.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def hub():
    """起真实回环假 hub（127.0.0.1:0），返回 (base, 观测面)。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HubHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://127.0.0.1:{port}", _HubHandler.state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _dead_port() -> int:
    """取一个确定无监听的端口（bind 后立即释放→连接拒绝）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── 回环钉死（构造期拒绝，不发出任何请求）─────────────────────────


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:8787",
        "http://[::1]:8787",
        "http://10.0.0.5:8787",
        "http://radar.example.com",
        "ftp://127.0.0.1:8787",
        "",
    ],
)
def test_constructor_rejects_non_loopback(base: str) -> None:
    with pytest.raises(ValueError):
        radarhub.RadarHubClient(base)


def test_constructor_accepts_loopback() -> None:
    client = radarhub.RadarHubClient("http://127.0.0.1:8787/")
    assert client.base == "http://127.0.0.1:8787"  # 尾斜杠剥除


# ── 四端点 mock（真实 HTTP 回环）────────────────────────────────────


def test_four_endpoints_roundtrip(hub) -> None:
    base, state = hub
    client = radarhub.RadarHubClient(base)
    assert client.health() == {"ok": True, "time": 1794756122.0}
    assert client.overview() == {"summary": {"total": 10}, "radars": []}
    assert client.radars() == {"radars": [{"id": "embedded_github_radar"}], "errors": []}
    payload = client.outputs("embedded_github_radar")
    assert payload["outputs"][0]["files"][0]["name"] == "scored.json"
    # 只读红线：全部请求都是 GET（run/stop 端点从未被触达）
    assert {method for method, _ in state.requests} == {"GET"}


def test_outputs_rejects_malformed_radar_id(hub) -> None:
    base, _ = hub
    client = radarhub.RadarHubClient(base)
    for bad in ("", "../radars", "a b", "a/b"):
        with pytest.raises(ValueError):
            client.outputs(bad)


def test_unknown_endpoint_returns_down(hub) -> None:
    base, _ = hub
    client = radarhub.RadarHubClient(base)
    payload = client.outputs("nosuch")  # 假 hub 404
    assert radarhub.is_down(payload) and "404" in payload["error"]


def test_down_returns_down_payload_not_raise() -> None:
    """连接拒绝（hub 宕机）→ 四端点统一 down 载荷，零异常。"""
    client = radarhub.RadarHubClient(f"http://127.0.0.1:{_dead_port()}", timeout=0.5)
    for payload in (client.health(), client.overview(), client.radars(), client.outputs("x")):
        assert radarhub.is_down(payload)
    health = client.health()
    assert health["ok"] is False and health["down"] is True


def test_timeout_returns_down_payload(hub) -> None:
    """慢响应（>timeout）→ down 载荷（超时 3s 纪律的可注入验证）。"""
    base, state = hub
    state.slow_paths.add("/api/health")
    client = radarhub.RadarHubClient(base, timeout=0.2)
    payload = client.health()
    assert radarhub.is_down(payload)
    assert "error" in payload


def test_non_json_response_returns_down() -> None:
    """响应非 JSON → down 载荷（不抛）。"""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            raw = b"<html>not json</html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _, port = server.server_address[:2]
        client = radarhub.RadarHubClient(f"http://127.0.0.1:{port}", timeout=1.0)
        payload = client.health()
        assert radarhub.is_down(payload) and "JSON" in payload["error"]
    finally:
        server.shutdown()
        server.server_close()
