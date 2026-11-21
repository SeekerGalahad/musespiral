"""版本池读面与恢复（E-6，L3；正典=《musespiral-专业创作者视角深度评审与改进-2026-11-18》§3.7）。

数据面**全部**来自 `infra/storage/filesystem` 的 F43 版本池（单一持有者：
`<article>.md.bak.<YYYYMMDD-HHMMSS>` 命名约定、滚动保留 N、旧无时间戳 `.bak`
读侧兼容）——本服务不新建索引、不复制命名逻辑，只做三件事：

  - `list_versions(path)`：版本清单（**首项为「当前版」**，其后池内新→旧）——
    对比的常见形态是「当前 vs 某一版」，当前版必须可选；
  - `diff_versions(path, v1, v2)`：读两版全文 → `core.version.diff` 出结构化
    hunks + HTML（红删绿增）；
  - `restore_version(path, vid)`：**恢复前先 confirm**（端点的 confirm:true 闸门），
    委托 `restore_backup`——当前版先入池，故「恢复可再恢复」。

版本 id 口径（前端下拉的 value，也是 API 的 v1/v2）：
  - `"current"`：磁盘上的当前正文；
  - `"YYYYMMDD-HHMMSS"`：池内时间戳版本（文件名即可见真相）；
  - `"legacy"`：F43 之前的旧单版本 `<article>.md.bak`（池内恒排尾部）。
id 只与 `list_backups` 的返回值比对（**绝不由调用方串拼路径**）。

分层纪律：本模块不 import musespiral.studio（服务层不知道 HTTP），也不做路径穿越
校验——文章路径由表现层 `_resolve_file` 解析后传进来。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.version.diff import diff_result, render_diff_html, summarize
from ..infra.storage.filesystem import BackupEntry, list_backups, restore_backup

# 当前版在选择器里的 id（不是池成员，restore 它无意义）
CURRENT_ID = "current"
# 旧单版本（无时间戳 .md.bak）的 id
LEGACY_ID = "legacy"


class VersionError(ValueError):
    """版本业务错误（可读中文；表现层转 400 / ok:false）。"""


def _format_ts(ts: str) -> str:
    """池时间戳 `YYYYMMDD-HHMMSS` → 展示形态 `YYYY-MM-DD HH:MM:SS`。"""
    return f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}:{ts[13:15]}"


def _entry_id(entry: BackupEntry) -> str:
    return LEGACY_ID if entry.ts is None else entry.ts


def _entry_label(entry: BackupEntry) -> str:
    return "旧单版本" if entry.ts is None else _format_ts(entry.ts)


def _decode(raw: bytes, name: str) -> str:
    """严格 UTF-8 解码（errors 缺省即 strict——U+FFFD 不可逆替换会掩盖坏文件）。

    版本池可能含二进制/损坏内容（用户手工塞进来的 .bak）：报可读错误而不是
    静默显示一堆问号。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise VersionError(f"{name} 不是 UTF-8 文本，无法对比（可能不是 Markdown 版本）") from None


def _read_current(path: Path) -> tuple[str, int]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise VersionError(f"读取当前版失败：{exc.strerror or exc.__class__.__name__}") from None
    return _decode(raw, "当前版"), len(raw)


def _entry_for(path: Path, vid: str) -> BackupEntry:
    """按 id 在池内定位（id 只做比对，绝不由它拼路径）。"""
    for entry in list_backups(path):
        if _entry_id(entry) == vid:
            return entry
    raise VersionError(f"版本不存在: {vid}（先 GET /api/article/diff 取版本清单）")


def list_versions(path: Path) -> list[dict[str, Any]]:
    """版本清单：当前版 + 池内各版（新→旧）。

    每项 `{id, label, ts, summary, bytes, current, available}`：`summary` 取首行
    非空非界定符行（列表里认版用），`bytes` 为整文件字节数，`available` 恒 True
    （列出来就能读/恢复——读失败会在 diff/restore 时报可读错误）。
    """
    text, size = _read_current(path)
    items: list[dict[str, Any]] = [
        {
            "id": CURRENT_ID,
            "label": "当前版",
            "ts": None,
            "summary": summarize(text),
            "bytes": size,
            "current": True,
            "available": True,
        }
    ]
    for entry in list_backups(path):
        try:
            size = entry.path.stat().st_size
        except OSError:  # 池文件被外部删/权限异常：仍列出（恢复时报错比静默消失诚实）
            size = 0
        items.append(
            {
                "id": _entry_id(entry),
                "label": _entry_label(entry),
                "ts": entry.ts,
                "summary": summarize(_safe_text(entry.path)),
                "bytes": size,
                "current": False,
                "available": True,
            }
        )
    return items


def _safe_text(path: Path, limit: int = 8192) -> str:
    """摘要用的容错读：只读头部、坏字节不报错（列表不该因某一版损坏而全空）。"""
    try:
        raw = path.read_bytes()[:limit]
    except OSError:
        return ""
    return raw.decode("utf-8", errors="replace")


def read_version(path: Path, vid: str) -> tuple[str, str, int]:
    """读一版 → (全文, 展示标签, 字节数)。`current` 读磁盘，其余读池。"""
    if vid == CURRENT_ID:
        text, size = _read_current(path)
        return text, "当前版", size
    entry = _entry_for(path, vid)
    try:
        raw = entry.path.read_bytes()
    except OSError as exc:
        raise VersionError(f"读取版本失败：{exc.strerror or exc.__class__.__name__}") from None
    return _decode(raw, _entry_label(entry)), _entry_label(entry), len(raw)


def diff_versions(
    path: Path,
    v1: str,
    v2: str,
    context: int = 3,
) -> dict[str, Any]:
    """两版对比 → 结构化结果 + 服务端渲染 HTML。

    `v1` 为旧侧（删除线绯红）、`v2` 为新侧（新增竹绿）。两版相同 → `identical`
    为真且 html 为一句提示（不渲染空骨架）。
    """
    if v1 == v2:
        raise VersionError("请选择两个不同的版本再对比")
    old_text, old_label, old_size = read_version(path, v1)
    new_text, new_label, new_size = read_version(path, v2)
    result = diff_result(old_text, new_text, old_label, new_label, context=context)
    return {
        "v1": {"id": v1, "label": old_label, "bytes": old_size},
        "v2": {"id": v2, "label": new_label, "bytes": new_size},
        "added": result.added,
        "removed": result.removed,
        "identical": result.identical,
        "hunks": [h.to_dict() for h in result.hunks],
        "html": render_diff_html(result, old_label, new_label),
    }


def restore_version(path: Path, vid: str) -> dict[str, Any]:
    """把池内某一版恢复成当前版（**调用方必须先过 confirm 闸门**）。

    委托 `restore_backup`：先把当前版入池（同秒撞名覆写的边界见该函数
    docstring），再原子覆盖原文并沿用原文权限位；被恢复的那一版不消耗
    （池只增+滚动）。`current` 不属于可恢复集合 → 可读拒绝。
    """
    if vid == CURRENT_ID:
        raise VersionError("当前版无需恢复（请选择池内的一版）")
    entry = _entry_for(path, vid)
    restore_backup(path, entry)
    return {
        "id": vid,
        "label": _entry_label(entry),
        "bytes": path.stat().st_size,
    }
