"""OpenAPI 规格门（2026-11-24 W5-F4）：规格与路由清单必须一致。

规格是「128 端点、参数名两套」的可查化产物——它一旦与代码漂移就失去价值，
故用机器门锁住：生成结果必须覆盖路由自省清单的每个路径与方法。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MS = Path(__file__).resolve().parent.parent


def _spec() -> dict:
    out = subprocess.run(
        [sys.executable, str(MS / "scripts" / "gen_openapi.py")],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    return json.loads(out)


def test_spec_covers_every_route() -> None:
    """每条 GET/POST 路由都必须在规格里有对应条目。"""
    sys.path.insert(0, str(MS))
    from scripts.route_manifest import _action_routes, _get_routes

    spec = _spec()
    paths = spec["paths"]
    missing_get = [p for p in _get_routes() if "get" not in paths.get(p, {})]
    missing_post = [p for p in _action_routes() if "post" not in paths.get(p, {})]
    assert not missing_get, f"规格漏 GET: {missing_get}"
    assert not missing_post, f"规格漏 POST: {missing_post}"


def test_all_posts_require_confirm() -> None:
    """所有 POST 动作的 requestBody 必须声明 confirm 必填（人审闸门不可漏）。"""
    spec = _spec()
    bad = []
    for path, methods in spec["paths"].items():
        post = methods.get("post")
        if not post:
            continue
        req = post["requestBody"]["content"]["application/json"]["schema"].get("required") or []
        if "confirm" not in req:
            bad.append(path)
    assert not bad, f"这些 POST 未声明 confirm 必填: {bad}"


def test_param_aliases_documented() -> None:
    """article→path 别名必须在规格里可查（本轮实测三次猜错参数的教训）。"""
    spec = _spec()
    cover = spec["paths"]["/api/action/cover"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    props = cover["properties"]
    assert "path" in props and "article" in props, "别名 article 应作为 path 的兼容键出现在规格里"
    assert "别名" in props["article"]["description"]
