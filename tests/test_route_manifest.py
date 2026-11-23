"""路由清单一致性门（2026-11-24 W1 路由统一批）。

把「路由面」从**散落的字面量**变成**可对拍的清单**：自省脚本产出的事实
必须与契约锁（CONTRACT_GET_EXACT / CONTRACT_POST_EXACT）对得上——任何一端
漂移都红。这是后续「声明式路由表」重构的安全网：重构前后清单不变即行为不变。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.test_architecture import CONTRACT_GET_EXACT, CONTRACT_POST_EXACT

MS = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    import json

    out = subprocess.run(
        [sys.executable, str(MS / "scripts" / "route_manifest.py"), "--json"],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    return json.loads(out)


def test_get_literals_cover_contract() -> None:
    """自省到的 GET 字面量必须覆盖契约 GET 面（少一个=有路由漏在 if 链外）。"""
    m = _manifest()
    found = set(m["get_routes"])
    missing = sorted(CONTRACT_GET_EXACT - found)
    # 允许契约里有但自省抓不到的动态段占位（如 <run_id>）
    missing = [p for p in missing if "{" not in p and "<" not in p]
    assert not missing, f"契约 GET 面未被自省覆盖（真漏）: {missing}"


def test_action_literals_cover_post_contract() -> None:
    """自省到的 action 字面量覆盖契约 POST 面（动态段占位除外）。"""
    m = _manifest()
    found = set(m["action_routes"])
    missing = sorted(
        p for p in (CONTRACT_POST_EXACT - found) if "{" not in p and "<" not in p
    )
    assert not missing, f"契约 POST 面未被自省覆盖（真漏）: {missing}"


def test_dual_method_paths_are_known() -> None:
    """双态路径（GET+POST 共用）必须是已知的三个——新增双态要显式登记。

    双态是最容易出「方法语义漂移」的地方（server.py 有专门分支处理），
    本测试把集合钉死，新增时强制思考而不是顺手加。
    """
    m = _manifest()
    assert set(m["dual_method_paths"]) == {
        "/api/discover/star",
        "/api/materials/collections",
        "/api/pipelines",  # GET=清单 / POST=保存（门首跑实抓的第 4 个）
        "/api/sources",
    }
