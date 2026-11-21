"""discover 域动作：发现/知识库/回采三动作（/api 3 条）。

R-2 行数门拆分（行为零变更）：自 handlers/platform.py 按域迁出——
  - _action_sync_stats（/api/action/sync_stats 发布数据回采，第十四期 F39）
  - _action_discover_draft（/api/action/discover_draft KB→骨架，第十二期 F29/F30）
  - 星标家族（/api/discover/star GET+POST：_discover_star_payload/
    _action_discover_star + _recent_drafts；档A M0 + M4 迁移表件二）
job 提交复用共享管道 `_submit`（同包 platform 域持有，actions re-export
面不变）；`_discover_star_payload` 的 routing.py 顶部
`from .actions import _discover_star_payload` 导入面经 actions re-export
保持不变。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ...jobs.runner import JobFailure
from .platform import _submit
from ..security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_confirm,
    _require_str,
    _resolve_inside,
    _sanitize_error,
)


def _action_sync_stats(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """发布数据回采（第十四期 F39）：stats --sync 既有链路进 job 层。

    - confirm:true 人审闸门（与其余动作同语义）；
    - 回填目标 = scan._log_path(root)（root 自带日志优先，写侧单文件）；
      仪表盘 _stats_payload 读序第十四期起为 root∪home 合并+demo 滤除
      （publish_log_service）——回填写入的行落在合并面内，回采后瓷砖
      刷新即见新数；home 侧独有行的回填仍走 CLI `stats --sync`（同目标）；
    - job 执行体调 infra.wechat.analytics.sync_reads（CLI `stats --sync`
      同一实现），AnalyticsError → JobFailure（已脱敏）；stats 接口本体已随
      第十六期 F41 迁移 getarticletotaldetail（裁决③落定，入口零改动）。
    """
    _require_confirm(data)
    # 延迟导入：scan 反向依赖 actions 链路（_action_publish 同款环规避）
    from ..scan import _log_path

    from ...infra.wechat.analytics import AnalyticsError, sync_reads

    log_path = _log_path(ctx.root)

    def _body(_job_id: str) -> dict[str, Any]:
        try:
            done = sync_reads(log_path)
        except AnalyticsError as exc:
            raise JobFailure(_sanitize_error(exc, ctx.root)) from None
        return dict(done)

    return _submit(ctx, "sync_stats", {"log": str(log_path)}, _body)


def _action_discover_draft(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """知识库文档 → 文章骨架（第十二期 F29/F30 Studio 入口；同步瞬时，不建 job）。

    - confirm:true 人审闸门（_require_confirm）；
    - path 必须落在知识库根内（_resolve_inside 穿越防护）且命中实际文件；
    - 骨架写 ctx.root（output_dir），成功后队列页可见；O_EXCL 已存在 →
      ok:false 可读错误（不 500）；
    - discover_service 的 import 写在函数体内（并行小组落地，导入期解耦）。
    """
    from ... import config  # 函数体内导入：config 读取随 MUSESPIRAL_HOME 隔离解析

    _require_confirm(data)
    kb = config.knowledge_root()
    if kb is None or not kb.is_dir():
        raise _HttpError(400, "知识库根未配置（~/.musespiral/kb_root.txt 指向 docs/ai-knowledge-base）")
    path_rel = _require_str(data, "path")
    src = _resolve_inside(kb, path_rel)  # 越界/绝对路径 → 400
    if not src.is_file():
        raise _HttpError(404, f"知识库文档不存在: {path_rel}")
    series = data.get("series")
    if series is not None and not isinstance(series, str):
        raise _HttpError(400, "series 必须是字符串或 null")
    from ...services import discover_service  # 函数体内导入（导入期解耦）

    try:
        output = discover_service.draft_from_kb(src, series=series or None, output_dir=ctx.root)
    except FileExistsError as exc:  # O_EXCL：同一篇重复生成 → 可读拒绝（不透传 errno）
        name = Path(str(exc.filename or "")).name or path_rel
        return {"ok": False, "error": f"骨架已存在（拒绝覆盖）: {name}"}
    except Exception as exc:  # ValueError/OSError → 可读错误
        return _error_payload(exc, ctx.root)
    return {"ok": True, "output": _rel(ctx.root, output)}


# ---- 档A（扩展探索 §2.3 M0）：收藏星标 triage.starred 持久化 ----
# GET/POST /api/discover/star 微端点。M4（第二十期迁移表件二）：存储面自
# ~/.musespiral/discover_stars.json 切换为 materials 素材库（triage.starred，
# 与 /api/materials/triage 同一数据面）——旧文件由 migrate_p19_stars
# 一次性迁入后只读退役，本端点不再写它。契约形状不变：收藏集合
# {uri: fetched_at}（uri=KB 相对路径；迁移行 fetched_at 即原星标时间戳）
# +「最近」tab 数据面 = discovered.json 倒序（path 归一 KB 相对，读侧兼容）。

_STAR_RECENT_MAX = 20
_STAR_KB_SOURCE = "kb"


def _recent_drafts(kb: Path) -> list[dict[str, Any]]:
    """discovered.json 倒序取最近生成骨架记录（path 归一 KB 相对；坏行跳过）。"""
    from ...services import discover_service

    out: list[dict[str, Any]] = []
    try:
        lines = discover_service.discovered_path().read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return out
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict) or record.get("action") != "discover_draft":
            continue
        try:
            rel = Path(str(record.get("path") or "")).resolve().relative_to(kb).as_posix()
        except (OSError, ValueError):
            continue
        out.append(
            {
                "path": rel,
                "title": str(record.get("title") or ""),
                "score": record.get("score"),
                "ts": str(record.get("ts") or ""),
            }
        )
        if len(out) >= _STAR_RECENT_MAX:
            break
    return out


def _discover_star_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/discover/star：收藏集合（materials 现读）+ 最近生成骨架记录。

    routing.py 顶部 `from .actions import _discover_star_payload` 的既有导入
    面经 actions re-export 保持不变。
    """
    from ... import config
    from ...services import materials_service

    starred: dict[str, str] = {}
    for material in materials_service.query(starred=True):
        uri = str((material.get("origin") or {}).get("uri") or "")
        if uri:
            starred[uri] = str(material.get("fetched_at") or "")
    payload: dict[str, Any] = {"starred": starred}
    kb = config.knowledge_root()
    payload["recent"] = _recent_drafts(kb) if kb is not None and kb.is_dir() else []
    return payload


def _action_discover_star(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/discover/star：收藏/取消收藏（M4 起读写 materials triage.starred）。

    与 migrate_p19_stars 同语义：素材在册 → update_triage；不在册 → 落一条
    kb 引用素材（引用不复制，payload.local_path 指回——收藏先于采集的自然
    收敛）。KB 根界检查不变（越界/绝对路径 → 400）。
    """
    from ... import config
    from ...services import materials_service

    path_rel = _require_str(data, "path")
    starred = data.get("starred")
    if not isinstance(starred, bool):
        raise _HttpError(400, "starred 必须是布尔值")
    kb = config.knowledge_root()
    if kb is None or not kb.is_dir():
        raise _HttpError(400, "知识库根未配置（收藏随 KB 根界定）")
    _resolve_inside(kb, path_rel)  # 越界/绝对路径 → 400
    result = materials_service.set_kb_starred(path_rel, starred, source_id=_STAR_KB_SOURCE)
    return {"ok": True, "path": path_rel, **result}


# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/action/discover_draft": _action_discover_draft,  # 第十二期 F29：知识库文档→骨架
    "/api/action/sync_stats": _action_sync_stats,  # 第十四期 F39：发布数据回采（仪表盘按钮）
    "/api/discover/star": _action_discover_star,  # 档A M0：收藏星标 triage.starred 持久化
}
