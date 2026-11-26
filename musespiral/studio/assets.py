"""Studio 静态资源与元信息（R2 自 studio.py 搬迁；M1 前端多文件化加产物静态面）。

  - /asset 静态路由数据面：_asset_response（图片白名单 + root 边界）
  - /media/<rel> 静态面（B3 2026-11-26）：_media_static_response（双域
    白名单只读——文章工作区 + 素材仓 materials/ 子树；视频预览必需）
  - /assets/* 产物静态面：_studio_asset_response（M1 前端多文件化——JS/CSS
    白名单 + studio assets root 边界 + ETag/304，见 _ASSET_TYPES 注）
  - 预览图片 src 重写：_asset_url/_rewrite_local_images（iframe 专用）
    + 视频版 _media_url/_rewrite_local_videos（B3：:::video 块 src/poster
    → /media/<rel>）
  - /api/meta 数据面：_meta_payload（root/version/ai_configured，凭证不进响应）
  - ui.html 读取：_read_ui_html（importlib.resources，未打包时 None）
"""
from __future__ import annotations

import gzip
import hashlib
import io
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


# ── B3 多媒体静态面 /media/<rel>（2026-11-26，三线扩展设计 §3.4）────────
# 双域只读：①文章工作区（ctx.root）——视频文章引用的 assets/xxx.mp4、
# 编辑器插入的 media/xxx.png（预览 iframe 与素材卡消费）；②素材仓
# （~/.musespiral/materials/ 子树，精确前缀 materials/）——本地视频
# <id>.mp4 与首帧封面 <id>.poster.jpg（素材库抽屉 <video>、卡片缩略消费）。
# 为什么不像 /asset 只挂 query：视频/封面引用形态是「路径即身份」（前端
# src=/media/… 直拼），与 /assets/* 产物面同款 server 层旁路。安全三门
# 与 /asset 同纪律：rel 必须相对路径且 resolve 后落在允许域内（../、
# 绝对路径、NUL 一律 400）；后缀白名单 mp4/webm/mov/jpg/jpeg/png/webp/gif
# （svg 恒拒——同 _STATIC_TYPES 判例）；必须命中真实文件（404）。
# materials/ 前缀域额外锁死在 home/materials 子树内——home 下的
# credentials.json/ai.json 等敏感文件即便伪造媒体后缀也出不了
# 「materials/ 子树 + 白名单」双门。
_MEDIA_TYPES: dict[str, str] = {
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}
# 素材仓前缀域的第二基根判据：以 materials/ 开头的 rel 才尝试 home 基根
_MAT_PREFIX = "materials/"


def _media_static_response(ctx: _StudioContext, rel: str) -> tuple[int, str, Path]:
    """GET /media/<rel>：媒体文件（双域白名单只读——见 _MEDIA_TYPES 注）。

    返回 (200, Content-Type, 文件 Path)——**字节流由 server 层分块写出**
    （视频 GB 级不能整读进内存；图片顺带同路径，行为一致）。越界/非法
    400、白名单外/缺失 404（_HttpError，表现层统一 JSON 化）。"""
    raw = rel.strip()
    if not raw:
        raise _HttpError(400, "路径不能为空（需媒体相对路径）")
    if "\x00" in raw or Path(raw).is_absolute():
        raise _HttpError(400, "只接受相对路径（禁止绝对路径）")
    suffix = Path(raw).suffix.lower().lstrip(".")
    ctype = _MEDIA_TYPES.get(suffix)
    if ctype is None:
        raise _HttpError(
            404,
            f"不是可服务的媒体类型（{'/'.join(_MEDIA_TYPES)}；svg 恒拒）: .{suffix}",
        )
    from .. import config  # 函数体内导入（导入期解耦）

    # 域一：文章工作区（_resolve_file 自带 400 越界 / 404 不存在）
    try:
        return 200, ctype, _resolve_file(ctx.root, raw)
    except _HttpError as err:
        if not (err.code == 404 and raw.startswith(_MAT_PREFIX)):
            raise
    # 域二：素材仓子树（rel 以 materials/ 开头才尝试；越界/缺失同款 400/404）
    mats = (config.home_dir() / "materials").resolve()
    try:
        target = (config.home_dir() / raw).resolve()
    except (OSError, ValueError):
        raise _HttpError(400, f"路径非法: {raw}") from None
    if not target.is_relative_to(mats):
        raise _HttpError(400, f"路径越界（必须仍在素材仓 materials/ 内）: {raw}")
    if not target.is_file():
        raise _HttpError(404, f"文件不存在: {raw}")
    return 200, ctype, target


# 视频块（:::video 容器，renderer 产物）的本地 src/poster 预览重写——
# _asset_url 的视频版：白名单换 _MEDIA_TYPES、URL 形态换 /media/<rel>
def _media_url(root: Path, article_path: Path, src: str) -> str | None:
    """视频/封面 src → /media/<root 内相对路径>；外链/绝对/白名单外 None。"""
    raw = src.strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme or parsed.netloc or raw.startswith("//"):
        return None
    if Path(raw).is_absolute():
        return None
    try:
        target = (article_path.parent / raw).resolve()
    except (OSError, ValueError):
        return None
    if not target.is_relative_to(root) or not target.is_file():
        return None
    if target.suffix.lower().lstrip(".") not in _MEDIA_TYPES:
        return None
    # safe="/"：路径分隔符保持字面（%2F 形态浏览器会按需再编码，可读性差），
    # 仅 CJK/空格等真不安全字符走百分号编码
    return "/media/" + quote(_rel(root, target), safe="/")


_VIDEO_TAG_RE = re.compile(r"<video\b[^>]*>", re.IGNORECASE)
_VIDEO_ATTR_RE = re.compile(r'\b(src|poster)="([^"]*)"', re.IGNORECASE)


def _rewrite_local_videos(html: str, root: Path, article_path: Path) -> str:
    """iframe 预览专用：视频块 src/poster 重写为 /media/<rel>（img 走既有
    _rewrite_local_images 的 /asset——两族互补，发布上传仍用原始相对路径）。"""
    def _tag_repl(match: re.Match[str]) -> str:
        def _attr_sub(am: re.Match[str]) -> str:
            url = _media_url(root, article_path, am.group(2))
            if url is None:
                return am.group(0)
            return f'{am.group(1)}="{url}"'

        return _VIDEO_ATTR_RE.sub(_attr_sub, match.group(0))

    return _VIDEO_TAG_RE.sub(_tag_repl, html)


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


# ── 静态服务 gzip（五角色评审问题 12，2026-11-22）────────────────────────
# 只对 >1KB 的文本资源（js/css/html）压缩，小文件/二进制直传；压缩结果按
# (ctype, sha256(body)) 缓存，同一内容只压一次（本地工具首屏 assets 反复取，
# 不重复压缩 ui.js/core.js 这类大文件）。mtime=0 保证压缩字节确定性。
_GZIP_MIN_BYTES = 1024
_GZIP_TEXT_MIMES = frozenset({"text/css", "text/html", "application/javascript"})
_GZIP_CACHE: dict[tuple[str, str], bytes] = {}


def _accepts_gzip(value: str | None) -> bool:
    """Accept-Encoding 是否含 gzip（`gzip;q=0` 视为拒绝）。"""
    if not value or "gzip" not in value.lower():
        return False
    for token in value.lower().split(","):
        parts = token.split(";")
        if parts[0].strip() == "gzip":
            return not (len(parts) > 1 and parts[1].strip().startswith("q=0"))
    return False


def _gzip_body(ctype: str, body: bytes) -> bytes | None:
    """压缩 >1KB 文本体返回 gzip 字节；非文本/小文件返回 None=直传。"""
    mime = ctype.split(";", 1)[0].strip().lower()
    if len(body) <= _GZIP_MIN_BYTES or not (
        mime in _GZIP_TEXT_MIMES or mime.startswith("text/")
    ):
        return None
    key = (mime, hashlib.sha256(body).hexdigest())
    cached = _GZIP_CACHE.get(key)
    if cached is not None:
        return cached
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(body)
    compressed = buf.getvalue()
    _GZIP_CACHE[key] = compressed
    return compressed


def _maybe_gzip(
    ctype: str,
    body: bytes,
    accept_encoding: str | None,
    extra_headers: tuple[tuple[str, str], ...] = (),
) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    """静态面统一入口：客户端接受 gzip 且为 >1KB 文本才压缩，加 Content-Encoding/Vary。

    ETag 基于未压缩内容（mtime+size），压缩与直传共用同一 ETag；304 空体不压缩。
    """
    if _accepts_gzip(accept_encoding):
        gz = _gzip_body(ctype, body)
        if gz is not None:
            return gz, extra_headers + (
                ("Content-Encoding", "gzip"),
                ("Vary", "Accept-Encoding"),
            )
    return body, extra_headers


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
