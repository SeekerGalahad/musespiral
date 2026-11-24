"""发布域包化 facade 对拍（W2-2，2026-11-24）。

7 个发布域服务从 services/ 平铺迁入 services/publish/ 包，旧路径保留薄 facade。
本门锁「facade 符号集 == 包内源模块符号集」——防 re-export 漂移（沿
materials/agent facade 判例）。任何一边增减符号都会红。
"""
from __future__ import annotations

import importlib
import types

NAMES = (
    "adapt_service", "cover_service", "pack_service", "publish_log_service",
    "publish_service", "site_service", "tts_service",
)


def _public(mod) -> set[str]:
    """公开符号集（排除 dunder 与 import 进来的模块对象）。"""
    out = set()
    for n in dir(mod):
        if n.startswith("__"):
            continue
        if isinstance(getattr(mod, n), types.ModuleType):
            continue
        out.add(n)
    return out


def test_facade_matches_source() -> None:
    """每个 facade 与包内源模块的符号集逐一相等。"""
    drift = []
    for name in NAMES:
        facade = importlib.import_module(f"musespiral.services.{name}")
        source = importlib.import_module(f"musespiral.services.publish.{name}")
        f_set, s_set = _public(facade), _public(source)
        missing = s_set - f_set
        extra = f_set - s_set
        if missing or extra:
            drift.append(f"{name}: 缺 {sorted(missing)} / 多 {sorted(extra)}")
    assert not drift, "facade 符号漂移:\n" + "\n".join(drift)


def test_package_reexports_all_domains() -> None:
    """包 __init__ 聚合了 7 个服务的符号（`from musespiral.services.publish import X` 可用）。"""
    pkg = importlib.import_module("musespiral.services.publish")
    for probe in ("build_pack", "build_pack_for", "cover_filename", "DEFAULT_COVER_NAME"):
        assert hasattr(pkg, probe), f"包应聚合 {probe}"
