"""plugin_actions · run_adjudicate 域——运行提交 / 三态裁决 / 节点重生成。

ARCH-2 ③（拆自 studio/plugin_actions.py 单文件 950 行，行为零变更）：

  - POST /api/pipelines/run（ROUTES 片段 1 条）：plan_pipeline 校验先行
    （零 job 纪律）→ _submit(kind=plugin_pipeline) → {job_id, run_id}；
  - POST /api/pipelines/runs/<run_id>/adopt|discard：三态裁决（AI-1 批
    2026-11-21，契约 §3.19 修订 3；body {step:'ks'|'aw', indices?}——动态段
    POST 为 URL 形（run_id 在段内，server._handle 裁决分流先于 GET 面判定），
    把 run 的 draft 产物转 adopted/discarded）；
  - POST /api/pipelines/runs/<run_id>/regenerate：节点重生成（AI-3 批
    2026-11-21，正典=裁决机制设计 §2.3 三件套·重生成；body {step:'ks'|'aw',
    feedback?}）——从该节点重跑（前序不重跑），旧产物归档 outputs.v{N}.json
    、新产物 status 重置 draft；长动作（ks/aw 是 AI 节点）走 job
    kind=plugin_pipeline，前端轮询 jobs 面。

裁决落点=**生效文档**：ranges 有编辑版（ranges.edited.json，E-4）改编辑版、
无编辑版改原始 outputs.json inline——与 GET segments「编辑版优先」读面同源；
article 直改 outputs.json inline（article.status 与顶层 article_status 直判面
同步）。已终态（adopted→adopt 等）幂等零改。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from ...jobs.store import now_iso
from ...services import pipeline_service_plugins as psp
from ..security import _HttpError, _StudioContext
from .list_detail import _load_pipeline
from .pipeline_crud import _PIPE_ID_RE
from .run_read import _REGEN_STEPS, _find_output_node, _run_dir_checked

_ADJUDICATE_STATUSES = {"adopt": "adopted", "discard": "discarded"}
_ADJUDICATE_PATH_RE = re.compile(
    r"^/api/pipelines/runs/([^/]+)/(adopt|discard|regenerate)$"
)

# 动态 POST 面（占位形——契约锁 POST_EXACT 与 API 契约文档对齐用；
# <run_id> 为运行期段，与 GET_DYNAMIC_PREFIXES 承载动态读面同一思路）
POST_DYNAMIC_SURFACE = frozenset({
    "/api/pipelines/runs/<run_id>/adopt",
    "/api/pipelines/runs/<run_id>/discard",
    "/api/pipelines/runs/<run_id>/regenerate",
})


def resolve_adjudicate_post(path: str) -> tuple[str, str] | None:
    """路径 → (裁决动作名, run_id)；非裁决动作路径返回 None（server 分派用）。"""
    m = _ADJUDICATE_PATH_RE.match(path)
    return None if m is None else (m.group(2), m.group(1))


def _validate_indices(raw: Any, total: int) -> list[int] | None:
    """indices 校验：省略 → None（全集）；空数组/非数组/非整数/越界 → 400
    可读拒绝（裁决点 18：非法参数不静默丢弃）。"""
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise _HttpError(400, f"indices 须为下标数组或省略（省略=全集）: {raw!r}")
    if not raw:
        raise _HttpError(400, "indices 为空数组——至少选一个下标或省略（省略=全集）")
    for i in raw:
        if isinstance(i, bool) or not isinstance(i, int):  # bool 是 int 子类，显式排除
            raise _HttpError(400, f"indices 须为非负整数下标: {i!r}")
        if i < 0 or i >= total:
            raise _HttpError(
                400, f"indices 越界: {i}（产物共 {total} 项，合法下标 0-{total - 1}）"
            )
    return sorted(dict.fromkeys(raw))


def _append_run_log(run_dir: Path, node_id: str, msg: str) -> None:
    """裁决动作追加一行 run log（引擎 _log 同形状）——审计面，失败不吞裁决。"""
    try:
        with (run_dir / "log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"node": node_id, "msg": msg, "ts": now_iso()}, ensure_ascii=False
            ) + "\n")
    except OSError:
        pass  # 状态已落 outputs.json，日志追加失败不影响裁决结果


def _rewrite_inline_output(node_dir: Path, key: str, value: Any) -> None:
    """把改后的 inline 值写回 outputs.json（refs 与其余端口原样保留）。"""
    path = node_dir / "outputs.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _HttpError(500, f"outputs.json 读取失败: {exc}") from None
    if not isinstance(doc, dict):
        raise _HttpError(500, "outputs.json 结构损坏（顶层非对象）")
    inline = doc.get("inline")
    if not isinstance(inline, dict):
        inline = doc["inline"] = {}
    inline[key] = value
    try:
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise _HttpError(500, f"outputs.json 写回失败: {exc}") from None


def _adjudicate_ranges(run_dir: Path, indices: Any, target: str) -> dict[str, Any]:
    """ks 切片裁决：生效 ranges（编辑版优先）按 indices（缺省全集）盖 status。"""
    found = _find_output_node(run_dir, "ranges")
    if found is None:
        raise _HttpError(400, f"该运行无 ranges 产物节点（链上需有切片节点）: {run_dir.name}")
    node_dir, original = found
    edited_path = node_dir / "ranges.edited.json"
    dest_list, dest_file = original, ""
    if edited_path.is_file():
        try:
            data = json.loads(edited_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):  # 宽容包装形（_effective_edited 同判据）
            data = data.get("ranges")
        if isinstance(data, list):  # 编辑版优先
            dest_list, dest_file = data, "ranges.edited.json"
    chosen = _validate_indices(indices, len(dest_list))
    targets = dest_list if chosen is None else [dest_list[i] for i in chosen]
    for i, item in enumerate(targets):
        if not isinstance(item, dict):  # 先验全量再落笔——半途失败不留混合态
            raise _HttpError(400, f"ranges 产物含非对象项（下标 {i}）: {item!r}")
    changed = sum(1 for item in targets if item.get("status") != target)
    for item in targets:
        item["status"] = target
    if dest_file:
        try:
            edited_path.write_text(
                json.dumps(dest_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            raise _HttpError(500, f"ranges.edited.json 写回失败: {exc}") from None
    else:
        _rewrite_inline_output(node_dir, "ranges", original)
    return {"changed": changed, "total": len(dest_list), "edited": bool(dest_file)}


def _adjudicate_article(run_dir: Path, target: str) -> dict[str, Any]:
    """aw 成稿裁决：单产物（article）整稿盖 status，顶层 article_status 同步。"""
    found = _find_output_node(run_dir, "article", expect=dict)
    if found is None:
        raise _HttpError(400, f"该运行无 article 产物节点（链上需有成稿节点）: {run_dir.name}")
    node_dir, _ = found
    path = node_dir / "outputs.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _HttpError(500, f"outputs.json 读取失败: {exc}") from None
    inline = doc.get("inline") if isinstance(doc, dict) else None
    article = inline.get("article") if isinstance(inline, dict) else None
    if not isinstance(article, dict):
        raise _HttpError(500, "outputs.json 结构损坏（inline.article 非对象）")
    changed = 1 if article.get("status") != target else 0
    article["status"] = target
    if isinstance(inline.get("article_status"), str):  # 顶层直判面同步（下游免解包判态）
        inline["article_status"] = target
    try:
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        raise _HttpError(500, f"outputs.json 写回失败: {exc}") from None
    return {"changed": changed, "total": 1}


def run_adjudicate(
    ctx: _StudioContext, data: dict[str, Any], run_id: str, action: str
) -> dict[str, Any]:
    """POST /api/pipelines/runs/<run_id>/adopt|discard：把指定 run 的 draft
    产物转 adopted/discarded（body: {step: 'ks'|'aw', indices?}；confirm 闸门
    走 server 通用层）。动态段 POST（server 分派见 _handle 裁决分流），
    run_id 在 URL 段内——白名单回验在 _run_dir_checked（404 面，防穿越）。"""
    target = _ADJUDICATE_STATUSES[action]
    run_dir = _run_dir_checked(run_id)
    step = data.get("step")
    if step not in ("ks", "aw"):
        raise _HttpError(
            400, f"step 须为 'ks'（key-segment 切片 ranges）或 'aw'（article-writer 成稿）: {step!r}"
        )
    if step == "ks":
        outcome = _adjudicate_ranges(run_dir, data.get("indices"), target)
        node = "ranges"
    else:
        if data.get("indices") is not None:
            raise _HttpError(400, "step=aw 为单产物（article），不接受 indices（省略即整稿裁决）")
        outcome = _adjudicate_article(run_dir, target)
        node = "article"
    _append_run_log(
        run_dir, node,
        f"裁决: {node} → {target}（changed={outcome['changed']}/{outcome['total']}）",
    )
    return {
        "ok": True,
        "run_id": run_dir.name,
        "step": step,
        "status": target,
        "changed": outcome["changed"],
        "total": outcome["total"],
    }


# ── 节点重生成（AI-3 批 2026-11-21；正典=裁决机制设计 §2.3/§三 3.1/3.2）──
# POST /api/pipelines/runs/<run_id>/regenerate {step:'ks'|'aw', feedback?}：
# 「这条切片不对，再来一次」——从该节点重跑（前序不重跑），旧版归档
# outputs.v{N}.json 供对比，新产物 status 重置 draft（要重新裁决）。执行
# 体=psp.regenerate_node（长动作：ks/aw 是 AI 节点，走 job 面轮询）。
# step→产物端口表 _REGEN_STEPS 在 run_read 域（versions 读面同表消费）。


def run_regenerate(ctx: _StudioContext, data: dict[str, Any], run_id: str) -> dict[str, Any]:
    """POST /api/pipelines/runs/<run_id>/regenerate（动态段 POST，confirm 闸门
    走 server 通用层）：提交一次节点重生成 job（kind=plugin_pipeline）。

    - step 校验（'ks'|'aw'，枚举外 400 可读）→ 按产物端口定位节点目录
      （_find_output_node——与裁决端点同款定位面，无产物节点 400）；
    - feedback 可选字符串（自然语言改进要求，如「更简洁」），空串视同
      省略；非字符串 400；
    - 提交期校验先行（psp.precheck_regenerate，零 job 纪律）：run 终态、
      节点在册、旧产物在场、plan 可重建——不过即 400 可读，失败零副作用；
    - job 体=psp.regenerate_node（归档/重跑/draft 重置/log 审计行全在引擎
      侧）；响应 {ok, job_id, run_id, step, node}，前端轮询 /api/jobs/<id>。
    """
    from ..actions import _submit  # 惰性导入（环导规避，_action_pipeline_run 同款）

    run_dir = _run_dir_checked(run_id)
    step = data.get("step")
    if step not in _REGEN_STEPS:
        raise _HttpError(
            400, f"step 须为 'ks'（key-segment 切片 ranges）或 'aw'（article-writer 成稿）: {step!r}"
        )
    port, expect, label = _REGEN_STEPS[step]
    found = _find_output_node(run_dir, port, expect=expect)
    if found is None:
        raise _HttpError(
            400, f"该运行无 {label} 产物节点（链上需有该步骤）: {run_id}"
        )
    node_id = found[0].name
    feedback = data.get("feedback")
    if feedback is not None:
        if not isinstance(feedback, str):
            raise _HttpError(400, f"feedback 须为字符串（自然语言改进要求，可省略）: {feedback!r}")
        feedback = feedback.strip() or None
    try:
        psp.precheck_regenerate(run_id, node_id)
    except psp.RegenerateError as err:
        raise _HttpError(400, str(err)) from None

    def _body(_job_id: str) -> dict[str, Any]:
        return psp.regenerate_node(run_id, node_id, feedback=feedback)

    out = _submit(
        ctx, "plugin_pipeline",
        {"regenerate": run_id, "step": step, "node": node_id}, _body,
    )
    return {**out, "run_id": run_id, "step": step, "node": node_id}


def _action_pipeline_run(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/pipelines/run：运行 → job（kind=plugin_pipeline）。

    校验先行（plan_pipeline，零 run 零 job 纪律）；run_id 预生成随响应返回
    （前端优先轮询 runs 读面，job 面兜底——psp.job_fn 语义不变）。
    """
    from ..actions import _submit  # 惰性导入（tts_actions 同款，避开环导）

    pid = str(data.get("id") or "").strip()
    if not _PIPE_ID_RE.match(pid):
        raise _HttpError(400, f"id 非法: {pid!r}")
    saved = _load_pipeline(pid)
    engine_nodes = [
        {"id": str(n.get("id") or ""), "plugin": str(n.get("plugin") or "")}
        for n in saved["nodes"]
    ]
    try:
        psp.plan_pipeline(engine_nodes)  # 阶段一：manifest 可读+契约+权限并集
    except psp.PipelinePlanError as err:
        raise _HttpError(400, str(err)) from None
    run_id = psp._new_run_id(pid)

    def _body(_job_id: str) -> dict[str, Any]:
        return psp.run_pipeline(engine_nodes, run_id=run_id)

    out = _submit(ctx, "plugin_pipeline", {"pipeline": pid, "run_id": run_id}, _body)
    return {**out, "run_id": run_id}


ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/pipelines/run": _action_pipeline_run,  # §十二 运行（body id 形）
}
