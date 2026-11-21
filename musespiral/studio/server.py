"""Studio HTTP 服务器装配（R2 自 studio.py 搬迁；M1 前端多文件化加 /assets/* 静态面）。

  - _make_handler：closure 持有 _StudioContext 的 BaseHTTPRequestHandler
    （方法表仅 GET/POST，其余 405；错误统一 JSON，不吐 HTML traceback）
  - GET / 与 GET /assets/* 为 server 层特判（静态文件非 API 面；后者 ETag
    304 需读 If-None-Match 请求头），其余 GET 走 _GET_ROUTES 分派
  - make_server：ThreadingHTTPServer 装配（只绑 127.0.0.1，port=0 系统分配）
公开入口 make_server 由包 __init__ re-export（签名不变）。
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .actions import _ACTION_ROUTES
from .assets import _read_ui_html, _studio_asset_response
from .plugin_actions import (  # 三态裁决动态 POST（AI-1 批）+ 节点重生成（AI-3 批）
    resolve_adjudicate_post,
    run_adjudicate,
    run_regenerate,
)
from .routing import _GET_ROUTES, _get_response
from .security import (
    _HTML_CTYPE,
    _HttpError,
    _JSON_CTYPE,
    _StudioContext,
    _error_payload,
    _json_bytes,
    _require_confirm,
    _sanitize_error,
)

# 二进制旁路 POST 路由（第二十一期 F59）：body 为原始字节流非 JSON dict，
# confirm 走 query `?confirm=1` 特判（契约 §1.2 的形式修订——同文案同语义），
# 不经 _ACTION_ROUTES 的 JSON 动作分派；路由面锁（test_architecture
# CONTRACT_POST_EXACT）与 _ACTION_ROUTES 取并集对拍。
_BINARY_POST_ROUTES = frozenset({"/api/materials/upload"})


def _make_handler(ctx: _StudioContext) -> type[BaseHTTPRequestHandler]:
    """构造 Studio Handler（closure 持有上下文，与 preview._make_handler 同风格）。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = "musespiral-studio"

        # ---- 方法表：仅 GET/POST，其余一律 405（§15.3）----

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_DELETE(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_PATCH(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def do_HEAD(self) -> None:  # noqa: N802
            self._method_not_allowed()

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            """stdlib 对未声明方法（TRACE/CONNECT 等）走 501 —— 统一转 405 JSON。"""
            if int(code) == 501:
                self._send_json(405, {"ok": False, "error": "方法不允许（仅 GET/POST）"})
                return
            super().send_error(code, message, explain)

        # ---- 分派 ----

        def _handle(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query, keep_blank_values=True)
            try:
                if path == "/":
                    if method != "GET":
                        raise _HttpError(405, "方法不允许（仅 GET/POST）")
                    ui = _read_ui_html()
                    if ui is None:
                        raise _HttpError(404, "studio/ui.html 缺失（集成阶段 package-data 接线）")
                    self._send(200, _HTML_CTYPE, ui)
                    return
                if path.startswith("/assets/") and len(path) > len("/assets/"):
                    # M1 前端多文件化（2026-11-18）：壳的 assets/core.js|css
                    # 静态面——与 `/` 同为 server 层特判（静态文件非 API 面，
                    # 且 ETag 304 需读请求头 If-None-Match，不经 _get_response
                    # 的 (ctx,path,query) 签名）。安全四门见 _studio_asset_response。
                    if method != "GET":
                        raise _HttpError(405, "方法不允许（仅 GET/POST）")
                    result = _studio_asset_response(
                        path[len("/assets/") :], self.headers.get("If-None-Match")
                    )
                    self._send(*result)
                    return
                # 三态裁决（AI-1 批 2026-11-21，契约 §3.19 修订 3）+ 节点重
                # 生成（AI-3 批 2026-11-21）：动态段 POST（/api/pipelines/
                # runs/<run_id>/adopt|discard|regenerate）先于 GET 面判定
                # ——该路径族在 _RouteSet 前缀覆盖内，不先分流会被下方
                # 「GET 路径非 GET 即 405」语义吃掉（/api/discover/star
                # 双态先例的动态版）；confirm 闸门与 JSON body 走通用层
                # 同款。regenerate 是长动作（ks/aw 是 AI 节点）→ 提交 job
                # 返 {job_id}（前端轮询 jobs 面），与 adopt/discard 的同步
                # 翻态分流。
                if method == "POST":
                    hit = resolve_adjudicate_post(path)
                    if hit is not None:
                        action, rid = hit
                        data = self._read_json()
                        _require_confirm(data)
                        if action == "regenerate":
                            self._send_json(200, run_regenerate(ctx, data, rid))
                        else:
                            self._send_json(200, run_adjudicate(ctx, data, rid, action))
                        return
                if path in _GET_ROUTES:
                    # 档A /api/discover/star 为 GET/POST 双态路径：方法不符时
                    # 让落到下方动作分支（其余 GET 路径维持 405 语义不变）
                    if method != "GET" and path not in _ACTION_ROUTES:
                        raise _HttpError(405, "方法不允许（仅 GET/POST）")
                    if method == "GET":
                        result = _get_response(ctx, path, query)
                        extra = result[3] if len(result) > 3 else ()
                        self._send(result[0], result[1], result[2], extra)
                        return
                if path in _BINARY_POST_ROUTES:
                    # F59 二进制旁路：upload 的 body 是原始字节流（唯一动
                    # server 分派的点，正典 §三）——confirm query 特判
                    if method != "POST":
                        raise _HttpError(405, "方法不允许（仅 GET/POST）")
                    self._send_json(200, self._handle_binary_upload(ctx, query))
                    return
                if path in _ACTION_ROUTES:
                    if method != "POST":
                        raise _HttpError(405, "方法不允许（动作接口仅 POST）")
                    data = self._read_json()
                    _require_confirm(data)
                    self._send_json(200, _ACTION_ROUTES[path](ctx, data))
                    return
                raise _HttpError(404, f"未知路由: {path}")
            except _HttpError as err:
                self._send_json(err.code, _error_payload(err, ctx.root))
            except Exception as err:  # 兜底：JSON 可读错误，不吐 HTML traceback
                self._send_json(500, _error_payload(err, ctx.root))

        # ---- 请求体 ----

        def _handle_binary_upload(self, ctx: _StudioContext, query: dict[str, list[str]]) -> dict:
            """POST /api/materials/upload（F59 二进制旁路，唯一非 JSON 动作面）。

            confirm 闸门 query 特判（?confirm=1 严格等于；缺省/假值 → 400 同
            文案同语义——契约 §1.2 的形式修订，见 musespiral-API契约 §3.2-3）；
            Content-Length 预检：缺失/非整数/空 → 400；>8MiB **未读 body 即
            拒**（400，不消耗请求体）；服务层校验失败（格式/枚举）→ 400
            可读错误（过脱敏管道）。
            """
            from ..infra import media_store
            from ..services import materials_service

            if query.get("confirm") != ["1"]:
                raise _HttpError(400, "动作接口必须带 confirm:true（人审闸门：缺省拒绝执行）")
            length = self.headers.get("Content-Length")
            if not length:
                raise _HttpError(400, "请求体为空（上传接口需二进制 body）")
            try:
                size = int(length)
            except ValueError:
                raise _HttpError(400, "Content-Length 非法") from None
            if size <= 0:
                raise _HttpError(400, "请求体为空（上传接口需二进制 body）")
            if size > media_store.MAX_UPLOAD_BYTES:
                raise _HttpError(
                    400, "文件超过 8MiB 单文件上限（Content-Length 预检未读 body 即拒）"
                )
            data = self.rfile.read(size)
            name = (query.get("name") or [""])[-1]
            license_ = (query.get("license") or [""])[-1] or "UNKNOWN"
            note = (query.get("note") or [""])[-1]
            try:
                result = materials_service.add_uploaded(
                    data, name=name, license=license_, note=note
                )
            except ValueError as err:
                raise _HttpError(400, _sanitize_error(err, ctx.root)) from None
            return {"ok": True, **result}


        def _read_json(self) -> dict[str, Any]:
            """读 JSON 对象请求体（缺失/非法/非对象一律 400）。"""
            length = self.headers.get("Content-Length")
            if not length:
                raise _HttpError(400, "请求体为空（动作接口需 JSON body）")
            try:
                size = int(length)
            except ValueError:
                raise _HttpError(400, "Content-Length 非法") from None
            if size <= 0:
                raise _HttpError(400, "请求体为空（动作接口需 JSON body）")
            raw = self.rfile.read(size)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _HttpError(400, "请求体不是合法 JSON") from None
            if not isinstance(data, dict):
                raise _HttpError(400, "请求体必须是 JSON 对象")
            return data

        # ---- 响应 ----

        def _send(
            self,
            code: int,
            ctype: str,
            body: bytes,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for key, value in extra_headers:  # F60 素材文件服务：immutable 缓存头等
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 客户端提前断开不影响服务端

        def _send_json(self, code: int, obj: Any) -> None:
            self._send(code, _JSON_CTYPE, _json_bytes(obj))

        def _method_not_allowed(self) -> None:
            self._send_json(405, {"ok": False, "error": "方法不允许（仅 GET/POST）"})

        def log_message(self, *args: Any) -> None:
            pass  # 本地工具：不刷访问日志

    return Handler


def make_server(root: str | Path, port: int = 0, db: str | Path | None = None) -> ThreadingHTTPServer:
    """构造 Studio HTTP 服务器（未启动，调用方 serve_forever/线程自起）。

    绑定 127.0.0.1（§15.3 仅回环）；port=0 由系统分配（CLI 与测试共用）。
    root 为工作目录 —— 文章扫描/动作产物/日志/全部相对路径解析的边界。
    db 供集成阶段预留（§15.2 契约当前无 DB 端点），当前不消费。
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"工作目录不存在: {root_path}")
    ctx = _StudioContext(root=root_path, db=db)
    return ThreadingHTTPServer(("127.0.0.1", port), _make_handler(ctx))
