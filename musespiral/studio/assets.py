"""Studio 静态资源与元信息（R2 自 studio.py 搬迁；M1 前端多文件化加产物静态面）。

  - /asset 静态路由数据面：_asset_response（图片白名单 + root 边界）
  - /assets/* 产物静态面：_studio_asset_response（M1 前端多文件化——JS/CSS
    白名单 + studio assets root 边界 + ETag/304，见 _ASSET_TYPES 注）
  - 预览图片 src 重写：_asset_url/_rewrite_local_images（iframe 专用）
  - /api/meta 数据面：_meta_payload（root/version/ai_configured，凭证不进响应）
  - ui.html 读取：_read_ui_html（importlib.resources，未打包时 None）
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .. import __version__
from ..infra.ai import ai_configured
from .security import _HttpError, _StudioContext, _query_str, _rel, _resolve_file

_UI_RESOURCE = ("studio", "ui.html")

# 预览静态图片白名单（只回放图片，不服务任意文件）。
# 不含 svg：直接导航到 SVG 会在同源执行脚本（可绕过 confirm 直发动作）。
_STATIC_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# <img ... src="..."> —— iframe 预览重写相对图片 src 用（与 checklist 同正则口径）
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.IGNORECASE)


def _asset_url(root: Path, article_path: Path, src: str) -> str | None:
    """文章内本地图片 src → `/asset?path=<rel>`；外链/绝对路径/白名单外返回 None。"""
    raw = src.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or raw.startswith("//"):
        return None  # 外链 / data: / 协议相对：不是本地文件，保持原样
    if Path(raw).is_absolute():
        return None  # 绝对文件系统路径不是可服务的 root 相对引用
    try:
        target = (article_path.parent / raw).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(root) or not target.is_file():
        return None
    if target.suffix.lower().lstrip(".") not in _STATIC_TYPES:
        return None
    return "/asset?path=" + quote(_rel(root, target), safe="")


def _rewrite_local_images(html: str, root: Path, article_path: Path) -> str:
    """iframe 预览专用：把相对图片 src 重写为 /asset，避免 /api/cover.png 裂图。

    只改渲染结果的副本；publish 上传仍用原始相对路径（uploader 自己读盘）。
    """
    def _replace(match: re.Match[str]) -> str:
        url = _asset_url(root, article_path, match.group(1))
        if url is None:
            return match.group(0)
        start, end = match.start(1) - match.start(0), match.end(1) - match.start(0)
        return match.group(0)[:start] + url + match.group(0)[end:]

    return _IMG_SRC_RE.sub(_replace, html)


def _asset_response(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET /asset?path=<rel>：预览 iframe 用本地图片（图片白名单 + root 边界）。"""
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    path = _resolve_file(ctx.root, rel)
    ctype = _STATIC_TYPES.get(path.suffix.lower().lstrip("."))
    if ctype is None:
        raise _HttpError(404, f"不是可预览的图片类型: {rel}")
    try:
        body = path.read_bytes()
    except OSError:
        raise _HttpError(404, f"图片读取失败: {rel}") from None
    return 200, ctype, body


# M1 前端多文件化（2026-11-18，正典《musespiral-前端多文件化设计》§四）：studio
# 自有产物（assets/core.js|css）的静态面白名单。与 _STATIC_TYPES（预览图片）
# 不同域：只收构建产物 JS/CSS——svg 恒拒（同源脚本执行面，同 _STATIC_TYPES
# 不收 svg 的既有理由），其余后缀一律 404。
_ASSET_TYPES: dict[str, str] = {
    "js": "application/javascript",
    "css": "text/css",
}

# 产物目录：musespiral/studio/assets/（pyproject package-data 已含 studio/assets/*，
# 仓库开发态与 pip 安装态都是磁盘真实目录，故按包位置解析——与 _read_ui_html
# 的 importlib.resources 读取并行不悖：ETag 需要 mtime/size，取真实路径最直接）。
_STUDIO_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _studio_asset_response(
    rel: str, if_none_match: str | None = None
) -> tuple[int, str, bytes, tuple[tuple[str, str], ...]]:
    """GET /assets/<rel>：studio 前端产物静态面（M1 前端多文件化）。

    安全四门（_asset_response 同款纪律 + _resolve_inside 穿越拒绝）：
      1) root 边界：_resolve_file(_STUDIO_ASSETS_DIR, rel)——绝对路径/NUL/
         ``../`` 穿越一律 400，root 内不存在/非文件 404；
      2) 后缀白名单：仅 .js/.css（Content-Type 按表），白名单外（如 .svg）404；
      3) ETag=文件 mtime+size：If-None-Match 命中回 304 空体（本地工具首屏
         assets 不重复传输），未命中回 200 + ``Cache-Control: no-cache``
         （每次用 ETag revalidate——改完立即生效比重缓存重要，正典 §四）。
    返回 (状态码, Content-Type, body, extra_headers)。
    """
    path = _resolve_file(_STUDIO_ASSETS_DIR, rel)
    ctype = _ASSET_TYPES.get(path.suffix.lower().lstrip("."))
    if ctype is None:
        raise _HttpError(404, f"不是可服务的资源类型: {rel}")
    try:
        body = path.read_bytes()
        stat = path.stat()
    except OSError:
        raise _HttpError(404, f"资源读取失败: {rel}") from None
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
    if if_none_match is not None and etag in if_none_match:
        return 304, ctype, b"", (("ETag", etag),)
    return 200, ctype, body, (("ETag", etag), ("Cache-Control", "no-cache"))


def _meta_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/meta：{root, version, ai_configured}（§16 F19-2）。

    root 为绝对路径（前端 root chip 显示真实工作目录）；version 为 musespiral
    包版本；ai_configured 与 ai.ai_configured() 同口径（F25 起=base_url+
    model，api_key 可空——本地 Ollana）——只回布尔，ai.json 的任何配置
    内容都不进响应（§15.3 凭证永不进响应）。每次请求现读配置文件，无缓存。
    """
    return {
        "root": str(ctx.root),
        "version": __version__,
        "ai_configured": ai_configured(),
    }


def _read_ui_html() -> bytes | None:
    """读前端壳 musespiral/studio/ui.html（未打包时返回 None）。

    M1 起壳只含 HTML 骨架+视图内联+assets 引用，JS/CSS 由 /assets/* 另取
    （见 _studio_asset_response）。
    """
    try:
        return resources.files("musespiral").joinpath(*_UI_RESOURCE).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return None
