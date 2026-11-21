"""Studio 版本 diff 与恢复端点（E-6，契约 §3.17；正典=《musespiral-专业创作者视角深度
评审与改进-2026-11-18》§3.7「I4 版本 diff 视图」）。

两条路由：

  - **GET /api/article/diff** `?path=&v1=&v2=&context=`（纯读）
      无 v1/v2 → 只回版本清单（当前版 + 池内各版）——下拉数据面；
      有 v1/v2 → 清单 + 结构化 hunks + 服务端渲染 HTML（红删绿增）。
      同一端点兼两态（同 /api/agent/runs 的双模式判例）：前端进 tab 一次请求
      拿全，增删一个 GET 足够。
  - **POST /api/article/restore** `{confirm:true, article, version}`
      把池内某一版恢复成当前版；恢复前 confirm 闸门由 server 动作层统一强制
      （`_require_confirm`），服务层 `restore_version` 再把当前版入池——撤销可撤销。

命名口径：`path` 与既有 /api/article 族同义（root 内相对路径）；设计 §3.7 文字
写作 `slug=`，两参数在本模块内**同义**（先取 path，缺省回落 slug），值都走
`_resolve_file` 的边界校验——不因参数名叫 slug 就绕过 root 边界。

分层：本模块只做参数解析 + 结果格式化，编排全在 services/version_service
（函数体内导入，同 compose_actions 的导入期解耦纪律）。
"""
from __future__ import annotations

from typing import Any

from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _query_str,
    _rel,
    _require_str,
    _resolve_file,
)

# 上下文行数钳制区间（缺省 3，与 unified diff 惯例一致）
_CONTEXT_MIN, _CONTEXT_MAX = 0, 20


def _service():
    from ..services import version_service  # 函数体内导入（导入期解耦）

    return version_service


def _article_path(ctx: _StudioContext, query: dict[str, list[str]]):
    """path（或 slug，同义）→ root 内实际文件；缺失/绝对/越界 400、不存在 404。"""
    rel = _query_str(query, "path") or _query_str(query, "slug")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径；与 slug 同义）")
    return _resolve_file(ctx.root, rel), rel


def _context_arg(query: dict[str, list[str]]) -> int:
    """context（上下文行数）：缺省 3；非整数/越界 → 400（契约 §六 同款口径）。"""
    raw = _query_str(query, "context")
    if raw is None:
        return 3
    try:
        value = int(raw)
    except ValueError:
        raise _HttpError(400, "context 必须是整数（上下文行数）") from None
    if value < _CONTEXT_MIN or value > _CONTEXT_MAX:
        raise _HttpError(400, f"context 超出范围 [{_CONTEXT_MIN}, {_CONTEXT_MAX}]")
    return value


def _article_diff_payload(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> dict[str, Any]:
    """GET /api/article/diff?path=&v1=&v2=&context= → 版本清单（+ 可选 diff）。

    响应（无 v1/v2）：
      `{article, versions:[{id,label,ts,summary,bytes,current,available}], diff:null}`
    响应（有 v1/v2）：
      `{article, versions:[…], diff:{v1:{id,label,bytes}, v2:{…}, added, removed,
        identical, hunks:[{header, rows:[{kind,text,old_no,new_no}]}], html}}`

    `versions[0]` 恒为当前版（`id="current"`）——「当前 vs 某一版」是最常见的
    对比形态；池内其余按新→旧，旧无时间戳 .md.bak 恒排尾部（`id="legacy"`）。
    """
    path, rel = _article_path(ctx, query)
    v1, v2 = _query_str(query, "v1"), _query_str(query, "v2")
    service = _service()
    try:
        versions = service.list_versions(path)
        diff: dict[str, Any] | None = None
        if v1 or v2:  # 只给一侧 = 形状不对（不猜默认论）
            if not (v1 and v2):
                raise _HttpError(400, "v1 与 v2 必须同时给出（两版对比）")
            diff = service.diff_versions(path, v1, v2, context=_context_arg(query))
    except service.VersionError as err:
        raise _HttpError(400, str(err)) from None
    return {"article": rel, "versions": versions, "diff": diff}


def _action_article_restore(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/article/restore {confirm:true, article, version} → 恢复池内一版。

    动作人审闸门在 server 动作层统一强制（缺 confirm → 400）。业务失败
    （版本不在池中 / version=current / 版本文件非 UTF-8）→ `200 {ok:false}`
    可读错误（同 compose 族的 ComposeError 口径）；路径越界仍在提交期 400。
    """
    article_rel = _require_str(data, "article")
    version = _require_str(data, "version")
    path = _resolve_file(ctx.root, article_rel)
    service = _service()
    try:
        info = service.restore_version(path, version)
    except service.VersionError as exc:
        return _error_payload(exc, ctx.root)
    return {
        "ok": True,
        "article": article_rel,
        "version": info["id"],
        "label": info["label"],
        "bytes": info["bytes"],
        "path": _rel(ctx.root, path),
    }
