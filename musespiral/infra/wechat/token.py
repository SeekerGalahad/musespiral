"""WeChat access_token management — single source of truth.

Uses the stable_token endpoint so concurrent callers don't clobber each other.
Caches to ~/.musespiral/token.json with a 300s pre-expiry refresh window.

Phase C 自顶层 musespiral/token.py 迁入（正典 §八 Phase C），行为零变更——
仅相对导入层级 from . import config → from .. import config。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ... import config

_TOKEN_EXPIRY_SECONDS = 7200
_REFRESH_WINDOW = 300  # refresh if fewer than this many seconds remain


class TokenError(RuntimeError):
    pass


def _token_path() -> Path:
    return config.home_dir() / config.TOKEN_FILE


def _read_cache() -> dict[str, Any] | None:
    p = _token_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(token: str, expires_at: float) -> None:
    p = config.home_dir()
    p.mkdir(parents=True, exist_ok=True)
    (p / config.TOKEN_FILE).write_text(
        json.dumps({"access_token": token, "expires_at": expires_at}, ensure_ascii=False),
        encoding="utf-8",
    )


def _wechat_source_address() -> str | None:
    """微信 API 走物理网卡直连（PolessVPN TUN 出口 IP 不在公众号白名单——
    2026-11-26 闭环测试发现 40164）。枚举 en* 物理网卡取 IPv4（默认路由走
    utun 时 route get 不会指向物理口——直接扫 ifconfig）；缺失回退 None。"""
    import subprocess

    try:
        r = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3)
        current_iface = None
        for line in (r.stdout or "").splitlines():
            if line and not line.startswith("\t") and not line.startswith(" "):
                # 新网卡行（如 en1: flags=...）
                name = line.split(":")[0].strip()
                current_iface = name
            elif current_iface and current_iface.startswith("en") and "inet " in line:
                ip = line.split("inet ")[1].split(" ")[0]
                if ip and not ip.startswith("127."):
                    return current_iface  # 返回接口名（en1）——curl --interface 用
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _fetch_token(appid: str, secret: str) -> tuple[str, float]:
    """Call stable_token and return (token, expires_at_epoch).

    网络路径：优先绑定物理网卡源地址直连（PolessVPN TUN 出口 IP 不在
    公众号白名单——40164 实测 2026-11-26）；直连失败回退默认路由。
    """
    url = f"{config.WECHAT_API_BASE}/cgi-bin/stable_token"
    body = {
        "grant_type": "client_credential",
        "appid": appid,
        "secret": secret,
        "force_refresh": False,
    }

    def _post(addr: str | None) -> dict:
        if addr:
            # macOS TUN 全局劫持：bind 源 IP 不够，须 curl --interface 绕 IFSCOPE
            import subprocess

            r = subprocess.run(
                ["curl", "-s", "-m", "15", "-X", "POST",
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(body), "--interface", addr, url],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0 and r.stdout.strip():
                return json.loads(r.stdout)
            raise RuntimeError(f"curl direct fail: {r.stderr[:80]}")
        resp = requests.post(url, json=body, timeout=15)
        return resp.json()

    _src = _wechat_source_address()
    data: dict = {}
    last_err: Exception | None = None
    for addr in ([_src] if _src else []) + [None]:
        try:
            data = _post(addr)
            if data.get("access_token"):
                break  # 成功（直连或默认均可）
            last_err = TokenError(f"获取 access_token 失败: {data}")
        except Exception as exc:
            last_err = exc
    if not data.get("access_token"):
        raise last_err or TokenError(f"获取 access_token 失败: {data}")
    token = data["access_token"]
    expires_at = time.time() + data.get("expires_in", _TOKEN_EXPIRY_SECONDS)
    return token, expires_at


def get_access_token(account: str | None = None) -> str:
    """Get a valid access_token, refreshing from cache or API as needed."""
    creds = config.load_credentials()
    if not creds:
        raise TokenError(
            "未配置公众号凭证。请先运行: musespiral config set appid <APPID> secret <SECRET>"
        )
    appid, secret = creds["appid"], creds["secret"]

    cached = _read_cache()
    now = time.time()
    if cached and cached.get("access_token") and cached.get("expires_at", 0) - now > _REFRESH_WINDOW:
        return cached["access_token"]

    token, expires_at = _fetch_token(appid, secret)
    _write_cache(token, expires_at)
    return token
