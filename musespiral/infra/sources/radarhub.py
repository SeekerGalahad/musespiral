"""radar-hub 只读 HTTP 客户端（第二十期 §三/F55，四端点钉死）。

端点固定四枚（hub/api.py 实测路由）：
  GET /api/overview                 全景（summary + radars 摘要）
  GET /api/radars                   雷达线清单
  GET /api/radars/<id>/outputs      单线产物清单（含文件级 rel/path/mtime）
  GET /api/health                   健康探测

红线（正典 §三依赖规则 + F55）：
  - 只读：本客户端只发 GET——run/stop 端点在 hub 侧存在但不接（雷达自有
    launchd 节律，musespiral 不越权驱动）；
  - 回环钉死：base 构造即过 registry.validate_base_url（host 非 127.0.0.1
    一律 ValueError——SSRF 纪律延伸，构造期拦截而非请求期）；
  - 宕机返 down 不抛：任何网络/HTTP/解析失败统一返回 {"down": True,
    "error": 可读文本}，调用方（ingest/健康探测）拿灰灯语义，不炸线程。

超时 3s（正典 §三：超时 3s；测试可注入更短值打超时路径）。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import registry

# 默认超时（秒）——正典 §三
DEFAULT_TIMEOUT = 3.0

# 四端点路径（钉死；不提供任何写端点方法）
PATH_OVERVIEW = "/api/overview"
PATH_RADARS = "/api/radars"
PATH_OUTPUTS = "/api/radars/{radar_id}/outputs"
PATH_HEALTH = "/api/health"

# radar_id 合法形（hub 路由 [a-z0-9_]+ 的宽容超集：禁空白/斜杠/点——
# 防路径拼接逃逸到其他路径）
_RADAR_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def is_down(payload: Any) -> bool:
    """宕机载荷判定：客户端失败统一形态 {"down": True, ...}。"""
    return isinstance(payload, dict) and payload.get("down") is True


class RadarHubClient:
    """radar-hub 四端点只读客户端（实例轻量：base + timeout，无连接态）。"""

    def __init__(self, base: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        # 回环钉死在构造期：非 127.0.0.1 的 base 直接 ValueError（registry
        # 单点维护，与本模块不重复实现校验规则）
        self._base = registry.validate_base_url(base).rstrip("/")
        self._timeout = timeout

    @property
    def base(self) -> str:
        return self._base

    # ── 四端点（全部 GET；失败返 down 载荷不抛）─────────────────────

    def health(self) -> dict[str, Any]:
        """GET /api/health → hub 原载荷；宕机/非 2xx → {"ok": False, "down": True, ...}。"""
        payload = self._get_json(PATH_HEALTH)
        if is_down(payload):
            return {"ok": False, **payload}
        return payload

    def overview(self) -> dict[str, Any]:
        """GET /api/overview → hub 原载荷；失败 → down 载荷。"""
        return self._get_json(PATH_OVERVIEW)

    def radars(self) -> dict[str, Any]:
        """GET /api/radars → {"radars": [...]}；失败 → down 载荷。"""
        return self._get_json(PATH_RADARS)

    def outputs(self, radar_id: str) -> dict[str, Any]:
        """GET /api/radars/<id>/outputs → {"outputs": [...]}；失败 → down 载荷。

        radar_id 非法形（空白/斜杠/点等）是调用方拼参错误，构造 URL 前即
        ValueError（可读错误，不静默发请求）。
        """
        if not isinstance(radar_id, str) or not _RADAR_ID_RE.match(radar_id):
            raise ValueError(f"radar_id 非法（仅字母/数字/下划线/连字符）: {radar_id!r}")
        return self._get_json(PATH_OUTPUTS.format(radar_id=radar_id))

    # ── 内部：GET + JSON 解析（唯一网络出口）────────────────────────

    def _get_json(self, path: str) -> dict[str, Any]:
        url = self._base + path
        try:
            req = Request(url, method="GET", headers={"Accept": "application/json"})
            with urlopen(req, timeout=self._timeout) as resp:  # noqa: S310 - base 已钉回环
                raw = resp.read()
        except HTTPError as exc:
            return self._down(f"HTTP {exc.code} {exc.reason} @ {path}")
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            return self._down(f"连接失败 @ {path}: {reason}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._down(f"响应不是合法 JSON @ {path}: {exc}")
        if not isinstance(data, dict):
            return self._down(f"响应不是 JSON 对象 @ {path}")
        return data

    @staticmethod
    def _down(error: str) -> dict[str, Any]:
        """统一宕机载荷（灰灯语义：调用方不抛不阻塞）。"""
        return {"down": True, "error": error}
