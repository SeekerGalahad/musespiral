"""URL 规范化（B 采集扩展 §二 F69-C）——L2 纯函数，幂等。

规范化规则（保守清单，不排序余参）：
  - scheme/host 小写；
  - 去默认端口（http:80 / https:443）；
  - 去 fragment；
  - 剔追踪参 utm_* 家族 + fbclid + gclid（清单保守：非清单参数原样保留、
    保持原顺序，不重排）。

幂等是数据面不变式（T69-1 钉死）：canonical(canonical(x)) == canonical(x)——
同 URL 二次规范化相等，RSS 条目缺 guid 回退 link / 剪藏落库的 uri 稳定
id 都依赖这一点。
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# 追踪参剔除清单（保守：utm_* 五键 + 两枚点击 id；utm_ 前缀整体剔除）
_TRACKED_PREFIXES = ("utm_",)
_TRACKED_EXACT = ("fbclid", "gclid")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _is_tracked(name: str) -> bool:
    low = name.lower()
    return low in _TRACKED_EXACT or any(low.startswith(p) for p in _TRACKED_PREFIXES)


def canonical_url(url: str) -> str:
    """规范化 URL（幂等）。非 http(s)/空 host 原样返回（调用方门禁把关）。"""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return raw
    port = parsed.port
    netloc = host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    # IPv6 字面量 host 需要方括号（urlparse.hostname 剥了括号）
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
         if not _is_tracked(k)]
    )
    return urlunparse((scheme, netloc, parsed.path or "/", "", query, ""))
