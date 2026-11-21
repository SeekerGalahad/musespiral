"""Local preview server — render article in browser before publishing.

Starts a small HTTP server that serves the rendered HTML inside a
WeChat-width frame (max 677px). No API calls are made.

静态路由（§十三 F7-7b）: / 与 /preview 返回 srcdoc 包装页；其余路径按
文章目录解析静态文件（扩展名白名单）。srcdoc 内相对路径图片以包装页
URL 为 base，正好命中静态路由 —— 预览里本地图片不再裂图。
"""
from __future__ import annotations

import http.server
import socketserver
import threading
from html import escape as html_escape
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core.content.parser import parse_article
from .core.content.renderer import Renderer

# 静态服务扩展名白名单 → Content-Type
_STATIC_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
}


def render_to_html(
    article_path: str | Path,
    theme: str | None = None,
    code_theme: str | None = None,
    dark: bool = False,
) -> str:
    from . import config as cfg

    article = parse_article(article_path)
    t = theme or article.metadata.get("theme") or cfg.DEFAULT_THEME
    ct = code_theme or article.metadata.get("code_theme") or cfg.DEFAULT_CODE_THEME
    renderer = Renderer(theme_name=t, code_theme=ct, dark=dark)
    return renderer.render(article)


def _wrap_html(html: str, article_path: str | Path) -> str:
    """生成 srcdoc iframe 包装页（浏览器实际访问的预览页）。"""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>"
        "body{background:#f0f0f0;margin:0;padding:20px;font-family:sans-serif;}"
        ".frame{max-width:677px;margin:0 auto;background:white;"
        "box-shadow:0 0 20px rgba(0,0,0,.1);}"
        ".frame iframe{width:100%;min-height:800px;border:none;}"
        ".bar{max-width:677px;margin:0 auto 10px;color:#666;font-size:13px;}"
        "</style></head><body>"
        f"<div class='bar'>预览: {article_path} （宽度模拟公众号 677px）</div>"
        "<div class='frame'>"
        f'<iframe srcdoc="{html_escape(html, quote=True)}"></iframe>'
        "</div></body></html>"
    )


def _resolve_static(article_dir: Path, url_path: str) -> tuple[Path, str] | None:
    """把请求路径解析为文章目录内的白名单静态文件。

    返回 (文件路径, Content-Type)；扩展名不在白名单、路径越界（穿越
    文章目录）或文件不存在时返回 None（统一 404）。
    """
    rel = unquote(urlparse(url_path).path).lstrip("/")
    if not rel:
        return None
    ctype = _STATIC_TYPES.get(Path(rel).suffix.lower().lstrip("."))
    if ctype is None:
        return None
    try:
        resolved = (article_dir / rel).resolve()
    except (ValueError, OSError):
        return None  # %00 等非法路径字符——统一 404（第四期遗留 low 修复）
    if not resolved.is_relative_to(article_dir) or not resolved.is_file():
        return None
    return resolved, ctype


def _make_handler(article_dir: Path, wrapped: str) -> type[http.server.BaseHTTPRequestHandler]:
    """构造预览 Handler：/ 与 /preview 返回包装页，其余走静态路由。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in ("/", "/preview"):
                self._send(200, "text/html; charset=utf-8", wrapped.encode("utf-8"))
                return
            static = _resolve_static(article_dir, self.path)
            if static is None:
                self._send(404, "text/plain; charset=utf-8", b"not found")
                return
            file_path, ctype = static
            self._send(200, ctype, file_path.read_bytes())

        def _send(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    return Handler


def start_preview(article_path: str | Path, port: int = 8765, dark: bool = False) -> None:
    html = render_to_html(article_path, dark=dark)
    wrapped = _wrap_html(html, article_path)
    handler = _make_handler(Path(article_path).resolve().parent, wrapped)

    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"预览服务器已启动: http://localhost:{port}")
        print("按 Ctrl+C 停止")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止预览")
