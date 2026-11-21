"""一键金路径命令组：pipeline run（E1，组合批 2026-11-17）。

CLI 是薄壳（agent_cmds 判例 6 同款）：构造期校验、五步执行体全部下沉
``services/pipeline_service``；本模块只做参数解析、**job 轮询与终端呈现**。

**为什么 CLI 走 job 层**（agent run / topic-radar 同款纪律）：管线含
LLM 步（title）与多平台批量生成（adapt 四平台+pack 四包），分钟级长动作
同步阻塞会让终端无输出挂住。故本命令**进程内建一次性 runner**
（``max_workers=1``）、提交后轮询到终态再退出：对外仍是「一条命令一次
执行」，对内按 ``job.phase`` 打印步级进度。**刻意不调 ``runner.startup()``**
——孤儿恢复会把 Studio 正在跑的 job 误标 failed（那是 Studio 进程启动时
的职责，CLI 无权代做）。
"""
from __future__ import annotations

import time

import click

# 轮询间隔（agent_cmds 同款 0.8s；与前端 trackJob 的 800ms 纪律同量级）
_POLL_SECONDS = 0.8
# 轮询总预算（秒）：超时即放弃等待（job 仍在后台跑完，jobs list 可事后查）
_POLL_TIMEOUT = 900

_MARKS = {"ok": "✅", "skipped": "⏭️ ", "failed": "❌"}


def _fail(message: str, code: int = 1):
    click.echo(f"❌ {message}", err=True)
    raise SystemExit(code)


@click.group()
def pipeline() -> None:
    """一键金路径：check→title→cover→adapt→pack 五步串行（E1，失败即停）。"""


def _watch(runner, job_id: str) -> dict:
    """轮询 job 至终态，phase 变化即打印（步级进度，只打印新 phase 不刷屏）。"""
    printed = ""
    deadline = time.monotonic() + _POLL_TIMEOUT
    job: dict = {}
    while True:
        job = runner.get(job_id) or {}
        phase = str(job.get("phase") or "")
        if phase and phase != printed:
            click.echo(f"[{phase}]")
            printed = phase
        status = str(job.get("status") or "")
        if status in ("done", "failed"):
            return job
        if time.monotonic() > deadline:
            click.echo(
                f"⏱ 轮询超时（{_POLL_TIMEOUT}s）——job 仍在后台，"
                f"稍后 musespiral jobs list 查看", err=True
            )
            return job
        time.sleep(_POLL_SECONDS)


def _print_steps(result: dict) -> None:
    """终态步表：`[1/5] check  ✅ 审核通过…` + 各步产物行。"""
    steps = result.get("steps") or []
    total = len(steps) or 5
    for i, s in enumerate(steps, 1):
        mark = _MARKS.get(str(s.get("status")), "·")
        click.echo(f"[{i}/{total}] {str(s.get('step')):<6} {mark} {s.get('detail', '')}")
        for a in s.get("artifacts") or []:
            click.echo(f"        {a}")


@pipeline.command("run")
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--skip",
    default="",
    help="跳过步骤（逗号分隔：check,title,cover,adapt,pack）",
)
@click.option(
    "--keep-cover",
    is_flag=True,
    help="cover.png 已存在时跳过重生成（幂等续跑；缺省重生成覆盖）",
)
def run_cmd(article: str, skip: str, keep_cover: bool) -> None:
    """执行五步金路径（提交 job → 步级进度 → 步表与产物清单）。

    构造期校验失败（文章不存在 / skip 枚举外）→ 可读错误退出，**零 job**。
    """
    from ..jobs.runner import JobRunner
    from ..services import pipeline_service

    skip_list = [s.strip() for s in skip.split(",") if s.strip()]
    runner = JobRunner(max_workers=1)
    try:
        try:
            job_id = pipeline_service.submit_to_runner(
                runner, article, skip=skip_list, skip_existing_cover=keep_cover
            )
        except pipeline_service.PipelineServiceError as exc:
            _fail(str(exc))
        click.echo(f"job_id: {job_id}")
        click.echo("")
        job = _watch(runner, job_id)
        result = job.get("result") or {}
        click.echo("")
        _print_steps(result)
        if str(job.get("status")) == "failed":
            _fail(str(job.get("error") or "金路径失败"))
        click.echo(
            f"✅ 金路径完成：{result.get('steps_done', 0)}/5 步，"
            f"产物 {len(result.get('artifacts') or [])} 件"
        )
        click.echo("   物料包按各 README.md 指引人工粘贴发布（机器不做最后一步）")
    finally:
        runner.shutdown(wait=True)


__all__ = ["pipeline"]
