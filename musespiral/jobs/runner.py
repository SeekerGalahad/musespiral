"""R4 任务执行器（Phase C 自 musespiral/core/jobs/ 提升，行为零变更） —— Studio 进程内 ThreadPoolExecutor(max_workers=2)。

  - submit(kind, params, fn)：入队即返回 job_id（store 落 queued 行）；
    fn(job_id) 在 worker 线程执行，返回值入 result，异常转 failed
    （error 经 store.sanitize_error 脱敏截断）。
  - JobFailure：闭包内已按调用方上下文（如 Studio 的 root）脱敏的业务失败，
    runner 原文入库（仅再过一遍秘密值打码兜底）；可选 result 携带部分产物
    （如 batch_adapt 中断前已生成的前缀）一并入库。
  - set_phase：批量子任务的进度标记（如 batch_adapt 的 "2/4"）。
  - cancel/is_cancelled（2026-11-18 集成轮，R3.5 正典 §2.2 取消语义落点）：
    **协作式取消**——cancel(job_id) 置检查点旗标（终态 job 返回 False 不误报）；
    闭包在自查点调 is_cancelled 决定提前收束（已完成的产物保留入库，未完成
    放弃——部分成功是合法终态）。未设检查点的 kind 不受影响，自然终态。
  - startup()：孤儿恢复——上一进程遗留的 running/queued 行标 failed(interrupted)；
    重发=新建同参 job（UI/CLI 侧重发按钮），不复活旧行。
CLI 保持同步（正典 §三-3：任务层只服务长动作 AI/网络/批量）。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .store import JobStore, now_iso, sanitize_error


class JobFailure(Exception):
    """动作闭包抛出的「已脱敏业务失败」：message 原文作为 job.error 入库。

    result（可选）：失败时仍值得留下的部分产物（如 batch_adapt 中断前已
    生成的 outputs 前缀），runner 一并写入 job 行，供任务面板呈现与重发
    跳过已存在产物。
    """

    def __init__(self, message: str, result: Any | None = None) -> None:
        super().__init__(message)
        self.result = result


class JobRunner:
    """任务执行器：ThreadPoolExecutor 驱动 fn(job_id) → done/failed。"""

    def __init__(self, max_workers: int = 2, store: JobStore | None = None) -> None:
        self._store = store if store is not None else JobStore()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="musespiral-job")
        self._cancels: dict[str, threading.Event] = {}
        self._cancels_lock = threading.Lock()

    @property
    def store(self) -> JobStore:
        return self._store

    # ---- 生命周期 ----

    def submit(self, kind: str, params: dict[str, Any], fn: Callable[[str], Any]) -> str:
        """入队即返回 job_id；fn 收 job_id（供 set_phase 上报进度）。"""
        job = self._store.create(kind, params)
        self._pool.submit(self._run, job["id"], fn)
        return job["id"]

    def startup(self) -> int:
        """孤儿恢复：把遗留的 running/queued 行标 failed(interrupted)；返回恢复条数。

        queued 一并恢复——Studio 路径下所有提交都必须先经 _runner()（建池即
        startup()），故 startup() 执行时刻本进程必然零任务，jobs.jsonl 里全部
        queued 行都只能是死进程孤儿：不标 failed 就无终态（clean 只删
        done/failed，UI 角标还把 queued 计为进行中），只能手改文件清掉。
        """
        recovered = 0
        for job in self._store.list(limit=None):
            if job.get("status") == "running":
                self._store.update(
                    job["id"],
                    status="failed",
                    finished=now_iso(),
                    error="interrupted（进程重启，任务中断；可在任务面板重发）",
                )
                recovered += 1
            elif job.get("status") == "queued":
                self._store.update(
                    job["id"],
                    status="failed",
                    finished=now_iso(),
                    error="interrupted（进程重启时仍在排队，未开始执行；可在任务面板重发）",
                )
                recovered += 1
        return recovered

    def shutdown(self, wait: bool = True) -> None:
        """排空并关闭线程池（测试收尾用；进程退出时 atexit 也会 join）。"""
        self._pool.shutdown(wait=wait)

    # ---- 进度与查询 ----

    def set_phase(self, job_id: str, phase: str) -> None:
        self._store.update(job_id, phase=phase)

    # ---- 协作式取消（2026-11-18 集成轮）----

    def cancel(self, job_id: str) -> bool:
        """请求取消：置检查点旗标并在 running 行上标 phase；返回是否受理。

        终态（done/failed，行缺失）→ False——不误报「已取消」。受理后旗标
        恒在直到 worker 收尾（_run finally 清理）；闭包是否、何时响应取决于
        其检查点密度（未设检查点的 kind 自然跑完，旗标无副作用）。
        """
        job = self._store.get(job_id)
        if job is None or job.get("status") in ("done", "failed"):
            return False
        with self._cancels_lock:
            self._cancels.setdefault(job_id, threading.Event()).set()
        if job.get("status") == "running":
            self._store.update(job_id, phase="取消中（于检查点生效）")
        return True

    def is_cancelled(self, job_id: str) -> bool:
        """闭包检查点读：旗标是否已置（未知 job → False）。"""
        with self._cancels_lock:
            event = self._cancels.get(job_id)
        return bool(event and event.is_set())

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._store.get(job_id)

    def list(self, limit: int | None = 50) -> list[dict[str, Any]]:
        return self._store.list(limit=limit)

    # ---- worker ----

    def _run(self, job_id: str, fn: Callable[[str], Any]) -> None:
        """worker：queued→running→done/failed；落盘异常也尽力留下终态。

        running 置位若抛 IO 异常，job 会永久停留 queued 且无错误记录——
        故任何一步的 update 失败都兜底再试一次 failed 落盘（_settle）。
        """
        try:
            self._store.update(job_id, status="running", started=now_iso())
            result = fn(job_id)
        except JobFailure as exc:
            fields: dict[str, Any] = {"error": sanitize_error(str(exc))}
            if exc.result is not None:
                fields["result"] = exc.result  # 部分产物（如 batch 已生成前缀）
            self._settle(job_id, "failed", **fields)
        except Exception as exc:  # 业务/系统异常一律落 job.error，不炸 worker
            text = str(exc).strip() or exc.__class__.__name__
            self._settle(job_id, "failed", error=sanitize_error(text))
        else:
            self._settle(job_id, "done", result=result)
        finally:
            with self._cancels_lock:
                self._cancels.pop(job_id, None)  # 旗标随终态清理（防字典缓涨）

    def _settle(self, job_id: str, status: str, **fields: Any) -> None:
        """终态落盘；jobs.jsonl 已不可写时放弃（终态无处落，不炸 worker）。"""
        try:
            self._store.update(job_id, status=status, finished=now_iso(), **fields)
        except Exception:
            pass
