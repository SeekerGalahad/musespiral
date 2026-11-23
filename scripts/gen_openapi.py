#!/usr/bin/env python3
"""OpenAPI 3.1 规格导出（2026-11-24 W5-F4）。

从**路由自省清单**（scripts/route_manifest.py 同一事实源）+ 契约锁生成规格：
不手写、不解析 handler 内部（参数只描述已知必填项）。目的=让「128 端点、
参数名两套」这件事变成可查的机器可读规格（本轮实测三次猜错参数的根因）。

用法：
    python3 scripts/gen_openapi.py            # 打印 JSON 到 stdout
    python3 scripts/gen_openapi.py --apply    # 写入 docs/openapi.json
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from scripts.route_manifest import _action_routes, _get_routes  # noqa: E402

MS = pathlib.Path(__file__).resolve().parent.parent
OUT = MS / "docs" / "openapi.json"

# 已知必填查询/请求参数（人工维护的最小集：只列被实测验证过的必填项；
# 其余标为 optional/unknown —— 规格的价值在于「可查」，不在于穷尽）
GET_PARAMS = {
    "/api/article": ["path"],
    "/api/article/body": ["path"],
    "/api/article/html": ["path"],
    "/api/article/pack": ["path"],
    "/api/article/review": ["path", "note"],
    "/api/article/diff": ["path"],
    "/api/annotations": ["path"],
    "/api/materials/detail": ["id"],
    "/api/materials/file": ["id"],
    "/api/materials/iterations": ["id"],
    "/api/materials/iterations/diff": ["id"],
    "/api/materials/search": ["q"],
    "/api/booksource/one": ["url"],
    "/api/compose/session": ["slug"],
    "/api/book/info": ["sourceUrl", "bookUrl"],
    "/api/book/toc": ["sourceUrl", "bookUrl"],
    "/api/book/original": ["path"],
    "/api/media/preview": ["path"],
    "/api/tts/file": ["path"],
    "/api/visual/design": ["path"],
}
# 动作请求体必填参数（规范名优先；alias 列在描述里）
ACTION_PARAMS = {
    "/api/action/cover": {"required": ["path"], "aliases": {"article": "path"}},
    "/api/action/adapt": {"required": ["path", "platform"], "aliases": {"article": "path"}},
    "/api/action/pack": {"required": ["path", "platform"], "aliases": {"article": "path"}},
    "/api/action/refine": {"required": ["id", "action"], "aliases": {}},
    "/api/action/quality_loop": {"required": ["path"], "aliases": {"article": "path"}},
    "/api/action/title_ab": {"required": ["path", "mode"], "aliases": {"article": "path"}},
    "/api/compose/new": {"required": ["slug"], "aliases": {}},
    "/api/compose/add": {"required": ["slug", "path|material_id|article_slug(恰一)"], "aliases": {}},
    "/api/compose/decide": {"required": ["slug", "round", "block", "action"], "aliases": {}},
    "/api/compose/generate": {"required": ["slug"], "aliases": {}},
    "/api/compose/adopt": {"required": ["slug"], "aliases": {}},
    "/api/booksource/import": {"required": ["text_or_url"], "aliases": {}},
    "/api/booksource/import_apply": {"required": ["text_or_url"], "aliases": {}},
    "/api/book/search": {"required": ["key"], "aliases": {}},
    "/api/sources/verify": {"required": ["id"], "aliases": {}},
    "/api/sources/enabled": {"required": ["id", "enabled"], "aliases": {}},
    "/api/materials/triage": {"required": ["id"], "aliases": {}},
    "/api/materials/collections": {"required": ["action"], "aliases": {}},
    "/api/action/bookengine_demo": {"required": [], "aliases": {}},
    "/api/pipelines": {"required": ["id"], "aliases": {}},
}


def _op(path: str, method: str) -> dict:
    is_action = path.startswith("/api/action/") or path in ACTION_PARAMS
    if method == "get":
        params = GET_PARAMS.get(path)
        op: dict = {
            "summary": f"GET {path}",
            "responses": {"200": {"description": "OK（JSON 或字节流）"}},
        }
        if params:
            op["parameters"] = [
                {"name": p, "in": "query", "required": True, "schema": {"type": "string"}}
                for p in params
            ]
        return op
    spec = ACTION_PARAMS.get(path, {"required": [], "aliases": {}})
    required = spec.get("required", [])
    aliases = spec.get("aliases", {})
    desc = f"POST {path}（动作接口，必须带 confirm:true）"
    if aliases:
        desc += "；参数别名：" + "、".join(f"{k}→{v}" for k, v in aliases.items())
    body: dict = {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["confirm"] + [r for r in required if "|" not in r],
                    "properties": {
                        "confirm": {"type": "boolean", "description": "人审闸门，必须 true"},
                        **{r: {"type": "string"} for r in required},
                        **{a: {"type": "string", "description": f"别名，等价 {v}"} for a, v in aliases.items()},
                    },
                }
            }
        },
    }
    return {
        "summary": desc,
        "requestBody": body,
        "responses": {"200": {"description": "OK（{ok:true} 或 {ok:false} 业务层语义）"}},
    }


def build() -> dict:
    gets = _get_routes()
    acts = _action_routes()
    paths: dict = {}
    for p in gets:
        paths.setdefault(p, {})["get"] = _op(p, "get")
    for p in acts:
        paths.setdefault(p, {})["post"] = _op(p, "post")
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "musespiral Studio local API",
            "version": "0.5.2",
            "description": "本地回环 API（127.0.0.1:8766）。全部 POST 动作需 confirm:true。"
                           "本规格由 scripts/gen_openapi.py 从路由自省清单生成。",
        },
        "servers": [{"url": "http://127.0.0.1:8766"}],
        "paths": dict(sorted(paths.items())),
    }


def main() -> int:
    spec = build()
    text = json.dumps(spec, ensure_ascii=False, indent=1)
    if "--apply" in sys.argv:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text + "\n", encoding="utf-8")
        n_get = sum(1 for v in spec["paths"].values() if "get" in v)
        n_post = sum(1 for v in spec["paths"].values() if "post" in v)
        print(f"已写入 {OUT}：{len(spec['paths'])} 路径（GET {n_get} / POST {n_post}）")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
