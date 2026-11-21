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


def _fetch_token(appid: str, secret: str) -> tuple[str, float]:
    """Call stable_token and return (token, expires_at_epoch)."""
    url = f"{config.WECHAT_API_BASE}/cgi-bin/stable_token"
    resp = requests.post(
        url,
        json={
            "grant_type": "client_credential",
            "appid": appid,
            "secret": secret,
            "force_refresh": False,
        },
        timeout=15,
    )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise TokenError(f"获取 access_token 失败: {data}")
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
