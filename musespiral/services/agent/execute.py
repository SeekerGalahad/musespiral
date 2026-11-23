"""agent 执行簇——跑计划、job 提交闭包、外部宿主挂接与读面（清单/详情）。

原 ``services/agent_service.py`` 的「执行」「读面」段（W2-B4 拆分落点）。
三条纪律（设计 §四 Q1-Q4 的服务层落点）：

1. **jobs result 只存计数无正文**（§1.1）：`{run_id, status, steps_done,
   chars_out}` —— 正文在 run 目录的 `steps/<n>.json`（preview 态留痕）。
2. **run 终态 → job 终态映射**（§四 Q2）：`done`/`budget_stopped` → job
   **done**（`result.status` 区分二者——预算守门非失败）；`failed` → job
   **failed**（error=run 可读错误）。

读面把 run.json 的步级**计数**与 steps/<n>.json 的步级**正文**合并（详情面
按 `id` 合并）；孤儿恢复的 run 在读侧标 `interrupted`，不自动续跑。
语义逐字未动，本模块只改落点。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from ...core.agent.interp import STATUS_DONE, run_plan
from ...core.agent.interp import STATUS_BUDGET_STOPPED  # noqa: F401 - 经包 __all__ 再导出
from ...infra.ai import TASKS, AIError, ai_configured
from ...infra.ai.gateway import chat_policed
from ...jobs.store import JobStore
from .. import sources_service
from .build import build_run, step_templates
from .store import (
    PRUNE_KEEP,
    RUN_FILE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    STEPS_DIR,
    _make_run_id,
    _now,
    _read_json,
    _run_meta,
    _valid_run_id,
    _write_json,
    run_dir,
    runs_root,
)

ChatFn = Callable[..., dict[str, Any]]


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


def _job_result(meta: dict[str, Any]) -> dict[str, Any]:
    """jobs result（**计数无正文**，设计 §1.1）：{run_id, status, steps_done, chars_out}。"""
    return {
        "run_id": meta["id"],
        "status": meta["status"],
        "steps_done": len(meta.get("steps") or []),
        "chars_out": int((meta.get("budget") or {}).get("out_chars") or 0),
    }


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


# ── 读面（清单 / 详情）──────────────────────────────────────────────


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
