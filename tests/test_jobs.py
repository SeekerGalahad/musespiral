"""R4 任务层单测：job 生命周期 / 孤儿恢复 / 损坏行容忍 / 字节级保留 / 并发提交。

全部走 tmp_path + MUSESPIRAL_HOME 隔离（jobs.jsonl 落在临时 home），不起服务器、
无网络；fn 均为本地快速闭包。CLI jobs 命令用 CliRunner 冒烟（命令未注册进
cli.py——注册归集成阶段，这里直接调命令组对象）。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from musespiral.commands.jobs_cmds import jobs as jobs_cmd
from musespiral.jobs.runner import JobFailure, JobRunner
from musespiral.jobs.store import JobStore, jobs_path


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def runner() -> JobRunner:
    r = JobRunner()
    yield r
    r.shutdown(wait=True)


def _wait_terminal(runner: JobRunner, job_id: str, timeout: float = 5.0) -> dict:
    """轮询 runner.get 至 done/failed（fn 均为毫秒级本地闭包）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get(job_id)
        if job is not None and job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.01)
    pytest.fail(f"job {job_id} 未在 {timeout}s 内到达终态")


# ---- store：行结构与读取 ----


def test_store_create_appends_queued_row() -> None:
    store = JobStore()
    job = store.create("adapt", {"article": "a.md", "platform": "xhs"})
    assert job["status"] == "queued"
    assert job["kind"] == "adapt"
    assert len(job["id"]) == 8  # 短 uuid
    assert job["created"]
    assert jobs_path().is_file()
    assert store.get(job["id"])["params"] == {"article": "a.md", "platform": "xhs"}


def test_store_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError, match="未知任务类型"):
        JobStore().create("nope", {})


def test_store_list_newest_first_with_limit() -> None:
    store = JobStore()
    ids = [store.create("adapt", {"n": i})["id"] for i in range(3)]
    rows = store.list(limit=2)
    assert [r["id"] for r in rows] == ids[::-1][:2]  # 倒序（新在前）
    assert [r["id"] for r in store.list()] == ids[::-1]  # limit=None 全量


def test_store_update_merges_fields_and_missing_id_noop() -> None:
    store = JobStore()
    job = store.create("pack", {"x": 1})
    assert store.update(job["id"], status="running", started="t0") is not None
    row = store.get(job["id"])
    assert row["status"] == "running"
    assert row["started"] == "t0"
    assert row["kind"] == "pack" and row["params"] == {"x": 1}  # 未触及字段不动
    assert store.update("deadbee0", status="done") is None  # 未知 id 不写不炸


# ---- store：字节级保留 + 损坏行容忍 ----


def test_store_update_preserves_other_rows_byte_level() -> None:
    """行级更新只改目标行：非 UTF-8 行 / CRLF 行尾 / 兄弟行全部字节原样。"""
    store = JobStore()
    first = store.create("adapt", {"a": 1})
    second = store.create("adapt", {"b": 2})
    path = jobs_path()
    original_lines = path.read_bytes().splitlines(keepends=True)  # [first 行, second 行]

    corrupted = b"\xff\xfe not-utf8 \x80\n"  # 非 UTF-8 字节行（不可解码）
    crlf_row = (
        json.dumps({"id": "crlfrow1", "kind": "adapt", "status": "done"}).encode("utf-8")
        + b"\r\n"
    )
    broken_json = b"{not json at all\n"
    path.write_bytes(corrupted + crlf_row + broken_json + b"".join(original_lines))

    assert store.update(second["id"], status="done", result={"dir": "x.pack"}) is not None
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[0] == corrupted  # 非 UTF-8 行整行字节原样
    assert lines[1] == crlf_row  # CRLF 行尾原样（未被改写成 LF）
    assert lines[2] == broken_json  # 损坏 JSON 行原样
    assert lines[3] == original_lines[0]  # 兄弟行（first）字节不变
    updated = json.loads(lines[4].decode("utf-8"))
    assert updated["id"] == second["id"] and updated["status"] == "done"
    assert updated["result"] == {"dir": "x.pack"}
    assert lines[4].endswith(b"\n")  # 目标行行尾风格保留

    # 损坏行不进列表、不阻断读取
    assert {r["id"] for r in store.list()} == {"crlfrow1", first["id"], second["id"]}
    assert store.get(first["id"]) is not None


def test_store_clean_removes_terminal_rows_only() -> None:
    """clean 只删 done/failed；queued/running/损坏行字节级保留。"""
    store = JobStore()
    keep_queued = store.create("adapt", {})
    keep_running = store.create("adapt", {})
    store.update(keep_running["id"], status="running")
    done = store.create("adapt", {})
    store.update(done["id"], status="done")
    failed = store.create("adapt", {})
    store.update(failed["id"], status="failed", error="boom")
    corrupted = b"\xff\xfe broken\n"
    path = jobs_path()
    path.write_bytes(corrupted + path.read_bytes())

    assert store.clean() == 2
    lines = path.read_bytes().splitlines(keepends=True)
    assert lines[0] == corrupted
    assert {json.loads(l)["id"] for l in lines[1:]} == {keep_queued["id"], keep_running["id"]}
    assert JobStore().clean() == 0  # 幂等：无可删行不重写文件


# ---- runner：生命周期 / 失败 / 进度 / 孤儿恢复 / 并发 ----


def test_runner_lifecycle_queued_to_done(runner: JobRunner) -> None:
    job_id = runner.submit("adapt", {"x": 1}, lambda jid: {"out": "f.md", "seen": jid})
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "done"
    assert job["result"] == {"out": "f.md", "seen": job_id}  # fn 收到 job_id
    assert job["started"] and job["finished"] and job["kind"] == "adapt"
    assert job["params"] == {"x": 1}


def test_runner_lifecycle_failed_with_sanitized_error(runner: JobRunner) -> None:
    def boom(job_id: str) -> None:
        raise RuntimeError("boom access_token=SECRET-987 /Users/x/secret.md")

    job_id = runner.submit("adapt", {}, boom)
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "failed"
    assert "SECRET-987" not in job["error"]  # 秘密值打码
    assert "access_token=***" in job["error"]
    assert len(job["error"]) <= 201  # 截断上限（200 + 省略号）


def test_runner_job_failure_pre_sanitized_passthrough(runner: JobRunner) -> None:
    """JobFailure = 闭包内已脱敏的业务失败，错误原文入库。"""

    def fail(job_id: str) -> None:
        raise JobFailure("目标文件已存在，拒绝覆盖: sub/a.xhs.md")

    job_id = runner.submit("adapt", {}, fail)
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "目标文件已存在，拒绝覆盖: sub/a.xhs.md"


def test_runner_phase_progress_via_set_phase(runner: JobRunner) -> None:
    def fn(job_id: str) -> dict:
        runner.set_phase(job_id, "1/2")
        runner.set_phase(job_id, "2/2")
        return {}

    job_id = runner.submit("batch_adapt", {"platforms": ["xhs", "zhihu"]}, fn)
    job = _wait_terminal(runner, job_id)
    assert job["phase"] == "2/2"


def test_runner_orphan_recovery_marks_running_failed(tmp_path: Path) -> None:
    """startup() 把遗留 running/queued 行都标 failed(interrupted)——queued 孤儿也无终态路径。

    Studio 路径下所有提交先经 _runner()（建池即 startup()），startup() 时点
    本进程零任务，遗留 queued 行只能是死进程孤儿：不标 failed 则 clean 清不掉
    （只删 done/failed）、UI 角标永久计入进行中。
    """
    store = JobStore()
    orphan = store.create("adapt", {})
    store.update(orphan["id"], status="running", started="t0")
    queued = store.create("adapt", {})

    runner = JobRunner()
    try:
        assert runner.startup() == 2
        for row_id in (orphan["id"], queued["id"]):
            row = runner.get(row_id)
            assert row is not None and row["status"] == "failed"
            assert "interrupted" in row["error"]
        assert "排队" in runner.get(queued["id"])["error"]  # queued 文案区分未执行
        assert runner.startup() == 0  # 幂等
    finally:
        runner.shutdown()


def test_runner_job_failure_carries_partial_result(runner: JobRunner) -> None:
    """JobFailure(result=...)：失败行同时落部分产物（batch_adapt 中断前缀）。"""

    def fail(job_id: str) -> None:
        raise JobFailure("口播稿没有可靠的启发式降级", result={"outputs": ["a.xhs.md"]})

    job_id = runner.submit("batch_adapt", {"platforms": ["xhs", "channels"]}, fail)
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "failed"
    assert job["error"] == "口播稿没有可靠的启发式降级"
    assert job["result"] == {"outputs": ["a.xhs.md"]}


def test_runner_running_update_io_error_settles_failed(runner: JobRunner, monkeypatch) -> None:
    """running 置位抛 IO 异常时兜底标 failed，不留无终态的 queued 行。"""
    calls: list[str] = []
    real_update = runner.store.update

    def flaky_update(job_id: str, **fields):
        # 第一次 update（running 置位）抛 OSError，其后恢复正常（兜底可落盘）
        if not calls:
            calls.append(job_id)
            raise OSError("disk full")
        return real_update(job_id, **fields)

    monkeypatch.setattr(runner.store, "update", flaky_update)
    job_id = runner.submit("adapt", {}, lambda jid: {"ok": True})
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "failed"  # 不再永久停留 queued
    assert "disk full" in job["error"]


def test_runner_concurrent_submissions_do_not_clobber() -> None:
    """两线程同时 submit：两条记录各自完整终态，互不覆盖（并发安全门）。"""
    runner = JobRunner(max_workers=2)
    try:
        barrier = threading.Barrier(2, timeout=5)  # 两任务同时在场均释放

        def slow(job_id: str) -> dict:
            barrier.wait()
            return {"id": job_id}

        ids: list[str] = []

        def submit(n: int) -> None:
            ids.append(runner.submit("adapt", {"n": n}, slow))

        threads = [threading.Thread(target=submit, args=(n,)) for n in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(ids) == 2 and ids[0] != ids[1]
        for job_id in ids:
            job = _wait_terminal(runner, job_id)
            assert job["status"] == "done"
            assert job["result"] == {"id": job_id}  # 各自的 job_id，无串行覆盖
        rows = runner.list()
        assert len(rows) == 2 and len({r["id"] for r in rows}) == 2
        for row in rows:  # 字段完整
            assert row["kind"] == "adapt" and row["created"] and row["finished"]
    finally:
        runner.shutdown()


# ---- CLI jobs 命令（未注册进 cli.py；直接调命令组冒烟）----


def test_jobs_cli_list_and_clean() -> None:
    store = JobStore()
    done = store.create("adapt", {"article": "a.md", "platform": "xhs"})
    store.update(done["id"], status="done", result={"output": "a.xhs.md"})
    store.create("pack", {})

    cli = CliRunner()
    res = cli.invoke(jobs_cmd, ["list"])
    assert res.exit_code == 0
    assert done["id"] in res.output
    assert "done" in res.output and "queued" in res.output
    assert "a.xhs.md" in res.output  # result 摘要展示

    res = cli.invoke(jobs_cmd, ["clean"])
    assert res.exit_code == 0
    rows = JobStore().list()
    assert [r["status"] for r in rows] == ["queued"]  # 只剩非终态行


# ---- 协作式取消（2026-11-18 集成轮，R3.5 正典 §2.2 取消语义落点）----


def test_runner_cancel_wakes_checkpoint_and_settles_done(runner: JobRunner) -> None:
    """running job 在检查点观察到旗标→提前收束；终态后旗标清理+phase 标记。"""
    seen: dict[str, object] = {}

    def fn(job_id: str) -> str:
        for _ in range(500):
            if runner.is_cancelled(job_id):
                seen["cancelled"] = True
                return "stopped-at-checkpoint"
            time.sleep(0.01)
        seen["cancelled"] = False
        return "finished-naturally"

    job_id = runner.submit("adapt", {}, fn)
    deadline = time.time() + 5
    while time.time() < deadline and runner.get(job_id)["status"] == "queued":
        time.sleep(0.01)
    assert runner.cancel(job_id) is True  # 受理
    assert runner.is_cancelled(job_id) is True
    job = _wait_terminal(runner, job_id)
    assert job["status"] == "done" and job["result"] == "stopped-at-checkpoint"
    assert seen["cancelled"] is True
    assert job.get("phase") == "取消中（于检查点生效）"  # running 行的 phase 标记
    assert runner.is_cancelled(job_id) is False  # 终态后旗标清理（_run finally）


def test_runner_cancel_terminal_and_unknown_rejected(runner: JobRunner) -> None:
    """终态/未知 job → cancel 返回 False（不误报已取消）。"""
    job_id = runner.submit("adapt", {}, lambda _jid: "ok")
    _wait_terminal(runner, job_id)
    assert runner.cancel(job_id) is False
    assert runner.cancel("00000000") is False
    assert runner.is_cancelled("00000000") is False
