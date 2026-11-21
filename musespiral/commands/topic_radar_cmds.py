"""选题雷达命令：musespiral topic-radar（Phase D F78.1，L4 薄壳）。

编排全部下沉 services/topic_radar_service；本模块只做参数解析、job 轮询
与终端呈现（agent_cmds 同款纪律：进程内一次性 runner max_workers=1，
提交后按 jobs 语义轮询到终态——快照含 AI 增强（top5 次 gateway 调用），
同步阻塞会让终端无输出挂住；刻意不调 runner.startup()，孤儿恢复是 Studio
进程启动时的职责）。

无 --refresh 时 latest.json 已存在即直接打印既有快照（不建 job 不重算）；
--refresh 强制重算：覆盖 latest + 历史追加（≤20 份超限删最旧）。
"""
from __future__ import annotations

import sys
import time

import click

# 轮询间隔（agent_cmds 同款；与前端 trackJob 的 800ms 纪律同量级）
_POLL_SECONDS = 0.8
_POLL_TIMEOUT = 300


def _fail(message: str, code: int = 1):
    click.echo(f"❌ {message}", err=True)
    raise SystemExit(code)


def _print_snapshot(snapshot: dict) -> None:
    """候选表：序位 [kind 徽] 标题 · score · reasons 首条（+AI 角度行），
    表尾排除注记与灰灯行（F78.1）。"""
    candidates = snapshot.get("candidates") or []
    kept = [c for c in candidates if not c.get("excluded")]
    excluded = [c for c in candidates if c.get("excluded")]
    inputs = snapshot.get("inputs") or {}
    click.echo(f"选题雷达快照 {snapshot.get('generated_at', '')}  "
               f"ai_enhanced_n={snapshot.get('ai_enhanced_n', 0)}")
    if not kept:
        click.echo("（无候选：检查素材库 radar_signal/星标与 KB 根配置）")
    for i, c in enumerate(kept, 1):
        click.echo(f"{i:>2}. [{str(c.get('kind') or ''):<5}] {c.get('title', '')} · "
                   f"{c.get('score', 0)} · {((c.get('reasons') or [''])[0])}")
        angle = str(c.get("ai_angle") or "")
        if angle:
            click.echo(f"     ↳ AI 角度: {angle}")
    if excluded:
        click.echo(f"已写过排除 {len(excluded)} 项（相似度 ≥0.6，--refresh 前后可见变化）")
    down = inputs.get("down_sources") or []
    if down:
        click.echo(f"⚠️ 外部腿缺失: {', '.join(str(x) for x in down)}（KB/星标腿照常）")


@click.command("topic-radar")
@click.option("--refresh", is_flag=True, help="强制重算并覆盖 latest（新快照 append 入史，≤20 份超限删最旧）")
@click.option("--no-ai", "no_ai", is_flag=True, help="跳过 AI 增强（top5 候选的 ↳ AI 角度行）")
def topic_radar(refresh: bool, no_ai: bool) -> None:
    """选题雷达：radar/KB/星标三源聚合的「本期值得写什么」快照（Phase D F78）。

    无快照时自动重算；--refresh 强制重算入史；--no-ai 跳过 AI 增强。
    """
    from ..jobs.runner import JobRunner
    from ..services import topic_radar_service

    if not refresh:
        snapshot = topic_radar_service.latest()
        if snapshot is not None:
            _print_snapshot(snapshot)
            return
    runner = JobRunner(max_workers=1)
    try:
        job_id = topic_radar_service.submit_to_runner(runner, use_ai=not no_ai)
        click.echo(f"job_id: {job_id}")
        deadline = time.monotonic() + _POLL_TIMEOUT
        while True:
            job = runner.get(job_id) or {}
            status = str(job.get("status") or "")
            if status in ("done", "failed"):
                break
            if time.monotonic() > deadline:
                click.echo(f"⏱ 轮询超时（{_POLL_TIMEOUT}s）——job 仍在后台，稍后重跑本命令查看", err=True)
                raise SystemExit(1)
            time.sleep(_POLL_SECONDS)
        if status == "failed":
            _fail(str(job.get("error") or "快照生成失败"))
        snapshot = topic_radar_service.latest()
        if snapshot is None:  # 防御：job done 但快照缺失（不应发生）
            _fail("job 已完成但快照缺失（检查 ~/.musespiral/topic_radar/ 权限）")
        _print_snapshot(snapshot)
    finally:
        runner.shutdown(wait=True)


__all__ = ["topic_radar"]
