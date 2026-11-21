"""Studio R7 编辑环节 POST 动作（Phase A 自 actions.py 拆出，行为零变更）。

正典 §八 Phase A「actions 拆为 actions（路由表+薄壳）+ annotate（批注动作）」
的落点：save / 批注增删（瞬时本地，数据面在 annotations.py）与按批注 AI
改写（长动作，执行体在 services.annotate_service）。_runner 延迟导入——
actions 顶部导入本模块装配路由表，反向顶部互导会成环（与 payload/scan 的
既有纪律一致）。
"""
from __future__ import annotations

import secrets
from typing import Any

from ..infra import ai
from ..jobs.store import now_iso
from ..services import annotate_service
from .annotations import _mutate_annotations, _read_annotations, _save_body
from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
    _resolve_file,
)


def _action_save(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/article/save {article, content, confirm:true}：仅替换正文。

    frontmatter 字节不动（annotations._save_body 行级改写，保存前当前版入
    .md.bak.<时间戳> 滚动版本池——F43 三处写回共用 helper）；本地瞬时文件
    操作，保持同步不建 job。
    """
    article_rel = _require_str(data, "article")
    content = data.get("content")
    if not isinstance(content, str):
        raise _HttpError(400, "缺少必填字段 content（新正文，字符串；空串=清空正文）")
    path = _resolve_file(ctx.root, article_rel)
    try:
        _save_body(path, content)
    except OSError as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, "article": article_rel}


def _action_annotation_add(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/annotations/add {path, line_start, line_end?, comment}：追加批注。

    路径用 /add 而非与 GET 同名：server 分派先查 GET 路由表，同名 POST 会
    先撞上 405（方法不符），动作分支不可达。sidecar <stem>.annotations.json
    追加一条；id 服务端生成（8 位 hex 短 id），created 用任务层同一时间源。
    line_end 缺省 = line_start（单行批注）。
    """
    rel = _require_str(data, "path")
    comment = data.get("comment")
    if not isinstance(comment, str) or not comment.strip():
        raise _HttpError(400, "缺少必填字段 comment（批注内容）")
    start = data.get("line_start")
    end = data.get("line_end", start)
    for name, value in (("line_start", start), ("line_end", end)):
        # bool 是 int 子类，True 会被当行号 1 —— 一并排除
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise _HttpError(400, f"{name} 必须是 ≥1 的整数（正文行号）")
    path = _resolve_file(ctx.root, rel)
    item = {
        "id": secrets.token_hex(4),
        "line_start": start,
        "line_end": end,
        "comment": comment.strip(),
        "created": now_iso(),
    }
    try:
        # 锁内读改写（并发 add 交错会把 sidecar 写坏，复核实证）；坏 sidecar
        # → err 拒绝覆写（防空表整表覆写清空既有批注）
        _, err = _mutate_annotations(path, lambda items: items.append(item))
    except OSError as exc:
        return _error_payload(exc, ctx.root)
    if err is not None:
        return err
    return {"ok": True, "annotation": item}


def _action_annotation_delete(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/annotations/delete {path, id, confirm:true}：按 id 删批注。

    id 不存在 → {ok:false} 可读错误（不静默成功）。承载形态为主 agent 裁决：
    server 方法表仅 GET/POST（§15.3），删除动作与其它动作同走 POST 管线。
    """
    rel = _require_str(data, "path")
    anno_id = _require_str(data, "id")
    path = _resolve_file(ctx.root, rel)
    removed = {"n": 0}

    def _drop(items: list[dict[str, Any]]) -> None:
        before = len(items)
        items[:] = [item for item in items if str(item.get("id")) != anno_id]
        removed["n"] = before - len(items)

    try:
        _, err = _mutate_annotations(path, _drop)  # 锁内读改写（同 add 的并发纪律）
    except OSError as exc:
        return _error_payload(exc, ctx.root)
    if err is not None:
        return err
    if removed["n"] == 0:
        return _error_payload(ValueError(f"批注不存在: {anno_id}"), ctx.root)
    return {"ok": True, "deleted": anno_id}


def _action_annotate_rewrite(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/annotate_rewrite {article, confirm:true}：按批注 AI 改写。

    提交期同步校验（快、本地）：路径解析/批注非空/AI 已配置——不满足即
    {ok:false} 可读错误，不建 job。AI 调用是长动作进 job（执行体
    annotate_service.annotate_rewrite）：批注列表+正文 → 逐条回应的修改稿，
    产出 <stem>.annotated.md 预览文件（frontmatter 沿袭原文，绝不在原文上
    落笔）；job result 记 {output, annotations_used}。
    """
    from .actions import _submit  # 延迟导入（actions 顶部导入本模块，避免成环）

    article_rel = _require_str(data, "article")
    article_path = _resolve_file(ctx.root, article_rel)
    # 严格读：坏 sidecar 报「文件损坏」而非误导性的「没有批注」（同根缺陷）
    annotations, ann_err = _read_annotations(article_path)
    if ann_err is not None:
        return {"ok": False, "error": ann_err}
    if not annotations:
        return _error_payload(ValueError("没有批注可应用：先在编辑 tab 添加批注"), ctx.root)
    if not ai.ai_configured("annotate_rewrite"):  # 文案与 ai.rewrite 同款（引导 musespiral ai config）
        return _error_payload(
            ai.AIError(
                "AI 未配置。请先运行: musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
            ),
            ctx.root,
        )

    def _body(_job_id: str) -> dict[str, Any]:
        out = annotate_service.annotate_rewrite(article_path, annotations)
        return {"output": _rel(ctx.root, out), "annotations_used": len(annotations)}

    return _submit(ctx, "annotate_rewrite", {"article": article_rel}, _body)
