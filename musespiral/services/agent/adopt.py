"""agent 采纳簇——双采纳目标（唯一外部写面）。

原 ``services/agent_service.py`` 的「双采纳目标」段（W2-B4 拆分落点）。
纪律 3（设计 §四 Q3）：**采纳是唯一外部写面**——run 永不直写 materials/正稿；
本模块通过 `adopt_material` / `adopt_compose` 两窄门落库（前者
`add_material`，后者 `compose_service.new_session` + `blocks_from_markdown`
种稿）。架构门 D5（`test_rule_d5_agent_service_write_surface_only_in_adopt`）
扫描本簇：写面 API 引用只允许出现在 `adopt*` 函数体内。

**license 最严者胜**（设计 §二-F76.5 本期裁）：多源综合的 license 继承不是
「继承第一个」，而是全源取最保守——`ALL-RIGHTS` 有一个即 ALL-RIGHTS；
否则有 `UNKNOWN` 即 UNKNOWN；全 `CC-BY` 才 CC-BY。理由：综合稿引用多方，
任何一方的限制都传导到成品上。各源声明原文合并成列表存
`payload.meta.license_note`（单源时仍是列表——形状恒定，消费方不必双分支）。

**幂等**（§二-F76.4）：`uri=synthesis://<run_id>/<指纹8>` 由 run_id 与综合稿
正文指纹决定，同 run 同 target 二次采纳 → 素材 id 不变 → `add_material`
整行相等返回既有行零追加（materials_service.add_material 幂等判定既有路径）。
语义逐字未动，本模块只改落点。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Sequence

from ...core.agent.interp import STATUS_DONE
from ...core.agent.plans_builtin import PLAN_SYNTHESIS
from ...core.composition import blocks_from_markdown
from ...core.materials import model
from ...core.materials.confidence import parse_ai_confidence  # N-4 综合稿末行自评
from .. import materials_service
from .build import strictest_license
from .execute import read_run
from .store import AgentServiceError, _now

# 综合计划的合成动作与 uri 前缀（F76 血统动作；model.PROVENANCE_OPS 扩键）
SYNTHESIS_OP = "synthesize"
SYNTHESIS_URI_SCHEME = "synthesis"
# adopt material 时的 origin.source_id（综合稿的信息源标记）
SYNTHESIS_SOURCE_ID = "synthesis"


def _require_done_synthesis(run_id: str) -> dict[str, Any]:
    """采纳前置校验 → run.json（不存在/status≠done/plan 非 synthesis_v1 均拒）。

    `budget_stopped` 明确不可采纳（设计 §二-F76.4：部分产物不落库）——它是
    守门而非「部分成功」。
    """
    meta = read_run(run_id)
    if meta is None:
        raise AgentServiceError(f"run 不存在（可能已被 prune 清理）: {run_id!r}")
    if meta.get("status") != STATUS_DONE:
        raise AgentServiceError(
            f"run 状态为 {meta.get('status')!r}，不可采纳（仅 done 可采纳；"
            f"running/failed/budget_stopped 的部分产物不落库）"
        )
    if meta.get("plan") != PLAN_SYNTHESIS:
        raise AgentServiceError(
            f"计划 {meta.get('plan')!r} 无采纳面（仅 {PLAN_SYNTHESIS} 可采纳；"
            f"tag_batch 的标签走 triage 窄门）"
        )
    return meta


def _synthesis_text(meta: dict[str, Any]) -> str:
    """从 run 存证取综合稿正文（末步 out_text）——采纳正文不设入参（§二-F76.4）。"""
    steps = meta.get("steps") or []
    for step in reversed(steps):
        if isinstance(step, dict) and step.get("task") == "agent_synthesize":
            text = step.get("out_text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise AgentServiceError("run 存证中找不到综合稿正文（steps/*.json 缺失或被手改）")


def _snapshot_notes(licenses: Sequence[dict[str, Any]]) -> list[str]:
    """快照 → 各源声明原文列表（空白跳过；顺序=选材序——快照序即选材序）。"""
    return [str(r.get("license_note")) for r in licenses if str(r.get("license_note") or "").strip()]


def _adopt_licenses(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """采纳用的源许可集合：run.json inputs.licenses 快照优先；缺（防御性
    fallback，如 run.json 被手改删键）→ 现库存活源（此时已删源按缺失=
    UNKNOWN 参与最严判定——`strictest_license` 对缺 license 键的行取
    UNKNOWN，不因源消失而变宽松）。"""
    licenses = (meta.get("inputs") or {}).get("licenses")
    if isinstance(licenses, list) and licenses:
        return [dict(r) for r in licenses if isinstance(r, dict)]
    # legacy/防御路径：快照缺席时按「id 列表齐全、许可未知」重建——每个源
    # id 一行 license=UNKNOWN（比「跳过已删源」严格：最严判定不会被删除
    # 动作放松），license_note 从现库存活源尽力补
    ids = [str(mid) for mid in (meta.get("inputs") or {}).get("material_ids") or []]
    alive = {m["id"]: m for m in _run_materials(meta)}
    return [
        {
            "id": mid,
            "license": str((alive.get(mid) or {}).get("license") or "UNKNOWN"),
            "license_note": _license_note_of(alive.get(mid)),
        }
        for mid in ids
    ]


def _license_note_of(material: dict[str, Any] | None) -> str:
    """单素材的 license_note 原文（缺失/非串 → 空串）。"""
    if not material:
        return ""
    meta = (material.get("payload") or {}).get("meta")
    note = meta.get("license_note") if isinstance(meta, dict) else None
    return note.strip() if isinstance(note, str) and note.strip() else ""


def _run_materials(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """按 run.json inputs.material_ids 取素材（已删的源跳过——综合稿仍可采纳）。"""
    materials: list[dict[str, Any]] = []
    for mid in (meta.get("inputs") or {}).get("material_ids") or []:
        material = materials_service.get_material(str(mid))
        if material is not None:
            materials.append(material)
    return materials


def adopt_material(run_id: str, *, title: str | None = None) -> dict[str, Any]:
    """采纳为 derived 素材（target=material）→ `{ok, id, material}`。

      - `type=derived`；`provenance.chain=[{from_id:<m_i>, op:"synthesize"}…]`
        （选中素材 id 全集，顺序=选材序——run.json inputs 的原序）；
      - `license`=最严者胜（`strictest_license`，**按 run.json inputs.licenses
        快照判定**——源在采纳前被删不放松许可，见 `_license_snapshot`）；
      - `payload.meta.license_note`=各源声明**列表**（单源也是列表——形状恒定；
        同样取自快照）；
      - `payload.meta.material_ids`=选中 id 全集（回溯面；chain 已带，此处便于
        详情页直接展示而不用遍历 chain）；
      - `triage.confidence`=AI 自评 0-1（N-4：综合稿末行 CONFIDENCE 标记解析
        剥除后写入；AI 未带该行/值非法 → None——容错不阻断面）；
      - `origin.source_id="synthesis"`、`uri=synthesis://<run_id>/<指纹8>`。
    幂等：同 run 二次采纳 → 同 uri → 同 id → `add_material` 返回既有行零追加。
    """
    meta = _require_done_synthesis(run_id)
    body = _synthesis_text(meta)
    body, confidence = parse_ai_confidence(body)  # N-4：剥末行自评（无标记原样）
    licenses = _adopt_licenses(meta)
    ids = [str(mid) for mid in (meta.get("inputs") or {}).get("material_ids") or []]
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:8]
    payload: dict[str, Any] = {"text": body, "meta": {"material_ids": ids}}
    notes = _snapshot_notes(licenses)
    if notes:
        payload["meta"]["license_note"] = notes
    row = model.make_material(
        mtype="derived",
        source_id=SYNTHESIS_SOURCE_ID,
        uri=f"{SYNTHESIS_URI_SCHEME}://{run_id}/{digest}",
        title=(title.strip() if isinstance(title, str) and title.strip() else f"综合 · {run_id}"),
        license=strictest_license(licenses),
        payload=payload,
        provenance={
            "chain": [{"from_id": mid, "op": SYNTHESIS_OP} for mid in ids],
            "derived_from": [],
        },
        triage={"confidence": confidence},
        fetched_at=_now(),
    )
    saved = materials_service.add_material(row)
    return {"ok": True, "id": saved["id"], "material": saved}


def _compose_slug(run_id: str) -> str:
    """compose 会话 slug：`synthesis-<run_id>`（run_id 已含 plan+时戳+rand，
    天然 [A-Za-z0-9._-] 白名单内且幂等——同 run 二次采纳撞同一 slug）。"""
    return f"synthesis-{run_id}"


def adopt_compose(run_id: str, *, root: str | Path | None = None) -> dict[str, Any]:
    """采纳为 compose 会话（target=compose）→ `{ok, slug, dir}`。

    桥接口径（设计 §二-F76.5）：复用 `compose_service.new_session` 建会话
    + `blocks_from_markdown` 把综合稿解析为块序列种入 `draft.md`。
    **不自动挂 sources**——素材→SourceRef 需路径面（radar_signal/derived 等
    没有可读路径），用户后续手动 `compose add`。

    幂等：同 run 二次 → slug 相同 → `new_session` 抛 ComposeError（会话已
    存在）→ 转 AgentServiceError（Studio 200 ok:false）。
    """
    from .. import compose_service

    meta = _require_done_synthesis(run_id)
    body = _synthesis_text(meta)
    slug = _compose_slug(run_id)
    title = f"综合 · {run_id}"
    try:
        directory = compose_service.new_session(slug, title, root=root)
    except compose_service.ComposeError as exc:
        raise AgentServiceError(str(exc)) from None
    blocks = blocks_from_markdown(body)
    if blocks:
        # 复用 compose 的 draft 写路径：块锚协议经 markdown_from_blocks 序列化
        from ...core.composition import markdown_from_blocks
        from ...infra.storage.filesystem import atomic_write_bytes

        atomic_write_bytes(
            directory / compose_service.DRAFT_FILE,
            markdown_from_blocks(blocks).encode("utf-8"),
        )
    root_path = compose_service.sessions_root(root)
    try:
        rel = directory.resolve().relative_to(root_path.resolve()).as_posix()
    except (OSError, ValueError):
        rel = directory.name
    return {"ok": True, "slug": slug, "dir": rel, "blocks": len(blocks)}


def adopt(
    run_id: str,
    *,
    target: str,
    title: str | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """采纳分发（唯一写面入口）：target=material → derived 素材；compose → 会话种稿。"""
    if target == "material":
        return adopt_material(run_id, title=title)
    if target == "compose":
        return adopt_compose(run_id, root=root)
    raise AgentServiceError(f"target 必须是 material/compose 之一，实际: {target!r}")
