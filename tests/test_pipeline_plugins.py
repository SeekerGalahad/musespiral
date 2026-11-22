"""插件流水线执行引擎测试（插件系统设计 §五，PL-2——本批测试硬要求 19-24）。

覆盖：
  a) Workspace 单元：put/get 内联往返 / 序列化阈值精确边界（≤1MiB 内联、
     >1MiB 引用）/ >1MiB bytes 走引用且 get 不读全量进内存（read_bytes
     哨兵证伪）/ >1MiB JSON 惰性引用 / path 与 mem 引用 / 端口名净化 /
     产物收集；
  b) 三节点假插件链路（fake-transcribe → fake-keysegment → fake-sink）
     全绿：workspace/output 正确落盘、meta/tmp 收尾；
  c) 失败语义：中间节点失败（failed_at/前序 ok/后序未执行/workspace 保留）、
     缺输出键判失败、额外键忽略、契约不兼容阶段一即拒（零 run 目录）；
  d) 取消：ctx.cancelled 置位 → 插件抛 PluginCancelled → 判 cancelled
     而非 failed（直跑 + JobRunner 协作取消两路）；
  e) job 集成：kind=plugin_pipeline 金路径 done / 失败可读 error 携带
     步级 result / 阶段一红零 job / KINDS 19 计数锚。

全部用例在 MUSESPIRAL_HOME 沙箱跑（test_pipeline_service 同款 autouse 纪律，
不触真实 ~/.musespiral）；假插件写进 tmp 插件区，python runtime 进程内加载，
零外部网络。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from musespiral.core.plugins.context import PluginCancelled, PluginContext, PluginError
from musespiral.jobs.store import KINDS, JobStore
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services.pipeline_service_plugins import (
    INLINE_MAX_BYTES,
    ArtifactRef,
    OutputContractError,
    PipelinePlanError,
    Workspace,
)

# ── 沙箱与夹具 ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


@pytest.fixture
def runs_root(tmp_path: Path) -> Path:
    return tmp_path / "pipeline_runs"


def _write_plugin(
    root: Path, dirname: str, manifest: dict, code: str, *, entry: str = "plugin.py"
) -> Path:
    d = root / dirname
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (d / entry).write_text(code, encoding="utf-8")
    return d


# 假插件族：三节点链 fake-transcribe(source) → fake-keysegment(process)
# → fake-sink(sink)。transcribe 以 variant 参数换行为（ok/missing/extra/
# cancel），keysegment 换 fail，覆盖失败/缺键/额外键/取消各路径。

_TRANSCRIBE_CODE = """
import time

VARIANT = {variant!r}

def run(inputs, config, ctx):
    ctx.log("transcribe start")
    ctx.progress(1, 2)
    if VARIANT == "missing":
        return {{"duration_s": 2.5}}  # 缺 segments（契约红路径）
    if VARIANT == "extra":
        return {{
            "segments": [{{"start": 0.0, "end": 2.5, "text": "hello"}}],
            "duration_s": 2.5,
            "surprise": "额外键应被忽略",  # req 13
        }}
    if VARIANT == "cancel":
        deadline = time.time() + 5
        while not ctx.cancelled and time.time() < deadline:
            time.sleep(0.01)
        if ctx.cancelled:
            from musespiral.core.plugins.context import PluginCancelled
            raise PluginCancelled("自查点收到取消信号")
        return {{"segments": [], "duration_s": 0.0}}
    return {{"segments": [{{"start": 0.0, "end": 2.5, "text": "hello"}}], "duration_s": 2.5}}
"""

_KEYSEGMENT_CODE = """
FAIL = {fail!r}
CANCEL = {cancel!r}

def run(inputs, config, ctx):
    if CANCEL and ctx.cancelled:
        from musespiral.core.plugins.context import PluginCancelled
        raise PluginCancelled("自查点收到取消信号")
    segs = inputs["segments"]
    ctx.log(f"keysegment over {{len(segs)}} segments")
    if FAIL:
        raise RuntimeError("AI 返回格式不合法")
    return {{"ranges": [{{"start": segs[0]["start"], "end": segs[0]["end"]}}], "score": 0.9}}
"""

_SINK_CODE = """
def run(inputs, config, ctx):
    ranges = inputs["ranges"]
    first = ranges[0] if ranges else {"start": 0, "end": 0}
    return {"article": f"# 转写稿\\n\\n[{first['start']:.1f}-{first['end']:.1f}] hello"}
"""


def _install_chain(
    plugins_root: Path,
    *,
    transcribe_variant: str = "ok",
    keysegment_fail: bool = False,
    keysegment_cancel: bool = False,
) -> None:
    _write_plugin(
        plugins_root,
        "fake-transcribe",
        {
            "id": "fake-transcribe",
            "version": "0.1.0",
            "kind": "source",
            "inputs": {"path": {"type": "string", "required": True}},
            "outputs": {"segments": {"type": "segments"}, "duration_s": {"type": "number"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _TRANSCRIBE_CODE.format(variant=transcribe_variant),
    )
    _write_plugin(
        plugins_root,
        "fake-keysegment",
        {
            "id": "fake-keysegment",
            "version": "0.1.0",
            "kind": "process",
            "inputs": {"segments": {"type": "segments", "required": True}},
            "outputs": {"ranges": {"type": "ranges"}, "score": {"type": "number"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _KEYSEGMENT_CODE.format(fail=keysegment_fail, cancel=keysegment_cancel),
    )
    _write_plugin(
        plugins_root,
        "fake-sink",
        {
            "id": "fake-sink",
            "version": "0.1.0",
            "kind": "sink",
            "inputs": {"ranges": {"type": "ranges", "required": True}},
            "outputs": {"article": {"type": "text/markdown"}},
            "runtime": {"type": "python", "entry": "plugin.py:run"},
        },
        _SINK_CODE,
    )


def _chain(media: str = "/tmp/lesson.mp4") -> list[dict]:
    return [
        {"id": "src", "plugin": "fake-transcribe", "inputs": {"path": media}},
        {"id": "seg", "plugin": "fake-keysegment"},
        {"id": "out", "plugin": "fake-sink"},
    ]


# ── a) Workspace 单元 ──────────────────────────────────────────────


def test_workspace_inline_roundtrip(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.put("n1", {"segments": [{"start": 0.0, "end": 1.0, "text": "hi"}], "n": 3})
    assert ws.storage_kind("n1", "segments") == "inline"
    assert ws.get("n1", "segments") == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    # outputs.json 落盘且内联/引用分列（目录布局 <run>/workspace/<node>/）
    persisted = json.loads((tmp_path / "workspace" / "n1" / "outputs.json").read_text("utf-8"))
    assert persisted["inline"]["n"] == 3 and persisted["refs"] == {}


def test_workspace_threshold_boundary(tmp_path: Path) -> None:
    """阈值精确边界：序列化字节数 ≤ INLINE_MAX_BYTES 内联，> 即引用。"""
    ws = Workspace(tmp_path)
    # "aaaa…"(N) 序列化 = N+2 字节（两侧引号）：N=限-3 → 限-1 内联；
    # N=限-1 → 限+1 引用
    fit = "a" * (INLINE_MAX_BYTES - 3)
    over = "a" * (INLINE_MAX_BYTES - 1)
    ws.put("fit", {"payload": fit})
    ws.put("over", {"payload": over})
    assert ws.storage_kind("fit", "payload") == "inline"
    assert ws.storage_kind("over", "payload") == "json"  # 大 JSON 落盘存引用
    assert ws.get("fit", "payload") == fit
    assert ws.get("over", "payload") == over  # 消费点惰性加载，值完整


def test_workspace_big_bytes_ref_never_reads_into_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """>1MiB 二进制产物走引用路径：get 返回 Path，不读全量进内存。
    样本 2MiB（D-2 债清偿批升级：原 INLINE_MAX_BYTES+1 已过引用线但按台账
    要求抬到 ≥2MB，远离阈值边界防将来阈值上探时退化成内联样本）。"""
    ws = Workspace(tmp_path)
    big = b"\x00" * (2 * 1024 * 1024)
    paths = ws.put("n", {"frame": big})
    assert ws.storage_kind("n", "frame") == "bytes"
    assert (tmp_path / "workspace" / "n" / "frame.bin").stat().st_size == len(big)
    assert any(p.endswith("frame.bin") for p in paths)

    def _boom(self):  # noqa: ANN001 - 哨兵：任何读内容的尝试即断言失败
        raise AssertionError("引用路径不得读取产物内容到内存")

    monkeypatch.setattr(Path, "read_bytes", _boom)
    monkeypatch.setattr(Path, "read_text", _boom)
    got = ws.get("n", "frame")  # 只取路径——read_bytes 哨兵证明零内容读取
    assert isinstance(got, Path) and got.name == "frame.bin"


def test_workspace_collect_output_streams_big_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """阶段三收集大产物走流式复制（2026-11-20 复核修复的回归锁）。

    复核发现的缺口：collect_output 曾对 bytes/json 引用做
    ``target.write_bytes(src.read_bytes())``——成功态收集时整段进内存，
    多条产物叠加峰值=各文件之和（模块自述媒体产物可能 GB 级）。修法=
    shutil.copyfile 流式复制；本哨兵与 get 路径的同类哨兵（上一测试）
    合起来锁死「消费点与收集点都不整段读」。
    """
    import filecmp

    ws = Workspace(tmp_path)
    big = b"\x02" * (2 * 1024 * 1024)
    # bytes 引用（cover.bin）+ json 引用（blob.json，序列化 >1MiB）
    ws.put("sink", {"cover": big, "blob": "x" * (2 * 1024 * 1024)})
    src_bin = tmp_path / "workspace" / "sink" / "cover.bin"
    src_json = tmp_path / "workspace" / "sink" / "blob.json"

    def _boom(self):  # noqa: ANN001 - 哨兵：收集期任何 read_bytes 即断言失败
        raise AssertionError(f"collect_output 不得把产物整段读进内存: {self}")

    monkeypatch.setattr(Path, "read_bytes", _boom)
    out = tmp_path / "output"
    collected = ws.collect_output("sink", out)
    assert (out / "cover.bin").stat().st_size == len(big)
    assert (out / "blob.json").stat().st_size == src_json.stat().st_size
    # filecmp 走缓冲块对比（不经 Path.read_bytes）——哨兵期内完成字节级等价断言
    assert filecmp.cmp(src_bin, out / "cover.bin", shallow=False)
    assert filecmp.cmp(src_json, out / "blob.json", shallow=False)
    assert any(p.endswith("cover.bin") for p in collected)
    assert any(p.endswith("blob.json") for p in collected)


def test_workspace_path_and_mem_refs(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    sentinel = object()  # 不可 JSON 序列化
    ws.put("n", {"media": Path("/data/lesson.mp4"), "engine": sentinel})
    assert ws.storage_kind("n", "media") == "path"
    assert ws.storage_kind("n", "engine") == "mem"
    assert ws.get("n", "media") == Path("/data/lesson.mp4")  # 原路径不复制
    assert ws.get("n", "engine") is sentinel  # 进程内对象引用保真（同一性）


def test_workspace_unsafe_port_names_sanitized(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    big = b"x" * (INLINE_MAX_BYTES + 1)
    ws.put("n", {"../evil": big, "a/b": big})
    node_dir = tmp_path / "workspace" / "n"
    files = sorted(p.name for p in node_dir.iterdir() if p.suffix == ".bin")
    assert files == [".._evil.bin", "a_b.bin"]  # 净化后仍在节点目录内，无穿越
    assert ws.get("n", "../evil") == node_dir / ".._evil.bin"


def test_workspace_collect_output(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    ws.put(
        "sink",
        {
            "article": "# 稿",  # 内联 → output/article.json
            "cover": b"\x89PNG-fake",  # 小 bytes 依旧独立文件（二进制不内联）
            "media": Path("/data/lesson.mp4"),  # path 引用不复制
        },
    )
    out = tmp_path / "output"
    collected = ws.collect_output("sink", out)
    names = {Path(c).name for c in collected if not c.startswith("/")}
    assert {"article.json", "cover.bin"} <= names
    assert json.loads((out / "article.json").read_text("utf-8")) == "# 稿"
    assert (out / "cover.bin").read_bytes() == b"\x89PNG-fake"
    assert "/data/lesson.mp4" in collected  # path 引用原样记录、不复制
    assert not (out / "lesson.mp4").exists()


def test_workspace_get_missing_node_readable(tmp_path: Path) -> None:
    ws = Workspace(tmp_path)
    with pytest.raises(KeyError, match="尚未产出"):
        ws.get("ghost", "any")


# ── a') PluginContext 实现细节（ctx.ai 两道门）────────────────────


def test_context_ai_gated_by_uses_ai(tmp_path: Path) -> None:
    from musespiral.services.pipeline_service_plugins import _PipelineContext

    def _make(uses_ai: bool, client: object) -> _PipelineContext:
        return _PipelineContext(
            node_id="n", run_dir=tmp_path, workspace=Workspace(tmp_path),
            cancelled=None, ai_client=client, uses_ai=uses_ai,
        )

    with pytest.raises(PluginError, match="uses_ai"):
        _make(False, object()).ai  # 未声明 → 可读引导
    with pytest.raises(PluginError, match="未注入"):
        _make(True, None).ai  # 声明了但平台没配 → 可读引导
    client = object()
    assert _make(True, client).ai is client
    # 接口形状锚：六面齐全（log/progress/tmpdir/cancelled/artifacts_dir/ai）
    ctx = _make(True, client)
    assert isinstance(ctx, PluginContext)
    ctx.log("hi")
    ctx.progress(1, 2)
    assert ctx.tmpdir().is_dir() and ctx.cancelled is False
    assert ctx.artifacts_dir == tmp_path / "workspace" / "n"


# ── b) 三节点假插件链路（req 19）──────────────────────────────────


def test_three_node_fake_chain_green(
    plugins_root: Path, runs_root: Path
) -> None:
    _install_chain(plugins_root)
    result = psp.run_pipeline(
        _chain(), plugins_root=plugins_root, run_root=runs_root, run_id="pl-test-0001"
    )
    assert result["status"] == "done"
    assert [s["status"] for s in result["steps"]] == ["ok", "ok", "ok"]
    assert [s["step"] for s in result["steps"]] == ["src", "seg", "out"]
    assert result["steps_done"] == 3

    run_dir = runs_root / "pl-test-0001"
    # workspace 逐节点落盘（<run>/workspace/<node_id>/outputs.json）
    for node in ("src", "seg", "out"):
        persisted = json.loads(
            (run_dir / "workspace" / node / "outputs.json").read_text("utf-8")
        )
        assert persisted["inline"] or persisted["refs"]
    # 节点间传值正确：sink 收到 keysegment 的 ranges（经 workspace 中转）
    sink_out = json.loads((run_dir / "workspace" / "out" / "outputs.json").read_text("utf-8"))
    assert "[0.0-2.5] hello" in sink_out["inline"]["article"]
    # 阶段三：末节点产物收集到 output/
    article = json.loads((run_dir / "output" / "article.json").read_text("utf-8"))
    assert article.startswith("# 转写稿")
    assert "output/article.json" in result["artifacts"]
    # meta.json 终态 + 阶段四：tmp 受管区已清
    meta = json.loads((run_dir / "meta.json").read_text("utf-8"))
    assert meta["status"] == "done" and meta["nodes"] == ["src", "seg", "out"]
    assert not (run_dir / "tmp").exists()
    # 运行日志留痕（ctx.log → log.jsonl）
    logs = (run_dir / "log.jsonl").read_text("utf-8").strip().splitlines()
    assert any('"node": "src"' in line and "transcribe start" in line for line in logs)


# ── c) 失败语义（req 20/21/13/22）─────────────────────────────────


def test_middle_node_failure_stops_and_preserves_workspace(
    plugins_root: Path, runs_root: Path
) -> None:
    _install_chain(plugins_root, keysegment_fail=True)
    result = psp.run_pipeline(
        _chain(), plugins_root=plugins_root, run_root=runs_root, run_id="pl-fail-0001"
    )
    assert result["status"] == "failed"
    assert result["failed_at"] == "seg"
    assert [s["step"] for s in result["steps"]] == ["src", "seg"]  # 后序未执行
    assert result["steps"][0]["status"] == "ok"
    failed = result["steps"][1]
    assert failed["status"] == "failed" and "AI 返回格式不合法" in failed["detail"]
    # workspace 保留（现场可查）：src 已产出、seg 目录无产物
    run_dir = runs_root / "pl-fail-0001"
    assert (run_dir / "workspace" / "src" / "outputs.json").is_file()
    assert not (run_dir / "workspace" / "out").exists()
    # 失败不收集 output
    assert not (run_dir / "output").exists()
    meta = json.loads((run_dir / "meta.json").read_text("utf-8"))
    assert meta["status"] == "failed"


def test_missing_output_key_fails_with_readable_reason(
    plugins_root: Path, runs_root: Path
) -> None:
    _install_chain(plugins_root, transcribe_variant="missing")
    result = psp.run_pipeline(
        _chain(), plugins_root=plugins_root, run_root=runs_root, run_id="pl-miss-0001"
    )
    assert result["status"] == "failed" and result["failed_at"] == "src"
    detail = result["steps"][0]["detail"]
    assert "缺输出键" in detail and "segments" in detail  # OutputContractError 可读


def test_extra_output_keys_ignored(
    plugins_root: Path, runs_root: Path
) -> None:
    _install_chain(plugins_root, transcribe_variant="extra")
    result = psp.run_pipeline(
        _chain(), plugins_root=plugins_root, run_root=runs_root, run_id="pl-extra-0001"
    )
    assert result["status"] == "done"  # 额外键忽略不报错（req 13）


def test_plan_rejects_before_run_dir_created(plugins_root: Path, runs_root: Path) -> None:
    """契约不兼容 → 阶段一即拒，不创建 run 目录（req 22）。

    注：process 打头（必需输入当运行级参数直供）是 core 契约的合法形态
    （validate_pipeline_contract 对 i==0 不查供给）——真正阶段一红的
    「接不上」是后置节点的必需端口在全流水线无兼容供给。
    """
    _install_chain(plugins_root)
    # 红 1：sink 的必需输入 ranges 无任何节点供给（相邻契约接不上）
    bad = [
        {"id": "src", "plugin": "fake-transcribe", "inputs": {"path": "x.mp4"}},
        {"id": "out", "plugin": "fake-sink"},  # ranges 无人供给
    ]
    with pytest.raises(PipelinePlanError, match="未被任何更早节点的输出供给"):
        psp.run_pipeline(bad, plugins_root=plugins_root, run_root=runs_root)
    # 红 2：sink 不在结尾（kind 位置规则）
    with pytest.raises(PipelinePlanError, match="只能位于流水线结尾"):
        psp.run_pipeline(
            [{"id": "out", "plugin": "fake-sink"},
             {"id": "seg", "plugin": "fake-keysegment"}],
            plugins_root=plugins_root, run_root=runs_root,
        )
    # 红 3：插件不可用（未安装）
    with pytest.raises(PipelinePlanError, match="不可用"):
        psp.run_pipeline([{"id": "x", "plugin": "no-such-plugin"}],
                         plugins_root=plugins_root, run_root=runs_root)
    # 红 4：空流水线
    with pytest.raises(PipelinePlanError, match="至少需要一个节点"):
        psp.run_pipeline([], plugins_root=plugins_root, run_root=runs_root)
    assert not runs_root.exists() or not any(runs_root.iterdir())  # 零 run 目录


def test_plan_rejects_duplicate_node_ids(plugins_root: Path) -> None:
    _install_chain(plugins_root)
    dup = [
        {"id": "src", "plugin": "fake-transcribe", "inputs": {"path": "x"}},
        {"id": "src", "plugin": "fake-transcribe", "inputs": {"path": "y"}},
    ]
    with pytest.raises(PipelinePlanError, match="id 重复"):
        psp.plan_pipeline(dup, plugins_root=plugins_root)


# ── d) 取消（req 23）───────────────────────────────────────────────


def test_cancelled_not_failed_direct(plugins_root: Path, runs_root: Path) -> None:
    """中途取消（src 已产出、seg 自查点抛 PluginCancelled）→ 判 cancelled
    而非 failed；已完成步保留、workspace 保留（取消同失败的现场纪律）。"""
    _install_chain(plugins_root, keysegment_cancel=True)
    result = psp.run_pipeline(
        _chain(), plugins_root=plugins_root, run_root=runs_root, run_id="pl-cxl-0001",
        cancelled=lambda: True,  # 进入 seg 即置位
    )
    assert result["status"] == "cancelled"  # 判 cancelled 而非 failed
    assert result.get("cancelled_at") == "seg"
    assert "failed_at" not in result
    assert result["steps"][0]["status"] == "ok"  # 前序步保留
    assert result["steps"][1]["status"] == "cancelled"
    assert [s["step"] for s in result["steps"]] == ["src", "seg"]  # 后序未执行
    # 取消保留现场：src 已产出的 workspace 原样在
    assert (runs_root / "pl-cxl-0001" / "workspace" / "src" / "outputs.json").is_file()
    meta = json.loads((runs_root / "pl-cxl-0001" / "meta.json").read_text("utf-8"))
    assert meta["status"] == "cancelled"


# ── e) job 集成（req 14/15/17；KINDS 19 计数锚）───────────────────


def _poll_terminal(store: JobStore, job_id: str, timeout_s: float = 15.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = store.get(job_id)
        if job is not None and job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job 未在 {timeout_s}s 内终态: {store.get(job_id)}")


def test_jobs_kinds_include_plugin_pipeline() -> None:
    """具名锚：plugin_pipeline 入白名单（18→19），计数随扩键同步（19→20：P1 批 2026-11-23 +quality_loop，具名锚在 tests/test_quality_loop.py）。"""
    assert "plugin_pipeline" in KINDS and len(KINDS) == 20


def test_job_golden_path_done(plugins_root: Path, runs_root: Path) -> None:
    from musespiral.jobs.runner import JobRunner

    _install_chain(plugins_root)
    runner = JobRunner()
    try:
        job_id = psp.submit_to_runner(
            runner, _chain(), plugins_root=plugins_root, run_root=runs_root
        )
        assert runner.get(job_id)["kind"] == "plugin_pipeline"
        job = _poll_terminal(runner.store, job_id)
        assert job["status"] == "done"
        assert job["result"]["status"] == "done"
        assert job["result"]["steps_done"] == 3
    finally:
        runner.shutdown(wait=True)


def test_job_failure_readable_error_with_steps(plugins_root: Path, runs_root: Path) -> None:
    from musespiral.jobs.runner import JobRunner

    _install_chain(plugins_root, keysegment_fail=True)
    runner = JobRunner()
    try:
        job_id = psp.submit_to_runner(
            runner, _chain(), plugins_root=plugins_root, run_root=runs_root
        )
        job = _poll_terminal(runner.store, job_id)
        assert job["status"] == "failed"
        assert "插件流水线在 seg 节点失败" in job["error"]
        assert "AI 返回格式不合法" in job["error"]
        assert job["result"]["failed_at"] == "seg"  # JobFailure.result 携带步级记录
    finally:
        runner.shutdown(wait=True)


def test_job_cancel_cooperative_done_with_cancelled_result(
    plugins_root: Path, runs_root: Path
) -> None:
    """runner.cancel → ctx.cancelled 置位 → PluginCancelled → job done 且
    result.status=cancelled（batch_adapt 部分成功先例，非 failed）。"""
    from musespiral.jobs.runner import JobRunner

    _install_chain(plugins_root, transcribe_variant="cancel")
    runner = JobRunner()
    try:
        job_id = psp.submit_to_runner(
            runner, _chain(), plugins_root=plugins_root, run_root=runs_root
        )
        assert runner.cancel(job_id)  # queued/running 均受理（协作取消）
        job = _poll_terminal(runner.store, job_id)
        assert job["status"] == "done"  # 取消=收束非失败
        assert job["result"]["status"] == "cancelled"
        assert job["result"]["cancelled_at"] == "src"
    finally:
        runner.shutdown(wait=True)


def test_submit_plan_error_zero_job(plugins_root: Path) -> None:
    from musespiral.jobs.runner import JobRunner

    _install_chain(plugins_root)
    runner = JobRunner()
    try:
        with pytest.raises(PipelinePlanError, match="不可用"):
            psp.submit_to_runner(
                runner,
                [{"id": "x", "plugin": "ghost-plugin"}],
                plugins_root=plugins_root,
            )
        assert JobStore().list(limit=None) == []  # 构造期校验零 job
    finally:
        runner.shutdown(wait=True)


# ── 边角：引擎纪律锚 ───────────────────────────────────────────────


def test_inline_max_bytes_constant_anchor() -> None:
    """阈值唯一权威常量 = 1 MiB（评审 B-5：不散拷贝）。"""
    assert INLINE_MAX_BYTES == 1 << 20


def test_output_contract_error_lineage() -> None:
    """OutputContractError 是引擎判失败用的可读异常（非插件面异常族）。"""
    assert issubclass(OutputContractError, RuntimeError)
    assert issubclass(PluginCancelled, PluginError)  # 异常族同源（core 定义）
