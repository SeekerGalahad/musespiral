"""统一 HTTP GET 出网咽喉（B 采集扩展 §二 F67）——L1 infra。

一切新增出网（RSS 拉取 / 网页剪藏 / 手动爬取）必须经本模块；url_fetch
微信通道是存量独立豁免面（§〇 精准修改纪律，不合并）。

五段闸门（序即红线序，任一拒 = 结构化 refused 理由非裸异常）：
  1. URL 形状门：http(s) / 无 userinfo / 无 IP 字面量 / 端口限 80/443
     （提交期与取数期同一道门——assert_public_url 供 Studio 壳层 400 复用）；
  2. egress 门（SSRF 反向防线）：getaddrinfo 解析后逐 IP 复检——私网/回环/
     链路本地/保留/组播一律拒（与 radar_hub 客户端「只准回环」互补：web
     通道禁回环，防表单探测本机服务群 8766/8787/8790/8791）；
  3. robots 门：自动流 disallowed/unknown 皆硬拒（fail-closed），手动流只
     返回徽章数据不阻断；robots.txt 404→allowed、不可达（网络错/5xx）→unknown；
  4. 重定向逐跳重验：≤5 跳手动跟随，每跳重跑形状+egress+robots 门——跳向
     内网=拒且零后续请求；
  5. 限额：超时 5s、body ≤5MB、content-type 白名单（html/xml/rss/atom/json/text）。

robots 缓存：进程内 TTL 缓存（默认 300s，缓存命中不发请求），单趟进程
即弃不落盘。UA 令牌 `musespiral`（robots `User-agent: *` 组天然覆盖）。
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from .sources.url_fetch import _UA

# robots UA 令牌（与 mp.weixin 域无关的通用采集标识；* 组天然覆盖）
UA_TOKEN = "musespiral"

# 限额（B 正典 §二 F67-5；超时按任务书 5s 裁决）
_TIMEOUT = 5.0
_MAX_BODY = 5 * 1024 * 1024  # 5MB

# 出网端口门限：仅 80/443（egress 门限裁决）
_ALLOWED_PORTS = (80, 443)

# content-type 白名单（媒体类型精确匹配，参数段忽略）
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html", "application/xhtml+xml", "application/xml", "text/xml",
        "application/rss+xml", "application/atom+xml", "application/json",
        "text/plain",
    }
)

_REDIRECT_STATI = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_ROBOTS_REDIRECTS = 3


class FetchError(Exception):
    """抓取失败（网络错/HTTP≠200/重定向超限）——可读信息。"""


class RefusedError(FetchError):
    """闸门拒绝（形状/egress/robots/限额）——结构化理由，零请求或即停。"""


@dataclass
class FetchResult:
    """fetch 产物：最终规范化前的最终 URL（≤5 跳后）+ 响应面 + robots 徽章。"""

    final_url: str
    status: int
    content_type: str
    body: bytes
    robots: dict[str, Any] = field(default_factory=dict)


# ── 门 1：URL 形状（提交期/取数期同一道门）──────────────────────────


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def assert_public_url(url: str) -> str:
    """公共 web URL 形状门：http(s)/无 userinfo/无 IP 字面量/端口 80|443。

    通过原样返回（strip 后）；不满足抛 RefusedError（可读理由）。Studio
    壳层提交期与本模块取数期/重定向逐跳共用——同一道门多处执法。
    """
    raw = (url or "").strip()
    if not raw:
        raise RefusedError("URL 不能为空")
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError as exc:
        raise RefusedError(f"URL 非法: {exc}") from None
    if parsed.scheme not in ("http", "https"):
        raise RefusedError(f"仅支持 http(s) URL: {raw}")
    if parsed.username is not None or parsed.password is not None:
        raise RefusedError(f"URL 不允许携带用户凭证（userinfo）: {raw}")
    host = parsed.hostname
    if not host:
        raise RefusedError(f"URL 缺少主机名: {raw}")
    if _is_ip_literal(host):
        raise RefusedError(f"URL 不能使用 IP 字面量（请用域名）: {host}")
    if port is not None and port not in _ALLOWED_PORTS:
        raise RefusedError(f"仅允许 80/443 端口（egress 门限）: {raw}")
    return raw


# ── 门 2：egress（解析期逐 IP 复检）─────────────────────────────────


def _assert_public_ips(url: str) -> None:
    """域名解析后逐 IP 复检：任一私网/回环/链路本地/保留/组播地址即拒。

    零请求发出（解析先于 requests.get）；DNS rebinding TOCTOU 在单人本地
    威胁模型下接受并注记（B 正典 §二 F67-2）。
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"域名解析失败: {host}: {exc}") from None
    if not infos:
        raise FetchError(f"域名解析无结果: {host}")
    for info in infos:
        ip_text = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or ip.is_multicast
        ):
            raise RefusedError(f"egress 门拒绝：{host} 解析到内网/回环地址 {ip_text}")


# ── 门 3：robots（自动流硬门 / 手动流徽章）──────────────────────────


class RobotsCache:
    """进程内 robots.txt TTL 缓存（命中不发请求；单趟进程即弃不落盘）。

    fetch_fn / clock 可注入（测试离线替身）；线程安全（dict+锁轻实现，
    Studio 双 worker 与 tick 单线程共用）。
    """

    def __init__(
        self,
        ttl: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        fetch_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._fetch = fetch_fn if fetch_fn is not None else _fetch_robots
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, dict[str, Any]]] = {}

    def get(self, origin: str) -> dict[str, Any]:
        """origin（scheme://netloc）→ {state, crawl_delay, rules}；TTL 过期重取。"""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(origin)
            if entry is not None and now - entry[0] < self._ttl:
                return entry[1]
        fetched = self._fetch(origin)
        with self._lock:
            self._entries[origin] = (self._clock(), fetched)
        return fetched

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def _fetch_robots(origin: str) -> dict[str, Any]:
    """拉取并解析 origin/robots.txt：404/4xx→allowed；网络错/5xx→unknown。"""
    try:
        resp = _raw_get(urljoin(origin, "/robots.txt"), max_redirects=_ROBOTS_REDIRECTS)
    except FetchError as exc:
        return {"state": "unknown", "crawl_delay": None, "rules": None, "error": str(exc)}
    if resp.status_code >= 500:
        return {"state": "unknown", "crawl_delay": None, "rules": None,
                "error": f"robots.txt HTTP {resp.status_code}"}
    if resp.status_code != 200:
        return {"state": "allowed", "crawl_delay": None, "rules": None}
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(resp.text.splitlines())
    delay = parser.crawl_delay(UA_TOKEN)
    return {
        "state": "allowed",
        "crawl_delay": int(delay) if delay is not None else None,
        "rules": parser,
    }


def _robots_gate(url: str, mode: str) -> dict[str, Any]:
    """对单条 URL 做 robots 判定（mode=auto 硬拒 / manual 徽章不阻断）。"""
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    info = _ROBOTS.get(origin)
    state = str(info.get("state") or "unknown")
    crawl_delay = info.get("crawl_delay")
    rules = info.get("rules")
    if rules is not None:
        allowed = rules.can_fetch(UA_TOKEN, parsed.path or "/")
        state = "allowed" if allowed else "disallowed"
        if crawl_delay is None:
            delay = rules.crawl_delay(UA_TOKEN)
            crawl_delay = int(delay) if delay is not None else None
    if mode == "auto":
        if state == "disallowed":
            raise RefusedError(f"robots 不允许（自动流 fail-closed 拒绝）: {url}")
        if state == "unknown":
            raise RefusedError(f"robots.txt 不可达（自动流 fail-closed 拒绝）: {origin}")
    return {"state": state, "crawl_delay": crawl_delay}


# 模块级共享缓存（Studio 进程内单例；测试经 reset_robots_cache 清态）
_ROBOTS = RobotsCache()


def reset_robots_cache() -> None:
    """清空 robots 缓存（测试隔离用）。"""
    _ROBOTS.clear()


# ── 底层 GET（门 2 每跳执法；不跟随重定向）──────────────────────────


def _request(current: str) -> requests.Response:
    """单跳裸请求（门 1+2 已由调用方执法）：网络错 → FetchError。"""
    try:
        return requests.get(
            current,
            headers={"User-Agent": _UA},
            timeout=_TIMEOUT,
            allow_redirects=False,
            stream=False,
        )
    except requests.RequestException as exc:
        raise FetchError(f"网络请求失败: {exc}") from None


def _raw_get(url: str, *, max_redirects: int) -> requests.Response:
    """门内单跳 GET（robots.txt 自取用）：形状门→egress 门→请求。

    返回未跟随重定向的响应；每跳先过门再发请求，拒绝时零请求发出。
    """
    current = assert_public_url(url)
    _assert_public_ips(current)
    return _request(current)


# ── 公开入口：fetch（五段门全量）────────────────────────────────────


def fetch(url: str, *, mode: str = "auto") -> FetchResult:
    """统一出网 GET：五段门依序执法，任一拒即 RefusedError/FetchError。

    mode="auto"（RSS 刷新等自动流）：robots disallowed/unknown 均硬拒；
    mode="manual"（人工剪藏/爬取）：robots 只作徽章数据随结果返回。
    重定向 ≤5 跳手动跟随，每跳重跑全部闸门（含 robots——跨 origin 跳转
    重新判定）。
    """
    if mode not in ("auto", "manual"):
        raise ValueError(f"mode 必须是 auto/manual: {mode!r}")
    current = assert_public_url(url)
    robots_info: dict[str, Any] = {}
    resp: requests.Response | None = None
    for _hop in range(_MAX_REDIRECTS + 1):
        _assert_public_ips(current)  # 门 2（每跳，先于 robots——门序即红线序）
        robots_info = _robots_gate(current, mode)  # 门 3（每跳）
        try:
            resp = _request(current)
        except FetchError as exc:
            raise FetchError(f"{current}: {exc}") from None
        location = resp.headers.get("Location")
        if resp.status_code in _REDIRECT_STATI and location:
            current = assert_public_url(urljoin(current, location))  # 门 4 重验
            continue
        break
    if resp is None:  # pragma: no cover - 循环至少执行一次
        raise FetchError(f"抓取失败: {url}")
    if resp.status_code in _REDIRECT_STATI:
        raise FetchError(f"重定向缺少 Location（HTTP {resp.status_code}）: {current}")
    if resp.status_code != 200:
        raise FetchError(f"HTTP {resp.status_code}: {current}")
    # 门 5：限额（content-type / body ≤5MB）
    content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if content_type not in _ALLOWED_CONTENT_TYPES:
        raise RefusedError(f"content-type 不在白名单（html/xml/rss/atom/json/text）: {content_type or '缺失'}")
    body = resp.content or b""
    if len(body) > _MAX_BODY:
        raise RefusedError(f"响应体超过 5MB 上限: {len(body)} bytes")
    return FetchResult(
        final_url=current,
        status=resp.status_code,
        content_type=content_type,
        body=body,
        robots=robots_info,
    )
