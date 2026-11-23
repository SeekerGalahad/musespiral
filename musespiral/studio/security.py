"""Studio 安全红线与共享底层（R2 自 studio.py 整块搬迁，函数体零变更）。

本模块是 studio 包的被依赖叶子：路由/动作/静态资源/服务器各层都从这里取
路径穿越防护、错误脱敏与通用工具 —— 放在叶子层是为了避免拆分后的循环
import（server→routing/actions→security 单向依赖）。

内容（原 studio.py 的常量 + `_StudioContext`/`_HttpError` + 「通用工具」节）:
  - 穿越防护：_resolve_inside/_resolve_file/_resolve_dir_arg/_rel
  - 脱敏：_sanitize_error/_mask_paths/_SECRET_RE/_PATH_TOKEN_RE/_MAX_ERROR_CHARS
  - confirm 校验：_require_confirm（动作人审闸门）
  - 通用工具：_json_bytes/_error_payload/_require_str/_query_str/_mtime_date
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterator

_JSON_CTYPE = "application/json; charset=utf-8"
_HTML_CTYPE = "text/html; charset=utf-8"
_MAX_ERROR_CHARS = 200  # 异常文本落响应前的截断上限（脱敏红线）

# 敏感参数值打码：access_token/appid/secret/api_key/token 等的 =value 形态
_SECRET_RE = re.compile(r"(?i)\b(access_token|appid|secret|api_key|token)=([^&\s'\"),]+)")

# F19 路径打码的候选 token：/ 开头绝对路径与 ~/ 开头家目录路径。
# 字符面取常见路径成分（\w 外加 .-+@ 与转义空格 \ ），遇空白/引号/括号/CJK 等
# 定界即止；lookbehind 排除非文件系统斜杠——URL（https:// 的 ://、foo/bar 的 o/）
# 与 ../ 相对路径都不命中。含空格的路径段（"My Secret Dir"）以 \ 空格计入，
# 防止打码在空格处截断泄漏尾段（第七期复核 low 修复）。
_PATH_TOKEN_RE = re.compile(
    r"~/[\w.@+\-]+(?:[ \\][\w.@+\-]+)*(?:/[\w.@+\-]+(?:[ \\][\w.@+\-]+)*)*"  # ~/ 开头（家目录相对形态）
    r"|(?<![\w:/.~])/(?:[\w.@+\-]+(?:[ \\][\w.@+\-]+)*/)+[\w.@+\-]+(?:[ \\][\w.@+\-]+)*"  # / 开头绝对路径
)


@dataclass(frozen=True)
class _StudioContext:
    """Server 上下文：工作目录（动作产物/日志/相对路径解析都以它为边界）。"""

    root: Path
    db: str | Path | None = None  # 集成阶段预留；§15.2 契约当前无 DB 端点


class _HttpError(Exception):
    """路由层 HTTP 错误 —— handler 统一转 JSON 响应（绝不吐 HTML traceback）。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---- 通用工具 ----


def _json_bytes(obj: Any) -> bytes:
    """JSON 序列化（frontmatter 里的 date 等非 JSON 类型按字符串落盘）。"""
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


def _mask_paths(text: str, root: Path | None) -> str:
    """错误文本路径打码（§16 F19）：root 外路径 → <path>，root 内 → 相对形式。

    实现口径（设计允许「root 前缀替换为相对形式或保留」，此处选相对形式）：
    - root 内路径去掉绝对前缀保留文件名定位（如 sub/a.md）——本地单用户
      工具，root 本身经 /api/meta 本就对使用者可见，文件名有排障价值；
    - root 外一律 <path>，含 ~ 家目录形态（如 ~/.musespiral/credentials.json）；
    - 判定前双方都 resolve：macOS /tmp 符号链接（/tmp ↔ /private/tmp）下，
      未解析形态的 root 内路径也能正确归入「root 内」。
    """
    if root is None:
        return text  # 无边界可用时不猜测（当前调用方恒传 ctx.root）

    def _sub(match: re.Match[str]) -> str:
        try:
            candidate = Path(match.group(0)).expanduser().resolve()
        except (OSError, ValueError):
            return "<path>"  # 解析不了的按越界处理（保守打码）
        if candidate.is_relative_to(root):
            return candidate.relative_to(root).as_posix() or "."
        return "<path>"

    return _PATH_TOKEN_RE.sub(_sub, text)


def _sanitize_error(err: BaseException, root: Path | None = None) -> str:
    """异常 → 可读错误文本：敏感值打码 + root 外路径打码 + 截断（§15.3/F19）。"""
    text = str(err).strip() or err.__class__.__name__
    text = _SECRET_RE.sub(r"\1=***", text)
    text = _mask_paths(text, root)
    if len(text) > _MAX_ERROR_CHARS:
        text = text[:_MAX_ERROR_CHARS] + "…"
    return text


def _error_payload(err: BaseException, root: Path | None = None) -> dict[str, Any]:
    return {"ok": False, "error": _sanitize_error(err, root)}


def _rel(root: Path, path: Path) -> str:
    """root 相对路径（posix 分隔符，供前端拼回 query）。

    越界（符号链接指向 root 外等）转 400 —— 不让 ValueError 冒成 500，
    也避免异常文本回显 root 外的绝对路径。
    """
    try:
        return path.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        raise _HttpError(400, "路径越界（必须仍在工作目录内）") from None


def _resolve_inside(root: Path, rel: str) -> Path:
    """把 <rel> 解析为 root 内路径；绝对路径/非法字符/越界一律 400。"""
    raw = rel.strip()
    if not raw:
        raise _HttpError(400, "路径不能为空（需 root 内相对路径）")
    if "\x00" in raw:
        raise _HttpError(400, "路径含非法字符（NUL）")
    if Path(raw).is_absolute():
        raise _HttpError(400, "只接受 root 内相对路径（禁止绝对路径）")
    try:
        resolved = (root / raw).resolve()
    except (OSError, ValueError):
        raise _HttpError(400, f"路径非法: {raw}") from None
    if not resolved.is_relative_to(root):
        raise _HttpError(400, f"路径越界（必须仍在工作目录内）: {raw}")
    return resolved


def _resolve_file(root: Path, rel: str) -> Path:
    """同上，且要求命中实际文件（不存在 → 404）。"""
    path = _resolve_inside(root, rel)
    if not path.is_file():
        raise _HttpError(404, f"文件不存在: {rel}")
    return path


def _resolve_dir_arg(root: Path, raw: str) -> Path:
    """/api/articles?root=<dir> 的目录解析：root 相对路径或指向 root 内的绝对路径。

    §15.2 契约的 root 参数允许绝对目录（前端 ?root= 透传），但 §15.3 越界防护
    不放松 —— resolve 后仍在 root 内才放行，root 之外一律 400。
    """
    raw = raw.strip()
    if "\x00" in raw:
        raise _HttpError(400, "路径含非法字符（NUL）")
    if Path(raw).is_absolute():
        try:
            resolved = Path(raw).expanduser().resolve()
        except (OSError, ValueError):
            raise _HttpError(400, f"路径非法: {raw}") from None
        if not resolved.is_relative_to(root):
            raise _HttpError(400, f"root 参数越界（必须仍在工作目录内）: {raw}")
        return resolved
    return _resolve_inside(root, raw)


def _require_str(data: dict[str, Any], key: str, *, alias: str | None = None) -> str:
    """动作请求体的必填字符串字段（缺失/类型不对 → 400）。

    `alias`（2026-11-24 W1 参数规范批）：同一语义的两套历史命名——读面 GET
    用 `path`、写面 POST action 用 `article`（实测 6 处）。传 alias 即两键
    兼容（`key` 优先，alias 兜底），**响应错误文案只提 `key`**（规范名）。
    新增代码一律用规范名 `path`；`article` 作为别名至少保留一个版本。
    """
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        if alias is not None:
            alt = data.get(alias)
            if isinstance(alt, str) and alt.strip():
                return alt.strip()
        raise _HttpError(400, f"缺少必填字段 {key}（root 内相对路径/平台码）")
    return value.strip()


@contextmanager
def _http_400_on(*exc_types: type[BaseException]) -> Iterator[None]:
    """服务层可读校验错误 → 400 的统一转换件（actions/settings 八处同型
    `except X as err: raise _HttpError(400, str(err)) from None` 收编；
    行为零变更——错误文本 str(err) 原样、from None 链切断语义不变）。"""
    try:
        yield
    except exc_types as err:
        raise _HttpError(400, str(err)) from None


def _require_confirm(data: dict[str, Any]) -> None:
    """动作接口必须带 confirm:true（严格等于 JSON true；缺省/假值一律拒绝）。"""
    if data.get("confirm") is not True:
        raise _HttpError(400, "动作接口必须带 confirm:true（人审闸门：缺省拒绝执行）")


def _query_str(query: dict[str, list[str]], key: str) -> str | None:
    """取 query 参数（同键多次时取最后一个；空串视作缺省）。"""
    values = query.get(key) or []
    value = values[-1].strip() if values else ""
    return value or None


def _mtime_date(path: Path) -> str:
    return date.fromtimestamp(path.stat().st_mtime).isoformat()
