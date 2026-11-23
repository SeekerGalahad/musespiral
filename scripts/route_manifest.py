#!/usr/bin/env python3
"""路由自省清单（2026-11-24 W1 路由统一批第一步：只读、零改动）。

目的：把三套并存的派发面（routing.py 的 GET if 链 / handlers 的 action 字典 /
actions.py 的 ROUTES 片段合并）汇成一份**可机读的路由清单**，作为后续
「声明式路由表」重构的事实基线与回归对拍锚。

只读纪律：本脚本不 import 服务、不改任何文件，只做源码静态解析。
用法：python3 scripts/route_manifest.py [--json]
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

STUDIO = pathlib.Path(__file__).resolve().parent.parent / "musespiral" / "studio"


def _get_routes() -> list[str]:
    """routing.py 的 `if path == "/api/..."` 字面量（GET 静态面）。"""
    src = (STUDIO / "routing.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.comparators) == 1:
            right = node.comparators[0]
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                if right.value.startswith("/api/") or right.value.startswith("/asset"):
                    out.append(right.value)
    return sorted(set(out))


def _action_routes() -> list[str]:
    """action 字典里的 "/api/..." 键（POST 动作面）。

    覆盖三处定义面（2026-11-24 门首跑实抓的漏点）：
      - studio/*.py 顶层（platform 等）
      - studio/handlers/*.py
      - studio/plugin_actions/*.py（ROUTES 片段：pipelines/plugins/segments 等）
    另含 _BINARY_POST_ROUTES / _POST_ROUTES 等非字典形态——由契约锁侧覆盖。
    """
    out: list[str] = []
    roots = list(STUDIO.glob("*.py")) + list((STUDIO / "handlers").glob("*.py")) \
        + list((STUDIO / "plugin_actions").glob("*.py"))
    for p in roots:
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k in node.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        if k.value.startswith("/api/"):
                            out.append(k.value)
    # 二进制旁路（server._BINARY_POST_ROUTES 的 frozenset 字面量）
    srv = (STUDIO / "server.py").read_text(encoding="utf-8")
    tree_s = ast.parse(srv)
    for node in ast.walk(tree_s):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "_BINARY_POST_ROUTES":
                    if isinstance(node.value, ast.Call):
                        for a in node.value.args:
                            # frozenset({"/x"}) → Set；frozenset("/x") 不适用
                            if isinstance(a, ast.Set):
                                for e in a.elts:
                                    if isinstance(e, ast.Constant) and isinstance(e.value, str):
                                        out.append(e.value)
    return sorted(set(out))


def main() -> int:
    gets = _get_routes()
    acts = _action_routes()
    both = sorted(set(gets) & set(acts))
    payload = {
        "get_route_literals": len(gets),
        "action_route_literals": len(acts),
        "union": len(set(gets) | set(acts)),
        "dual_method_paths": both,
        "get_routes": gets,
        "action_routes": acts,
    }
    if "--json" in sys.argv:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"GET 字面量 {len(gets)} | action 字面量 {len(acts)} | 并集 {len(set(gets)|set(acts))}")
        print(f"双态路径（GET+POST 共用）：{len(both)}")
        for p in both:
            print("  ", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
