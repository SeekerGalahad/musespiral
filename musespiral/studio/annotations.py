"""Studio 批注 sidecar 与正文切分/保存（Phase A 自 routing.py 拆出，行为零变更）。

  - 正文数据面（R7）：_read_article_text（禁用换行翻译，CRLF 原样往返）/
    _split_head_body（frontmatter 头字节不动的同一刀法，读出→不改→写回字节
    恒等）/ _save_body（保存前当前版入滚动版本池 + mkstemp+os.replace 原子
    落盘，供 actions 的 save 动作复用）
  - 批注 sidecar <stem>.annotations.json：_read_annotations 严格读（坏文件
    拒绝覆写）/ _load_annotations 容错读（GET 不中断编辑）/ _write_annotations
    整表原子写 / _mutate_annotations 锁内读改写唯一入口（ThreadingHTTPServer
    并发纪律，见 _ANNOT_LOCK 注释）
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

from ..core.article import _fm_close_index
from ..infra.storage.filesystem import backup_article


# ---- R7 编辑环节数据面：正文切分/保存 + 批注 sidecar（正典 §四-R7）----


def _read_article_text(path: Path) -> str:
    """读文章原文字节（禁用换行翻译，CRLF 原样往返）。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _split_head_body(text: str) -> tuple[str, str]:
    """切成 (frontmatter 头含闭合界定符行, 其后正文字节原样)。

    读（GET body）与写（save）共用同一刀法，「读出→不改→写回」字节恒等；
    frontmatter 界定复用 core.article._fm_close_index（首行界定符 + 第二
    界定符、容行尾空白；无 frontmatter 读作头空、全文皆正文）。
    """
    lines = text.splitlines(keepends=True)
    close = _fm_close_index(lines)
    if close is None:
        return "", text
    return "".join(lines[: close + 1]), "".join(lines[close + 1 :])


def _save_body(path: Path, new_body: str) -> None:
    """仅替换正文：frontmatter 头字节不动，正文整体换 new_body，原子落盘。

    保存前当前版入滚动版本池（F43：backup_article 落 <file>.md.bak.<时间戳>
    保留 N 份——三处写回共用 helper；正典 §四-R7「服务端留 .bak 一次」的
    升级形态）；并发编辑最后写胜出，不做合并（单人工具，§八已写明的取舍）。
    原子写与 core.article._rewrite_status_line 同款。
    """
    head, _ = _split_head_body(_read_article_text(path))
    backup_article(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(head + new_body)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _annotations_file(path: Path) -> Path:
    """批注 sidecar：文章同目录 <stem>.annotations.json（不进文章扫描）。"""
    return path.with_name(f"{path.stem}.annotations.json")


_ANNOT_LOCK = threading.Lock()
"""批注 sidecar 读改写临界区锁（模块级，同 core.jobs.store 纪律）。

Studio 是 ThreadingHTTPServer——单人 UI 双击/连点即并发请求，load→append→
write 若无锁，两次 write_text 交错会把 sidecar 写坏成非法 JSON（复核实证）。
"""


def _read_annotations(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """严格读：缺文件 = ([], None)；坏 JSON/非数组 = ([], 可读错误)。

    写侧（add/delete/rewrite 校验）据此拒绝覆写——防「坏文件读作空 → 下次
    添加以空表为基底整表覆写、静默清空既有批注」（复核实证的现实链路）。
    读侧容错（GET 读空不中断编辑）走 _load_annotations。
    """
    sidecar = _annotations_file(path)
    if not sidecar.is_file():
        return [], None
    try:
        items = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], (
            f"批注文件损坏（{sidecar.name}: {exc.__class__.__name__}）——拒绝覆写以免"
            "清空既有批注，请人工修复或删除该文件后重试"
        )
    if not isinstance(items, list):
        return [], (
            f"批注文件格式异常（{sidecar.name}: 非数组）——拒绝覆写以免清空既有"
            "批注，请人工修复或删除该文件后重试"
        )
    return [item for item in items if isinstance(item, dict)], None


def _load_annotations(path: Path) -> list[dict[str, Any]]:
    """读批注列表（GET 容错入口）：sidecar 缺失/坏 JSON/非数组读作空——编辑
    不因坏 sidecar 中断。写侧禁止走本函数（坏文件须报错拒绝，见 _read_annotations）。"""
    items, _err = _read_annotations(path)
    return items


def _write_annotations(path: Path, items: list[dict[str, Any]]) -> None:
    """整表原子写回 sidecar：mkstemp + os.replace（与 _save_body 同款纪律），
    中断不留半截文件；覆写已有文件时保留其权限位。仅限 _mutate_annotations
    锁内调用——禁止裸 load→write（并发交错写坏 JSON，复核实证）。"""
    sidecar = _annotations_file(path)
    fd, tmp = tempfile.mkstemp(dir=str(sidecar.parent), prefix=f".{sidecar.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(items, ensure_ascii=False, indent=2))
        if sidecar.exists():
            os.chmod(tmp, stat.S_IMODE(sidecar.stat().st_mode))
        os.replace(tmp, sidecar)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _mutate_annotations(
    path: Path, mutate: Callable[[list[dict[str, Any]]], None]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """批注 sidecar 读改写唯一入口：锁内 严格读 → mutate 就地改 → 原子写回。

    坏 sidecar → ([], {"ok": False, "error": 可读错误}) 且绝不落盘；OSError
    原样上抛，由动作层转 _error_payload。返回 (改后列表, 错误 payload | None)。
    """
    with _ANNOT_LOCK:
        items, err = _read_annotations(path)
        if err is not None:
            return [], {"ok": False, "error": err}
        mutate(items)
        _write_annotations(path, items)
        return items, None
