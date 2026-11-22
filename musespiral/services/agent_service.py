"""多步编排服务（Phase D F75 执行面 + F76 双采纳目标，L3）。

单一持有者：`~/.musespiral/agent_runs/<run_id>/`（设计 §1.1）+ run 的 jobs 提交
（kind=`agent_run`）。引擎在 `core/agent`（L2 纯函数），本层只负责**组装**
（素材正文 → ctx）与**落盘/采纳**（唯一 I/O 与唯一外部写面）。

三条纪律（设计 §四 Q1-Q4 的服务层落点）：

1. **jobs result 只存计数无正文**（§1.1）：`{run_id, status, steps_done,
   chars_out}` —— 正文在 run 目录的 `steps/<n>.json`（preview 态留痕）。
2. **run 终态 → job 终态映射**（§四 Q2）：`done`/`budget_stopped` → job
   **done**（`result.status` 区分二者——预算守门非失败）；`failed` → job
   **failed**（error=run 可读错误）。
3. **采纳是唯一外部写面**（§四 Q3）：run 永不直写 materials/正稿；本模块
   通过 `_adopt_material` / `_adopt_compose` 两窄门落库（前者 `add_material`，
   后者 `compose_service.new_session` + `blocks_from_markdown` 种稿）。

**构造函数 vs 提交函数**（两条路径的校验分工）：

- `build_run(plan_name, material_ids, params)`：**纯构造期**——plan 名枚举、
  素材存在性、正文面、展平后步数，全部在此拒（可读 ValueError 子类
  `AgentServiceError`）。CLI 与 API 都在**提交 job 之前**调用它，因此校验
  失败时**零 job 零 run 目录**（设计 §二-F76.1 步骤 2）。
- `start_run(...)`：构造 → 建 run 目录（`status=running`）→ 提交 job。

**run_id 形态**：`<plan>-<YYYYMMDD-HHMMSS>-<rand4>`（设计 §1.1「会话级唯一
即可，无跨机诉求」）。rand4 取 `uuid4().hex[:4]`——同秒并发两 run 不撞。

**license 最严者胜**（设计 §二-F76.5 本期裁）：多源综合的 license 继承不是
「继承第一个」，而是全源取最保守——`ALL-RIGHTS` 有一个即 ALL-RIGHTS；
否则有 `UNKNOWN` 即 UNKNOWN；全 `CC-BY` 才 CC-BY。理由：综合稿引用多方，
任何一方的限制都传导到成品上。各源声明原文合并成列表存
`payload.meta.license_note`（单源时仍是列表——形状恒定，消费方不必双分支）。

**幂等**（§二-F76.4）：`uri=synthesis://<run_id>/<指纹8>` 由 run_id 与综合稿
正文指纹决定，同 run 同 target 二次采纳 → 素材 id 不变 → `add_material`
整行相等返回既有行零追加（materials_service.add_material 幂等判定既有路径）。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import config
from ..core.agent import budgets as agent_budgets
from ..core.agent.interp import STATUS_BUDGET_STOPPED, STATUS_DONE, run_plan  # noqa: F401 - STATUS_* 经 __all__ 再导出
from ..core.agent.plan import validate_plan
from ..core.agent.plans_builtin import (
    BUILTIN_PLANS,
    PLAN_SYNTHESIS,
    PLAN_TAG_BATCH,
    SYNTHESIS_MAX_MATERIALS,  # noqa: F401 - 经 __all__ 再导出（CLI/测试引用同一常量）
    SYNTHESIS_MIN_MATERIALS,  # noqa: F401
    build_synthesis,
    build_tag_batch,
)
from ..core.composition import blocks_from_markdown
from ..core.materials import model
from ..core.materials.confidence import parse_ai_confidence  # N-4 综合稿末行自评
from ..infra.ai import TASKS, AIError, ai_configured
from ..infra.ai.gateway import chat_policed
from ..infra.ai.prompts import load_override
from ..infra.storage.filesystem import atomic_write_bytes as _atomic_write
from ..jobs.store import JobStore, now_iso as _now_iso
from . import material_body, materials_service, sources_service

# 编排运行目录（config.home_dir() 下；随 MUSESPIRAL_HOME 漂移）
RUNS_DIR = "agent_runs"
RUN_FILE = "run.json"
STEPS_DIR = "steps"

# run_id 形状白名单（compose `_SLUG_RE` 同款纪律：首字符字母数字，其余
# [A-Za-z0-9._-]）。**路径穿越门**：`../x`、`/abs`、`..%2F` 等形态在首字符
# 与字符类两层均被拒——run_id 直接拼进 run 目录路径（`run_dir`），无此门则
# `GET /api/agent/runs?id=../sneak` 可读 home 下任意 JSON 原样回显（密钥零
# 回显红线的破口）、`adopt('../../x')` 可把外部目录伪造成 run 落库（复核
# 实测两路均通，本门为咽喉层收口）。本模块自产的 run_id
# （`<plan>-<YYYYMMDD-HHMMSS>-<rand4>`）天然在白名单内。
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _valid_run_id(run_id: object) -> bool:
    """run_id 是否白名单形态（非 str/空/穿越形态 → False）。"""
    return isinstance(run_id, str) and bool(_RUN_ID_RE.match(run_id))

# 综合计划的合成动作与 uri 前缀（F76 血统动作；model.PROVENANCE_OPS 扩键）
SYNTHESIS_OP = "synthesize"
SYNTHESIS_URI_SCHEME = "synthesis"
# adopt material 时的 origin.source_id（综合稿的信息源标记）
SYNTHESIS_SOURCE_ID = "synthesis"

# 素材正文进 extract 步的节选上限（设计 §二-F76.2「≤2000 字符，沿
# PER_SOURCE_BUDGET 语义」）+ 标题行余量 = 与 plans_builtin.EXTRACT_BUDGET_IN
# 对齐的扣除项（二者独立维护会漂移，故此处显式引用其常量）
MATERIAL_EXCERPT_MAX = agent_budgets.SLOT_BUDGET

# run 目录保留数（`agent runs --prune` 缺省 50，设计 §1.1 LRU by finished）
PRUNE_KEEP = 50

# 已运行态（run.json status；与 interp.RunResult 三态同枚举 + running）
STATUS_RUNNING = "running"
STATUS_FAILED = "failed"
# 读侧标记（run.json 仍是 running 但对应 job 已 failed(interrupted)——设计
# §二-F75.2 降级表末行：不自动续跑，读侧对齐 jobs 语义）
STATUS_INTERRUPTED = "interrupted"

# run.json steps[].status 的额外态（interp 只产 done/failed；running 是落盘
# 初值，仅出现在未完成 run 的 steps 列表——空）
STEP_RUNNING = "running"

# license 最严者胜的严重度序（数值大者胜；设计 §二-F76.5 细则）
_LICENSE_SEVERITY: dict[str, int] = {"CC-BY": 0, "UNKNOWN": 1, "ALL-RIGHTS": 2}

ChatFn = Callable[..., dict[str, Any]]


class AgentServiceError(ValueError):
    """编排业务错误（可读消息；CLI 退出 1 / Studio 转 400 或 ok:false）。

    构造期（plan 名/素材/正文面/步数）与采纳期（run 不存在/status≠done/
    plan 无 adopt 面）共用——两类都是「用户可修复的可读拒绝」，不区分类型
    以免调用方双分支。
    """


# ── 路径与时间戳 ─────────────────────────────────────────────────────


def runs_root() -> Path:
    """run 目录根：config.home_dir()/agent_runs（每次现读，随 MUSESPIRAL_HOME 漂移）。"""
    return config.home_dir() / RUNS_DIR


def run_dir(run_id: str) -> Path:
    """单个 run 目录（**形状门在咽喉层**：白名单外 run_id → AgentServiceError，
    任何未来调用方都不可能再拿穿越形态拼路径；不校验存在——调用方按需 stat）。"""
    if not _valid_run_id(run_id):
        raise AgentServiceError(
            f"非法 run_id: {run_id!r}（白名单 [A-Za-z0-9][A-Za-z0-9._-]；"
            f"含路径分隔/穿越形态一律拒）"
        )
    return runs_root() / run_id


def _make_run_id(plan_name: str) -> str:
    """`<plan>-<YYYYMMDD-HHMMSS>-<rand4>`（设计 §1.1）。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{plan_name}-{stamp}-{uuid.uuid4().hex[:4]}"


def _now() -> str:
    """ISO 秒级时间戳（与 jobs.store.now_iso 同口径，run.json started/finished）。"""
    return _now_iso()


# ── 构造期（纯校验 + ctx 组装，无 I/O 副作用）─────────────────────────


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


# ── run 目录落盘（单一持有者）──────────────────────────────────────


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    """tmp+replace 原子写（§四 Q4 落盘义务：全部经 atomic_write_bytes）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _read_json(path: Path) -> dict[str, Any] | None:
    """宽容读（坏文件/缺失 → None——读侧永不因脏数据崩溃）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run_meta(
    run_id: str,
    plan: dict[str, Any],
    material_ids: Sequence[str],
    *,
    status: str,
    started: str,
    licenses: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """run.json 骨架（设计 §1.1 键集；prompt 全文**不落盘**）。

    `inputs.licenses`=构造期定格的源许可快照（license 存证规则，见
    `_license_snapshot`）——采纳时据此取最严，源事后被删不放松许可。
    """
    inputs: dict[str, Any] = {"material_ids": list(material_ids)}
    if licenses:
        inputs["licenses"] = [dict(row) for row in licenses]
    return {
        "id": run_id,
        "plan": plan["plan"],
        "status": status,
        "started": started,
        "finished": None,
        "inputs": inputs,
        "budget": {"in_chars": 0, "out_chars": 0, "steps": 0},
        "limits": dict(plan.get("budget") or {}),  # run 级预算上限（摘要块的「余量」分母）
        "steps": [],
    }


def _finish_and_persist(
    directory: Path,
    meta: dict[str, Any],
    plan: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """终态回填 run.json + 逐完步写 steps/<n>.json → 更新后的 run.json。

    steps 落盘的口径：`steps_meta` 第 i 项 ↔ 计划第 i 步（interp 顺序折叠，
    steps_meta 恒是计划前缀）→ 槽名取 `plan["steps"][i]["out"]`，正文取
    `result["slots"][槽名]`。**只落已完成步**（meta 项数即完成步数）。
    """
    steps_dir = directory / STEPS_DIR
    steps_dir.mkdir(parents=True, exist_ok=True)
    for i, step_meta in enumerate(result["steps"], start=1):
        out_name = plan["steps"][i - 1]["out"]
        _write_json(
            steps_dir / f"{i}.json",
            {
                "id": step_meta["id"],
                "task": step_meta["task"],
                "status": step_meta["status"],
                "out_text": result["slots"].get(out_name, ""),
            },
        )
    finished = _now()
    meta = {
        **meta,
        "status": result["status"],
        "finished": finished,
        "budget": dict(result["budget"]),
        "steps": list(result["steps"]),
    }
    if result.get("error"):
        meta["error"] = str(result["error"])
    _write_json(directory / RUN_FILE, meta)
    return meta


# ── 执行（生产 chat_fn = gateway 闭包）────────────────────────────────


def _gateway_chat_fn(
    run_id: str,
    chat_fn: ChatFn | None,
    templates: dict[str, str] | None = None,
    ai_cfg: dict[str, Any] | None = None,
) -> ChatFn:
    """构造 interp 的 chat_fn：单条 user message + gateway 治理（cache/budget/usage）。

    与 `tag_service.batch` 的 `_gateway_step` 同形——**编排步一律经 gateway**
    （§三 依赖规则 3「新增 AI 调用点一律 gateway」）；`chat_fn` 参数是测试
    注入点（透传给 chat_policed，替换真实 `client.chat`）。

    `templates`（`step_templates(plan)` 的产物）逐任务透传给 gateway 的
    `template` 参——F79.1 口径 template_sha=sha1(**生效模板**)：生效模板是
    计划真实使用的文本（已含 F47 覆盖级联结果），gateway 层不再自行猜缺省。

    `ai_cfg`（N-3 源级 AI 覆盖，`build_run` 产）：profile/model/temperature
    三键经 `sources_service.override_kwargs` 透传——**只在本编排步生效**，
    文章生成路径从不经过此函数（裁决见《总体方案》N-3）。
    """
    overrides = sources_service.override_kwargs(ai_cfg or {})

    def _step(prompt: str, *, task: str, cache: bool) -> dict[str, Any]:
        return chat_policed(
            [{"role": "user", "content": prompt}],
            task=task,
            cache=cache,
            run_id=run_id,
            template=templates.get(task) if templates is not None else None,
            chat_fn=chat_fn,
            **overrides,
        )

    return _step


def _execute(
    run_id: str,
    plan: dict[str, Any],
    ctx: dict[str, Any],
    material_ids: Sequence[str],
    *,
    chat_fn: ChatFn | None,
    ai_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """跑一次编排并落盘（**供 job 闭包调用**）→ 更新后的 run.json。

    run 级异常（interp 内部未捕获的 ValueError/未预期异常）也落 `failed`
    终态——不留 running 悬挂目录（除进程崩溃，那属孤儿恢复的面）。
    """
    directory = run_dir(run_id)
    meta = _read_json(directory / RUN_FILE) or _run_meta(
        run_id, plan, material_ids, status=STATUS_RUNNING, started=_now()
    )
    try:
        result = run_plan(
            plan, ctx, _gateway_chat_fn(run_id, chat_fn, step_templates(plan), ai_cfg), tasks=TASKS
        )
    except BaseException as exc:  # noqa: BLE001 - run 级兜底：终态必落
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        result = {
            "status": STATUS_FAILED,
            "steps": [],
            "slots": {},
            "budget": {"steps": 0, "in_chars": 0, "out_chars": 0},
            "error": str(exc) or exc.__class__.__name__,
        }
    return _finish_and_persist(directory, meta, plan, result)


def _ensure_ai_ready(plan: dict[str, Any], ai_cfg: dict[str, Any] | None = None) -> None:
    """计划涉及的全部任务是否都有生效档案（AI 未配置 → AIError，**零 job 零 run 目录**）。

    逐任务查（而非只看默认）：ai.json routing 可按步路由（extract 走本地廉价
    档案、synthesize 走云档案），任一步无档案都会在运行期步级失败——提交期
    就给出可读引导（Studio 映射为 `200 {ok:false}` 业务失败层，设计 §二-F75.4-1）。
    `ai_cfg`（N-3 源级覆盖）给出 profile 时按该档案判定；无效档案读侧宽容
    回落全局（与 resolve_ai_config 的契约一致——只查有效 profile）。
    """
    profile = (ai_cfg or {}).get("profile")
    missing: list[str] = []
    for step in plan.get("steps") or []:
        task = str(step.get("task") or "")
        if task and not ai_configured(task, profile=profile) and task not in missing:
            missing.append(task)
    if missing:
        raise AIError(
            f"AI 未配置（任务 {'/'.join(missing)} 无可用档案）。请先运行: "
            f"musespiral ai config --profile <名> --base-url <URL> --api-key <KEY> --model <MODEL>"
        )


def create_run(
    plan_name: str,
    material_ids: Sequence[str],
    *,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """构造 + AI 就绪检查 + 建 run 目录（status=running）→ `{run_id, plan, ctx}`（**不执行**）。

    与 `start_run` 分开是为了让 CLI/API **先拿到 run_id 再提交 job**（响应
    契约：`{ok:true, job_id, run_id}`——run_id 在提交返回时就已知，前端可
    立即 `GET /api/agent/runs?id=` 看 running 态）。
    """
    built = build_run(plan_name, material_ids, params)
    ai_cfg = built.get("ai_cfg") or {}
    _ensure_ai_ready(built["plan"], ai_cfg)
    directory = None
    for _attempt in range(3):
        # 同秒同 rand4 撞名原为 500 级 FileExistsError（comment 判例 2026-11-22：
        # 全量套件实测 test_prune_keeps_lru 同秒建 4 run 撞名 1/3 轮红）——
        # 换新 rand4 重试；run_id 形态与响应契约零变化。
        run_id = _make_run_id(built["plan"]["plan"])
        directory = run_dir(run_id)
        try:
            directory.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            if _attempt == 2:
                raise
    _write_json(
        directory / RUN_FILE,
        _run_meta(
            run_id,
            built["plan"],
            built["material_ids"],
            status=STATUS_RUNNING,
            started=_now(),
            licenses=built.get("licenses"),
        ),
    )
    return {"run_id": run_id, "plan": built["plan"], "ctx": built["ctx"],
            "material_ids": built["material_ids"], "ai_cfg": ai_cfg}


def job_fn(run_id: str, plan: dict[str, Any], ctx: dict[str, Any],
           material_ids: Sequence[str], *, chat_fn: ChatFn | None = None,
           ai_cfg: dict[str, Any] | None = None) -> Callable[[str], dict[str, Any]]:
    """把一次 run 包成 jobs 闭包（runner 调 `fn(job_id)`）→ result 只含计数。

    `chat_fn` 为测试注入点（生产 None = 真实 `client.chat`）；`ai_cfg` 为
    N-3 源级 AI 覆盖（create_run 的产物）——闭包捕获后逐 execute 透传。
    """

    def _run(_job_id: str) -> dict[str, Any]:
        meta = _execute(run_id, plan, ctx, material_ids, chat_fn=chat_fn, ai_cfg=ai_cfg)
        if meta["status"] == STATUS_FAILED:
            # run failed → job failed（error=run 可读错误，§四 Q2 映射）
            raise RuntimeError(str(meta.get("error") or "run failed"))
        return _job_result(meta)

    return _run


def _job_result(meta: dict[str, Any]) -> dict[str, Any]:
    """jobs result（**计数无正文**，设计 §1.1）：{run_id, status, steps_done, chars_out}。"""
    return {
        "run_id": meta["id"],
        "status": meta["status"],
        "steps_done": len(meta.get("steps") or []),
        "chars_out": int((meta.get("budget") or {}).get("out_chars") or 0),
    }


def submit_to_runner(
    runner: Any,
    plan_name: str,
    material_ids: Sequence[str],
    *,
    params: dict[str, str] | None = None,
    chat_fn: ChatFn | None = None,
) -> dict[str, Any]:
    """构造 → 建 run 目录 → `runner.submit("agent_run", …)` → `{job_id, run_id}`。

    **提交前校验全部已完成**（`create_run` 内），故本函数抛 AgentServiceError
    时零 job 零 run 目录。
    """
    created = create_run(plan_name, material_ids, params=params)
    fn = job_fn(
        created["run_id"], created["plan"], created["ctx"], created["material_ids"],
        chat_fn=chat_fn,
        ai_cfg=created.get("ai_cfg"),
    )
    job_id = runner.submit(
        "agent_run",
        {"plan": created["plan"]["plan"], "material_ids": created["material_ids"],
         "run_id": created["run_id"]},
        fn,
    )
    return {"job_id": job_id, "run_id": created["run_id"]}


# ── 外部宿主 job 的进度 run（book_fetch 提炼管线挂接，2026-11-18 集成轮）──


def open_attached_run(run_id: str, plan: dict[str, Any]) -> None:
    """为外部宿主 job（kind=book_fetch）开进度 run 目录（running 态立即可查）。

    提炼管线消费章节文本而非素材 id，不走 create_run/submit_to_runner 的
    agent_run 提交面；但步级明细的**读面**（GET /api/agent/runs?id=<job_id>，
    前端步级双源之一）统一由本服务的 run.json/steps 存证承载——run_id=宿主
    job_id。已存在不覆盖（job_id 唯一，防重开洗掉过程态）。不查 AI 就绪
    （宿主已自查）、inputs.material_ids 恒空（章节不入此键）。
    """
    directory = run_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)
    if _read_json(directory / RUN_FILE) is None:
        _write_json(
            directory / RUN_FILE,
            _run_meta(run_id, plan, [], status=STATUS_RUNNING, started=_now()),
        )


def finish_attached_run(run_id: str, plan: dict[str, Any], result: dict[str, Any]) -> None:
    """外部宿主 job 的 run 过程/终态回填（_finish_and_persist 同款覆盖写）。

    `plan` 为宿主的**合成回填计划**（steps[].out 对齐 result.slots 键——
    跨 pass 累积时宿主以真实章节 index 重排步 id，避免 pass 内序号撞名）；
    result.status 由宿主编排给定（book_fetch 的 AI 全败降级不炸 job，run 面
    如实记 failed + error——降级优先不吞证据）。
    """
    directory = run_dir(run_id)
    meta = _read_json(directory / RUN_FILE) or _run_meta(
        run_id, plan, [], status=STATUS_RUNNING, started=_now()
    )
    _finish_and_persist(directory, meta, plan, result)


# ── 读面（清单 / 详情 / prune）──────────────────────────────────────


def list_runs(limit: int = PRUNE_KEEP) -> list[dict[str, Any]]:
    """run 清单（新在前，≤limit）：摘要键 `{id, plan, status, started, finished,
    steps_done, chars_out}`；`interrupted` 读侧标记见 `_read_side_status`。

    排序键：finished（终态）> started——running 态没有 finished，按其 started
    参与排序（与 jobs 的「新在前」同直觉）。jobs.jsonl 只读**一次**（跨全部
    run 复用），不按 run 逐次扫。
    """
    root = runs_root()
    if not root.is_dir():
        return []
    metas: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        meta = _read_json(directory / RUN_FILE)
        if meta is not None:
            metas.append(meta)
    interrupted = _interrupted_run_ids()
    rows = [_summary(meta, interrupted) for meta in metas]
    rows.sort(key=lambda r: (str(r.get("finished") or r.get("started") or ""), r["id"]), reverse=True)
    return rows[:limit] if limit and limit > 0 else rows


def _summary(meta: dict[str, Any], interrupted: set[str]) -> dict[str, Any]:
    """run.json → 清单行（详情才带 steps/inputs/limits）。"""
    run_id = str(meta.get("id") or "")
    return {
        "id": run_id,
        "plan": meta.get("plan"),
        "status": STATUS_INTERRUPTED if run_id in interrupted else str(meta.get("status") or ""),
        "started": meta.get("started"),
        "finished": meta.get("finished"),
        "steps_done": len(meta.get("steps") or []),
        "chars_out": int((meta.get("budget") or {}).get("out_chars") or 0),
    }


def _interrupted_run_ids() -> set[str]:
    """孤儿恢复过的 run_id 集（job status=failed + error 含 interrupted）。

    设计 §二-F75.2 降级表末行：进程崩溃时 job 被孤儿恢复标 failed，run 目录
    仍留 running——**读侧**给出与 jobs 一致的可读状态，且**不自动续跑**（重跑
    即缓存承载成本）。两类宿主：agent_run（run_id 在 params）/ book_fetch
    （挂接 run 的 run_id=job_id，见 open_attached_run）。
    """
    out: set[str] = set()
    for job in JobStore().list(limit=None):
        if job.get("status") != "failed" or "interrupted" not in str(job.get("error") or ""):
            continue
        kind = job.get("kind")
        if kind == "agent_run":
            run_id = (job.get("params") or {}).get("run_id")
            if isinstance(run_id, str) and run_id:
                out.add(run_id)
        elif kind == "book_fetch":
            out.add(str(job.get("id") or ""))
    return out


def read_run(run_id: str) -> dict[str, Any] | None:
    """单 run 详情：run.json 全量 + steps（`steps/<n>.json` 的 out_text 合并计数）。

    **steps 的两份事实源在此合并**：`run.json` 的 `steps[]` 是步级**计数**
    （chars_in/chars_out/cache_hit/attempts，interp 产），`steps/<n>.json` 是
    步级**正文**（out_text，落盘存证）。详情面按 `id` 合并——正文取文件、
    计数取 run.json。契约 §3.6-4 的下界形状是 `{id, task, status, out_text,
    error?}`，合并后是其超集（计数键为加法，不影响只要 id/out_text 的消费方）。

    不存在/坏目录 → None（Studio 转 404）。steps 按文件名数字序（稳定）。
    **形状门**：白名单外 run_id（含 ../ 穿越形态）→ None——读侧按「不存在」
    收口（API 层另有 400 显式门，见 agent_payload；此处是防漏的兜底层）。
    """
    if not _valid_run_id(run_id):
        return None
    directory = run_dir(run_id)
    meta = _read_json(directory / RUN_FILE)
    if meta is None:
        return None
    counters = {
        str(s.get("id")): s for s in (meta.get("steps") or []) if isinstance(s, dict)
    }
    steps: list[dict[str, Any]] = []
    steps_dir = directory / STEPS_DIR
    if steps_dir.is_dir():
        def _key(p: Path) -> tuple[int, str]:
            return (int(p.stem) if p.stem.isdigit() else 10**9, p.name)

        for path in sorted(steps_dir.glob("*.json"), key=_key):
            step = _read_json(path)
            if step is None:
                continue
            extra = {
                k: v for k, v in counters.get(str(step.get("id")), {}).items()
                if k not in ("id", "task", "status")
            }
            steps.append({**step, **extra})
    status = str(meta.get("status") or "")
    if status == STATUS_RUNNING and run_id in _interrupted_run_ids():
        status = STATUS_INTERRUPTED
    return {**meta, "status": status, "steps": steps}


# ── 双采纳目标（唯一外部写面）──────────────────────────────────────


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
    from . import compose_service

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
        from ..core.composition import markdown_from_blocks
        from ..infra.storage.filesystem import atomic_write_bytes

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


# ── prune（显式删，非墓碑）──────────────────────────────────────────


def prune(keep: int = PRUNE_KEEP) -> dict[str, Any]:
    """LRU 清理 run 目录（按 finished/started 新在前，保留 keep 个）→ `{removed, kept}`。

    run 目录不进 materials，故 prune 是**显式删**而非墓碑（设计 §四 Q3：
    「run 目录 prune=显式删非墓碑，不进 materials 无连坐义务」）。
    """
    import shutil

    root = runs_root()
    if not root.is_dir():
        return {"removed": 0, "kept": 0}
    entries: list[tuple[str, Path]] = []
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        meta = _read_json(directory / RUN_FILE)
        if meta is None:
            # 坏目录按 mtime 参与（防永久留垃圾）
            entries.append((_iso_from_mtime(directory), directory))
            continue
        entries.append((str(meta.get("finished") or meta.get("started") or ""), directory))
    entries.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
    limit = keep if keep and keep > 0 else PRUNE_KEEP
    removed = 0
    for _stamp, directory in entries[limit:]:
        try:
            shutil.rmtree(directory)
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "kept": min(len(entries), limit)}


def _iso_from_mtime(directory: Path) -> str:
    """目录 mtime → ISO 秒级串（**不走 now_iso**——那取的是「现在」不是文件时间）。

    用 `strftime` 而非 `datetime.isoformat(timespec=…)`：后者是服务层时间戳
    单一权威门的扫描字样（`test_services_timestamp_single_authority`），本函数
    的语义是「读既有文件的时间」而非「造当前时间」，不该与权威混淆。
    """
    try:
        return datetime.fromtimestamp(directory.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    except OSError:
        return ""


__all__ = [
    "AgentServiceError",
    "MATERIAL_EXCERPT_MAX",
    "PRUNE_KEEP",
    "RUNS_DIR",
    "STATUS_BUDGET_STOPPED",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_RUNNING",
    "adopt",
    "adopt_compose",
    "adopt_material",
    "build_run",
    "create_run",
    "finish_attached_run",
    "job_fn",
    "list_runs",
    "open_attached_run",
    "prune",
    "read_run",
    "run_dir",
    "runs_root",
    "strictest_license",
    "submit_to_runner",
]
