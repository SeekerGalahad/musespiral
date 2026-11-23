"""agent 构造簇——run 的纯校验期与 ctx 组装（零 I/O 写，仅读素材库）。

原 ``services/agent_service.py`` 的「构造期」段（W2-B4 拆分落点）：
``build_run`` 把 plan 名+素材 id+params 校验成可直接喂 ``interp.run_plan``
的 ``{plan, ctx, …}``——plan 名枚举/素材存在性/正文面/展平后步数**全部在
提交 job 之前拒**（零 job 零 run 目录，设计 §二-F76.1 步骤 2）。

同簇携带：F47 覆盖级联（prompts.d 是 I/O，interp 是 L2 纯函数不能做，
落点在服务层）与 license 快照（构造期定格——采纳只读快照不看现库）。
语义逐字未动，本模块只改落点。
"""
from __future__ import annotations

from typing import Any, Sequence

from ...core.agent import budgets as agent_budgets
from ...core.agent.plan import validate_plan
from ...core.agent.plans_builtin import (
    BUILTIN_PLANS,
    PLAN_SYNTHESIS,
    PLAN_TAG_BATCH,
    build_synthesis,
    build_tag_batch,
)
from ...infra.ai import TASKS
from ...infra.ai.prompts import load_override
from .. import material_body, materials_service, sources_service
from .store import AgentServiceError

# 素材正文进 extract 步的节选上限（设计 §二-F76.2「≤2000 字符，沿
# PER_SOURCE_BUDGET 语义」）+ 标题行余量 = 与 plans_builtin.EXTRACT_BUDGET_IN
# 对齐的扣除项（二者独立维护会漂移，故此处显式引用其常量）
MATERIAL_EXCERPT_MAX = agent_budgets.SLOT_BUDGET

# license 最严者胜的严重度序（数值大者胜；设计 §二-F76.5 细则）
_LICENSE_SEVERITY: dict[str, int] = {"CC-BY": 0, "UNKNOWN": 1, "ALL-RIGHTS": 2}


def _resolve_plan(plan_name: str, material_ids: Sequence[str]) -> dict[str, Any]:
    """按 plan 名调内置构造器（枚举外/构造器边界 → AgentServiceError）。"""
    if plan_name == PLAN_SYNTHESIS:
        try:
            return build_synthesis(material_ids)
        except ValueError as exc:
            raise AgentServiceError(str(exc)) from None
    if plan_name == PLAN_TAG_BATCH:
        try:
            return build_tag_batch(material_ids)
        except ValueError as exc:
            raise AgentServiceError(str(exc)) from None
    raise AgentServiceError(
        f"未知计划: {plan_name!r}（可选: {'/'.join(BUILTIN_PLANS)}）"
    )


def _load_materials(material_ids: Sequence[str]) -> list[dict[str, Any]]:
    """取素材并校验正文面（不存在/已删/无正文面 → AgentServiceError 含 id 与 type）。

    **无正文面在此拒而不是留给运行期**：radar_signal/link 的 extract 步只会
    把 URL 当正文喂给模型，产出与「素材内容」无关的噪声笔记——构造期拒让
    用户在提交前就知道该换素材（设计 §二-F76.5 红线）。
    """
    materials: list[dict[str, Any]] = []
    for mid in material_ids:
        material = materials_service.get_material(mid)
        if material is None:
            raise AgentServiceError(f"素材不存在或已删: {mid!r}")
        try:
            material_body.material_text(material, max_chars=MATERIAL_EXCERPT_MAX)
        except material_body.MaterialTextError as exc:
            raise AgentServiceError(f"素材 {mid!r}: {exc}") from None
        materials.append(material)
    return materials


def _excerpts(materials: Sequence[dict[str, Any]]) -> list[str]:
    """素材正文节选（≤MATERIAL_EXCERPT_MAX）+ 标题行——ctx["materials"] 的形状。

    标题行让 extract 步的模型知道「这是哪一篇」（carry 到 synthesize 时笔记
    自带出处），同时保持「step 的 inputs 键名不渲染进 prompt」的口径：标题
    是**内容**的一部分而非指令。
    """
    out: list[str] = []
    for material in materials:
        body = material_body.material_text(material, max_chars=MATERIAL_EXCERPT_MAX)
        title = str((material.get("origin") or {}).get("title") or material["id"])
        out.append(f"【素材】{title}\n{body}")
    return out


# ── F47 覆盖级联（agent 步模板，设计 §三-D3）────────────────────────


def apply_prompt_overrides(plan: dict[str, Any]) -> dict[str, Any]:
    """把 `prompts.d/<task>.txt` 覆盖级联应用到计划的步模板（浅拷贝计划、
    深拷贝 steps）。

    **为什么在服务层而不在 interp**：interp 是 L2 纯函数（架构门 D1 禁
    import infra），级联解析（读 `~/.musespiral/prompts.d/`）是 I/O——必须在这里
    完成。行为：`step.inputs.instruction` 在 `load_override(task)` 非空时被
    整体覆盖（与 refine/compose/tag_suggest 的覆盖语义一致：存在即整体
    覆盖，不做拼接）。返回新 dict，入参 plan 不被改写。
    """
    out = {**plan, "steps": [dict(step) for step in (plan.get("steps") or [])]}
    for step in out["steps"]:
        task = str(step.get("task") or "")
        inputs = step.get("inputs")
        if not task or not isinstance(inputs, dict):
            continue
        if not isinstance(inputs.get("instruction"), str):
            continue
        override = load_override(task)
        if override:
            step["inputs"] = {**inputs, "instruction": override}
    return out


def step_templates(plan: dict[str, Any]) -> dict[str, str]:
    """计划 → `{task: 生效模板文本}`（每任务取首个带 instruction 的步）。

    gateway 的 `template` 参数数据面（F79.1 口径：template_sha=sha1(生效
    模板)——生效模板必须是被本计划真实使用的文本，含覆盖级联结果）。
    """
    templates: dict[str, str] = {}
    for step in plan.get("steps") or []:
        task = str(step.get("task") or "")
        instruction = (step.get("inputs") or {}).get("instruction")
        if task and isinstance(instruction, str) and task not in templates:
            templates[task] = instruction
    return templates


def _apply_source_template(plan: dict[str, Any], override: str) -> dict[str, Any]:
    """把源级 prompt_override 应用到全部步（浅拷贝 plans/steps，不改入参）。

    与 `apply_prompt_overrides` 同形态：覆盖 `inputs.instruction`；只在源级
    文本生效（回退链已在此之前由 apply_prompt_overrides 处理全局 prompts.d）。
    """
    out = {**plan, "steps": [dict(step) for step in (plan.get("steps") or [])]}
    for step in out["steps"]:
        inputs = step.get("inputs")
        if isinstance(inputs, dict) and isinstance(inputs.get("instruction"), str):
            step["inputs"] = {**inputs, "instruction": override}
    return out


def strictest_license(materials: Sequence[dict[str, Any]]) -> str:
    """license 最严者胜（设计 §二-F76.5）：ALL-RIGHTS > UNKNOWN > CC-BY。

    枚举外/缺失值按 UNKNOWN 处理（读侧宽容——脏数据不该让采纳失败，但也
    不该被当成宽松许可）。**零源（全部已删）→ UNKNOWN**：无从判断即最保守，
    不因「没有反对意见」而落到 CC-BY。
    """
    if not materials:
        return "UNKNOWN"
    worst = 0
    for material in materials:
        value = str(material.get("license") or "UNKNOWN")
        if value not in _LICENSE_SEVERITY:
            value = "UNKNOWN"
        worst = max(worst, _LICENSE_SEVERITY[value])
    for name, severity in _LICENSE_SEVERITY.items():
        if severity == worst:
            return name
    return "UNKNOWN"


def _license_snapshot(materials: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """源许可快照（构造期定格）：`[{id, license, license_note}]`（选材序）。

    **为什么构造期定格**（license 存证规则，F76.5）：综合稿引用多方，license
    必须对「run 实际引用的源集合」取最严——run 存证（run.json inputs）保留了
    全部源 id，若 adopt 时才回现库取值，删掉一个 ALL-RIGHTS 源就能让综合稿
    许可变宽松（复核实测）。快照随 run.json 落盘，采纳只读快照不看现库。
    """
    snapshot: list[dict[str, Any]] = []
    for material in materials:
        meta = (material.get("payload") or {}).get("meta")
        note = meta.get("license_note") if isinstance(meta, dict) else None
        snapshot.append(
            {
                "id": str(material.get("id") or ""),
                "license": str(material.get("license") or "UNKNOWN"),
                "license_note": note.strip() if isinstance(note, str) and note.strip() else "",
            }
        )
    return snapshot


def build_run(
    plan_name: str,
    material_ids: Sequence[str],
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造并校验一次 run → `{plan, ctx, ai_cfg}`（**零 I/O 写**，仅读素材库）。

    校验链（任一失败 → AgentServiceError，调用方据此零 job 零 run 目录）：
      ① plan 名枚举 + 构造器自身边界（synthesis 篇数 [2,8]、去重、非空）；
      ② 素材存在性（不存在/已删）；
      ③ 素材正文面（radar_signal/link → 可读错误含 type 与 id）；
      ④ **展平后步数**（`validate_plan` 按 `budgets.RUN_STEPS` 裁决；R3.5 批
         8→16 后 synthesis 的 8 篇（9 步）已在可跑区间内）。

    返回的 ctx 形如 `{"materials": [...], "params": {...}}`——直接喂
    `interp.run_plan`（槽位 `{{materials[i]}}` / `{{params.k}}` 的取值面）。
    `ai_cfg` = 源级 AI 覆盖（N-3）：同源批量才生效，多源混合 → {} 回落全局。
    """
    ids = [str(mid).strip() for mid in material_ids]
    plan = _resolve_plan(plan_name, ids)
    # F47 覆盖级联（设计 §三-D3「agent 步免费继承」的落点）：interp 是 L2
    # 纯函数不能 import infra，级联解析必须在服务层组装期完成——
    # prompts.d/<task>.txt 存在即整体覆盖该步内置模板。
    plan = apply_prompt_overrides(plan)
    # ④ 展平后步数：construction 允许 8 篇（产数据可检视），执行前必须可解释
    try:
        validate_plan(plan, tasks=TASKS)
    except ValueError as exc:
        raise AgentServiceError(str(exc)) from None
    materials = _load_materials(ids)
    # N-3：源级 AI 覆盖（同源才生效；多源综合无「一个源配置」语义 → {}）
    ai_cfg = sources_service.ai_overrides(materials)
    if ai_cfg.get("prompt_override"):
        # 源级 prompt_override > 全局 prompts.d 覆盖（「这个源的内容特性」最具体）
        plan = _apply_source_template(plan, ai_cfg["prompt_override"])
    clean_params = {str(k): str(v) for k, v in (params or {}).items()}
    return {
        "plan": plan,
        "ctx": {"materials": _excerpts(materials), "params": clean_params},
        "material_ids": ids,
        "licenses": _license_snapshot(materials),
        "ai_cfg": ai_cfg,
    }
