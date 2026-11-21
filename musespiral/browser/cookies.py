"""Cookie 存取 — Playwright storage_state 原生格式（F16）。

文件落 config.home_dir()/cookies/<platform>.json（~/.musespiral，受 MUSESPIRAL_HOME
覆盖，仓外）——红线②：cookie 只存家目录，不进任何日志/报告/仓库。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import config


class CookieMissing(RuntimeError):
    """平台登录态缺失——先跑 `musespiral upload login --platform <code>` 扫码保存。"""


def _dir() -> Path:
    return config.home_dir() / "cookies"


def path(platform: str) -> Path:
    """平台 cookie 文件路径（家目录下，仓外）。"""
    return _dir() / f"{platform}.json"


def save(platform: str, state: dict[str, Any]) -> Path:
    """写 storage_state 到 <home>/cookies/<platform>.json（临时文件原子替换）。"""
    target = path(platform)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(target)
    return target


def load(platform: str) -> dict[str, Any]:
    """读平台 cookie；缺失抛 CookieMissing（提示先 login，可读错误）。"""
    target = path(platform)
    if not target.exists():
        raise CookieMissing(
            f"未找到 {platform} 的登录 cookie（{target}），"
            f"请先运行: musespiral upload login --platform {platform}"
        )
    return json.loads(target.read_text(encoding="utf-8"))


def has(platform: str) -> bool:
    return path(platform).exists()


def list() -> list[str]:
    """已保存登录态的平台列表（按文件名排序）。"""
    d = _dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.json"))
