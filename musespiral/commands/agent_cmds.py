"""智能体编排命令组：agent（plans / run / runs / adopt）——Phase D F75.1/F76.1。

CLI 是薄壳（判例 6）：构造期校验、run 目录、采纳全部下沉 `services.agent_service`；
本模块只做参数解析、**job 轮询与终端呈现**。

**为什么 CLI 走 job 层**（与「CLI 保持同步」既有约定的边界说明）：F75.1 的
M0 交互流明写「提交 jobs（kind=agent_run）→ 打印 job_id 与 run_id → CLI
轮询 job 打印步级进度」——编排是分钟级多步 AI 长动作，同步阻塞会让终端在
无输出状态挂住。故本命令**进程内建一次性 runner**（`max_workers=1`）、提交后
按 jobs 语义轮询到终态再退出：对外仍是「一条命令一次执行」，对内拿到步级
进度行。**刻意不调 `runner.startup()`**——孤儿恢复会把 Studio 正在跑的
job 误标 failed（那是 Studio 进程启动时的职责，CLI 无权代做）。
"""
from __future__ import annotations

import time

import click

from ..core.agent.plans_builtin import list_builtin_plans

# 轮询间隔（与前端 trackJob 的 800ms 纪律同量级；CLI 只看步级粒度）
_POLL_SECONDS = 0.8
# 轮询总预算（秒）：超时即放弃等待（job 仍在后台跑完，run 目录可事后查）
_POLL_TIMEOUT = 600


def _fail(message: str, code: int = 1):
    click.echo(f"❌ {message}", err=True)
    raise SystemExit(code)


@click.group()
def agent() -> None:
    """多步 AI 编排：计划清单 / 触发综合 / 运行记录 / 采纳（Phase D）。"""


def _parse_params(pairs: tuple[str, ...]) -> dict[str, str]:
    """`--param k=v` → dict（缺 `=` → 可读报错）。"""
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            _fail(f"--param 必须是 k=v 形态，实际: {raw!r}")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            _fail(f"--param 的键不能为空: {raw!r}")
        out[key] = value
    return out


@agent.command("plans")
def plans_cmd() -> None:
    """列出内置计划（名/步数区间/预算/一句话用途）。"""
    rows = list_builtin_plans()
    click.echo(f"{'plan':<16} {'steps':<8} 用途 / 预算")
    for row in rows:
        click.echo(f"{row['plan']:<16} {str(row['steps']):<8} {row['purpose']}")
        click.echo(f"{'':<25} {row['budget']}")


def _print_step_line(index: int, total: int, step: dict) -> None:
    """步级进度行：`[2/4] extract_2 2400→1750 字 cache✓`。"""
    badge = " cache✓" if step.get("cache_hit") else ""
    retries = f" 重试{step['attempts']}" if int(step.get("attempts") or 1) > 1 else ""
    click.echo(
        f"[{index}/{total}] {step.get('id')!s:<12} "
        f"{int(step.get('chars_in') or 0)}→{int(step.get('chars_out') or 0)} 字{badge}{retries}"
    )


def _watch(runner, job_id: str, run_id: str) -> dict:
    """轮询 job 至终态，边轮询边打印新完成的步（**只打印新步**，不刷屏）。

    job 行自带 `phase` 不用于步级（agent_run 的步级事实源是 run.json）——
    以「已打印步数」为水位，每次往 run.json 捞新增步。
    """
    from ..services import agent_service

    printed = 0
    deadline = time.monotonic() + _POLL_TIMEOUT
    job: dict = {}
    total = 0
    while True:
        job = runner.get(job_id) or {}
        detail = agent_service.read_run(run_id) or {}
        steps = detail.get("steps") or []
        limits = detail.get("limits") or {}
        total = int(limits.get("steps") or 0) or len(steps)
        while printed < len(steps):
            _print_step_line(printed + 1, max(total, printed + 1), steps[printed])
            printed += 1
        status = str(job.get("status") or "")
        if status in ("done", "failed"):
            return job
        if time.monotonic() > deadline:
            click.echo(f"⏱ 轮询超时（{_POLL_TIMEOUT}s）——job 仍在后台，稍后用 "
                       f"musespiral agent runs {run_id} 查看", err=True)
            return job
        time.sleep(_POLL_SECONDS)


def _summary_block(job: dict, run_id: str) -> None:
    """摘要块：status / steps_done / chars_in·out / cache_hits / 预算余量 + 采纳引导。"""
    from ..services import agent_service

    detail = agent_service.read_run(run_id) or {}
    result = job.get("result") or {}
    budget = detail.get("budget") or {}
    limits = detail.get("limits") or {}
    steps = detail.get("steps") or []
    hits = sum(1 for s in steps if s.get("cache_hit"))
    # run 级预算键是 in_chars/out_chars（core/agent/budgets.BUDGET_KEYS 单一口径）；
    # 与步级的 chars_in/chars_out 是两套键，不可混读
    used_in = int(budget.get("in_chars") or 0)
    used_out = int(budget.get("out_chars") or 0)
    click.echo("")
    click.echo(f"✅ 完成: status={result.get('status') or detail.get('status')} "
               f"steps_done={result.get('steps_done', len(steps))} "
               f"chars_in={used_in} chars_out={used_out} "
               f"cache_hits={hits}")
    if limits:
        remaining_in = int(limits.get("in_chars") or 0) - used_in
        remaining_out = int(limits.get("out_chars") or 0) - used_out
        click.echo(f"   预算余量: in {remaining_in} 字 / out {remaining_out} 字 / "
                   f"steps {int(limits.get('steps') or 0) - len(steps)} 步")
    if detail.get("plan") == "synthesis_v1":
        click.echo("   采纳: musespiral agent adopt " + run_id + " --target material  "
                   "（或 --target compose 发起创作会话）")


def _print_synthesis_preview(run_id: str) -> None:
    """done 后打印综合稿全文（run 目录末步 out_text）。"""
    from ..services import agent_service

    detail = agent_service.read_run(run_id) or {}
    text = ""
    for step in reversed(detail.get("steps") or []):
        if str(step.get("task")) == "agent_synthesize":
            text = str(step.get("out_text") or "")
            break
    if text.strip():
        click.echo("")
        click.echo("—" * 40)
        click.echo(text.strip())
        click.echo("—" * 40)


@agent.command("run")
@click.argument("plan", type=click.Choice(["synthesis_v1", "tag_batch"]))
@click.option("--material", "materials", multiple=True, required=True,
              help="素材 id（可重复；synthesis_v1 需 2-8 篇，tag_batch 每批 ≤8）")
@click.option("--param", "params", multiple=True, help="计划参数 k=v（可重复；模板可用 {{params.k}} 引用）")
def run_cmd(plan: str, materials: tuple[str, ...], params: tuple[str, ...]) -> None:
    """触发一次编排（构造期校验 → job → 步级进度 → 摘要与预览）。

    构造期校验失败（篇数/素材不存在/无正文面/展平超步数）→ 可读错误退出，
    **零 job 零 run 目录**。
    """
    from ..infra.ai import AIError
    from ..jobs.runner import JobRunner
    from ..services import agent_service

    parsed = _parse_params(params)
    runner = JobRunner(max_workers=1)
    try:
        try:
            out = agent_service.submit_to_runner(
                runner, plan, list(materials), params=parsed
            )
        except agent_service.AgentServiceError as exc:
            _fail(str(exc))
        except AIError as exc:
            _fail(str(exc))
        click.echo(f"job_id: {out['job_id']}")
        click.echo(f"run_id: {out['run_id']}")
        click.echo("")
        job = _watch(runner, out["job_id"], out["run_id"])
        if str(job.get("status")) == "failed":
            _summary_block(job, out["run_id"])
            _fail(str(job.get("error") or "编排失败"))
        _summary_block(job, out["run_id"])
        if plan == "synthesis_v1":
            _print_synthesis_preview(out["run_id"])
    finally:
        runner.shutdown(wait=True)


@agent.command("runs")
@click.argument("run_id", required=False)
@click.option("--prune", "prune", is_flag=True,
              help="LRU 清理旧 run 目录（保留最近 N 个，缺省 50）")
@click.option("--keep", "keep", default=50, show_default=True, type=int,
              help="--prune 保留份数")
def runs_cmd(run_id: str | None, prune: bool, keep: int) -> None:
    """运行记录：清单（无参）/ 单 run 详情（含各步 out_text 预览）/ --prune 清理。"""
    from ..services import agent_service

    if prune:
        if run_id:
            _fail("--prune 与 <run_id> 不能同时给出")
        result = agent_service.prune(keep=keep)
        click.echo(f"🧹 已清理 {result['removed']} 个 run，保留 {result['kept']} 个。")
        return
    if run_id:
        detail = agent_service.read_run(run_id)
        if detail is None:
            _fail(f"run 不存在（可能已被 prune 清理）: {run_id}")
        click.echo(f"run_id: {detail.get('id')}  plan: {detail.get('plan')}  "
                   f"status: {detail.get('status')}")
        click.echo(f"started: {detail.get('started')}  finished: {detail.get('finished')}")
        budget = detail.get("budget") or {}
        click.echo(f"budget: in {budget.get('in_chars', 0)} / out {budget.get('out_chars', 0)} "
                   f"/ steps {budget.get('steps', 0)}")
        if detail.get("error"):
            click.echo(f"error: {detail['error']}")
        click.echo("")
        for i, step in enumerate(detail.get("steps") or [], start=1):
            click.echo(f"[{i}] {step.get('id')}  task={step.get('task')}  status={step.get('status')}")
            preview = str(step.get("out_text") or "").strip().replace("\n", " ")[:120]
            if preview:
                click.echo(f"    {preview}…")
        return
    rows = agent_service.list_runs(limit=keep if keep > 0 else 50)
    if not rows:
        click.echo("（无 run 记录：musespiral agent run synthesis_v1 --material <id> --material <id>）")
        return
    click.echo(f"{'run_id':<40} {'plan':<14} {'status':<12} {'步':<4} {'out':<8} 完成时间")
    for row in rows:
        click.echo(f"{str(row['id']):<40} {str(row['plan']):<14} {str(row['status']):<12} "
                   f"{row['steps_done']:<4} {row['chars_out']:<8} {row.get('finished') or '（进行中）'}")


@agent.command("adopt")
@click.argument("run_id")
@click.option("--target", required=True, type=click.Choice(["material", "compose"]),
              help="采纳目标：material=存为 derived 素材；compose=发起创作会话")
@click.option("--title", default=None, help="素材标题（target=material；缺省含 run_id）")
@click.option("--root", "root", default=None, type=click.Path(file_okay=False),
              help="文章根目录（target=compose；缺省 ~/musespiral-articles）")
def adopt_cmd(run_id: str, target: str, title: str | None, root: str | None) -> None:
    """采纳一次 done 的 synthesis run（唯一外部写面；同 run 二次采纳幂等）。"""
    from ..services import agent_service

    try:
        out = agent_service.adopt(run_id, target=target, title=title, root=root)
    except agent_service.AgentServiceError as exc:
        _fail(str(exc))
    if target == "material":
        click.echo(f"✅ 已存为素材: {out['id']}")
        click.echo(f"   深链: #material?m={out['id']}")
        click.echo(f"   license: {out['material']['license']}（最严者胜）")
    else:
        click.echo(f"✅ 已建创作会话: {out['slug']}（{out['blocks']} 块已种入 draft.md）")
        click.echo(f"   目录: {out['dir']}")
        click.echo("   下一步: musespiral compose add / generate / decide / adopt")
        click.echo("   注：素材未自动挂入会话（素材→SourceRef 需路径面，请手动 compose add）")


__all__ = ["agent"]
