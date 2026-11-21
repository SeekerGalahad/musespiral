"""B 采集扩展单元测试（§五 5.2）：infra/fetcher 五段门（T67-1/T67-2/T67-3/
T67-5 + 提交期形状门）——mock requests + getaddrinfo，测试不联网。
"""
from __future__ import annotations

import pytest
import requests as real_requests

from musespiral.infra import fetcher

import socket as _real_socket

_GAIERROR = _real_socket.gaierror  # 真实异常类（fetcher 捕获语义保留）


class _Resp:
    """requests.Response 替身（status_code/content/text/headers 面）。"""

    def __init__(self, status: int = 200, body: bytes = b"", headers: dict | None = None) -> None:
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.headers = headers or {}


class _FakeRequests:
    """requests 模块替身：按 URL 前缀/路径路由预制响应，记录全部调用。"""

    RequestException = real_requests.RequestException

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.routes: dict[str, _Resp] = {}  # 精确 URL → 响应
        self.prefixes: list[tuple[str, _Resp]] = []  # (前缀, 响应) 依序首中

    def get(self, url: str, **_kw) -> _Resp:
        self.calls.append(url)
        if url in self.routes:
            return self.routes[url]
        for prefix, resp in self.prefixes:
            if url.startswith(prefix):
                return resp
        return _Resp(404, b"not found", {"Content-Type": "text/plain"})

    def count_for(self, needle: str) -> int:
        return sum(1 for c in self.calls if needle in c)


class _FakeSocket:
    """socket 模块替身：getaddrinfo 按映射表返回（gaierror/SOCK_STREAM 语义保留）。"""

    gaierror = _GAIERROR  # 真实异常类（fetcher 的 except socket.gaierror 捕获面）
    SOCK_STREAM = _real_socket.SOCK_STREAM

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def getaddrinfo(self, host, port, type=None):  # noqa: A002 - 形参名对齐 socket
        ips = self.mapping.get(host)
        if ips is None:
            raise _GAIERROR(f"dns fail: {host}")
        return [(2, 1, 6, "", (ip, port)) for ip in ips]


PUBLIC = {"93.184.216.34"}


@pytest.fixture
def fake_net(monkeypatch):
    """默认全网公网解析 + robots.txt 404（=allowed）；逐用例改写映射/路由。"""
    import socket as real_socket

    fq = _FakeRequests()
    fq.prefixes.append(("https://", _Resp(200, b"<html><body>x</body></html>",
                                         {"Content-Type": "text/html"})))
    fq.prefixes.append(("http://", _Resp(200, b"<html><body>x</body></html>",
                                         {"Content-Type": "text/html"})))
    hosts = {
        "example.com": list(PUBLIC),
        "a.example.com": list(PUBLIC),
        "b.example.com": list(PUBLIC),
        "evil.example.com": list(PUBLIC),
    }
    fs = _FakeSocket(hosts)
    monkeypatch.setattr(fetcher, "requests", fq)
    monkeypatch.setattr(fetcher, "socket", fs)
    monkeypatch.setattr(fetcher, "_TIMEOUT", 5.0)
    fetcher.reset_robots_cache()
    yield fq, fs, real_socket
    fetcher.reset_robots_cache()


def _robots_resp(status: int = 200, body: str = "") -> _Resp:
    return _Resp(status, body.encode(), {"Content-Type": "text/plain"})


# ── 提交期形状门（T66-5/E2E-5 同门：IP 字面量/userinfo/端口）─────────


def test_assert_public_url_submit_gate() -> None:
    for bad in (
        "http://127.0.0.1:8787/x",       # 回环 IP 字面量（radar-hub 端口）
        "http://10.0.0.5/x",             # 私网 IP 字面量
        "http://192.168.1.10/x",
        "http://[::1]:8790/x",           # IPv6 回环字面量
        "https://u:p@example.com/feed",  # userinfo
        "ftp://example.com/f",           # 非 http(s)
        "http://example.com:8080/x",     # 非 80/443 端口
        "",
    ):
        with pytest.raises(fetcher.RefusedError):
            fetcher.assert_public_url(bad)
    assert fetcher.assert_public_url("https://example.com/a") == "https://example.com/a"
    assert fetcher.assert_public_url("http://example.com:80/a") == "http://example.com:80/a"


# ── T67-1 域名解析到私网 = 拒（egress 解析期复检，零请求发出）────────


def test_t67_1_domain_resolving_private_refused(fake_net) -> None:
    fq, fs, _ = fake_net
    fs.mapping["example.com"] = ["10.0.0.5"]
    with pytest.raises(fetcher.RefusedError, match="10.0.0.5"):
        fetcher.fetch("https://example.com/x", mode="auto")
    assert fq.calls == []  # mock requests 未被调用（零请求发出）


# ── T67-2 重定向跳内网 = 第 2 跳拒（每跳重验，零后续请求）───────────


def test_t67_2_redirect_to_internal_refused_per_hop(fake_net) -> None:
    fq, fs, _ = fake_net
    fq.routes["https://a.example.com/x"] = _Resp(
        302, b"", {"Location": "http://169.254.169.254/", "Content-Type": "text/plain"}
    )
    with pytest.raises(fetcher.RefusedError, match="169.254.169.254"):
        fetcher.fetch("https://a.example.com/x", mode="auto")
    # 对第 2 跳目标零请求（含 robots 探测）——只有对首跳 origin 的请求
    assert fq.count_for("169.254.169.254") == 0


# ── T67-3 robots 三态（同输入两 mode 断言：自动硬拒/手动徽章）────────


def test_t67_3_robots_three_states_auto_vs_manual(fake_net) -> None:
    fq, fs, _ = fake_net
    # ① Disallow 匹配路径
    fq.routes["https://example.com/robots.txt"] = _robots_resp(
        200, "User-agent: *\nDisallow: /private\nCrawl-delay: 60"
    )
    with pytest.raises(fetcher.RefusedError, match="robots 不允许"):
        fetcher.fetch("https://example.com/private/doc", mode="auto")  # 自动流硬拒
    manual = fetcher.fetch("https://example.com/private/doc", mode="manual")
    assert manual.robots["state"] == "disallowed"  # 手动流徽章不阻断
    assert manual.robots["crawl_delay"] == 60
    assert manual.status == 200
    # ② robots.txt 404 → allowed（两 mode 同过）
    fq.routes["https://b.example.com/robots.txt"] = _robots_resp(404)
    assert fetcher.fetch("https://b.example.com/a", mode="auto").robots["state"] == "allowed"
    # ③ robots.txt 5xx → 自动流 fail-closed 拒 / 手动流 unknown 继续
    fq.routes["https://evil.example.com/robots.txt"] = _robots_resp(503)
    with pytest.raises(fetcher.RefusedError, match="不可达"):
        fetcher.fetch("https://evil.example.com/a", mode="auto")
    manual2 = fetcher.fetch("https://evil.example.com/a", mode="manual")
    assert manual2.robots["state"] == "unknown" and manual2.status == 200


# ── T67-5 robots 缓存：命中不发请求；TTL 过期重取 ───────────────────


def test_t67_5_robots_cache_hit_and_ttl_expiry() -> None:
    calls = {"n": 0}

    def fetch_fn(origin: str) -> dict:
        calls["n"] += 1
        return {"state": "allowed", "crawl_delay": None, "rules": None}

    clock = {"t": 100.0}
    cache = fetcher.RobotsCache(ttl=300.0, clock=lambda: clock["t"], fetch_fn=fetch_fn)
    cache.get("https://x.example.com")
    cache.get("https://x.example.com")
    assert calls["n"] == 1  # 缓存命中不发请求
    clock["t"] += 301.0  # TTL 过期
    cache.get("https://x.example.com")
    assert calls["n"] == 2  # 过期后重取


# ── 限额门（门 5）：content-type 白名单 / body 上限 / 超时文案 ───────


def test_fetch_limits_content_type_and_body(fake_net) -> None:
    fq, _, _ = fake_net
    fq.routes["https://example.com/bad-ct"] = _Resp(200, b"data", {"Content-Type": "application/octet-stream"})
    with pytest.raises(fetcher.RefusedError, match="content-type"):
        fetcher.fetch("https://example.com/bad-ct", mode="manual")
    fq.routes["https://example.com/huge"] = _Resp(
        200, b"x" * (fetcher._MAX_BODY + 1), {"Content-Type": "text/html"}
    )
    with pytest.raises(fetcher.RefusedError, match="5MB"):
        fetcher.fetch("https://example.com/huge", mode="manual")
    fq.routes["https://example.com/500"] = _Resp(500, b"err", {"Content-Type": "text/html"})
    with pytest.raises(fetcher.FetchError, match="HTTP 500"):
        fetcher.fetch("https://example.com/500", mode="manual")
    # 网络异常（DNS 视角）→ FetchError 可读文案
    fq.routes["https://example.com/net"] = None  # 占位不命中
    def _boom(url, **kw):
        fq.calls.append(url)
        raise real_requests.RequestException("connection reset")
    fq.get = _boom  # type: ignore[method-assign]
    with pytest.raises(fetcher.FetchError, match="网络请求失败"):
        fetcher.fetch("https://example.com/net", mode="manual")
