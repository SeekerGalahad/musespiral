"""PL-3 插件系统 HTTP 接驳端点测试（API 契约 §3.19，复核修复批 2026-11-20）。

后端此前零 /api/plugins*、/api/pipelines* 路由（复核 high-1/high-2：前端全按
占位降级、2668 全绿掩盖服务缺席）。本批接驳消费面全集，红绿要点：
  a) GET 读面：插件清单（含 skipped 坏目录不炸整面）/ 依赖检查 / 流水线清单
     与详情（节点 manifest 再水化）；
  b) POST 保存：UI 形状 {id, plugin: manifest} 归一引擎形状 {id, plugin: <id>}
     落盘；**后端真拒非法顺序（400 契约文案）**——此前只有前端拦；非法 id /
     未知插件 400 不落盘；
  c) POST 运行：plan_pipeline 校验先行（零 job 纪律）→ job plugin_pipeline →
     runs 读面（meta.json 终态 steps/artifacts + plugins 输出类型表——前端
     hasSegs 门控的数据源）；
  d) 闸门与边界：confirm 闸门 / 404 面 / run_id 与 pid 白名单回验防穿越；
  e) E-4 转写编辑接驳（PL-6 批 2026-11-20，台账 §四「PL-6 = E-3+E-4」）：
     GET segments（编辑版优先）/ POST /api/pipelines/runs/segments（精确
     路径 + body run_id，落 edited 于产出节点目录）/ GET audio 裁段 wav；
  f) 三态裁决端点（AI-1 批 2026-11-21，契约 §3.19 修订 3）：POST
     /api/pipelines/runs/<run_id>/adopt|discard（动态段 POST，run_id 在
     URL 段内）——ks ranges 全集/子集裁决与幂等、编辑版优先落点、aw
     article 与 article_status 同步、非法参数/confirm/404 可读拒绝面。
"""
from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from musespiral import studio
from musespiral.infra import plugins_loader

SRC_MANIFEST = {
    "id": "demo-source", "version": "1.0.0", "kind": "source",
    "inputs": {"path": {"type": "string", "required": False, "default": ""}},
    "outputs": {"media": {"type": "file/path"}},
    "runtime": {"type": "python", "entry": "plugin.py:run"},
}
SRC_CODE = (
    "from pathlib import Path\n"
    "def run(inputs, config, ctx):\n"
    "    p = (inputs.get('path') or '').strip()\n"
    "    if not p:\n"
    "        p = ctx.artifacts_dir / 'demo.txt'\n"
    "        p.parent.mkdir(parents=True, exist_ok=True)\n"
    "        p.write_text('demo payload', encoding='utf-8')\n"
    "    return {'media': str(p)}\n"
)
SINK_MANIFEST = {
    "id": "demo-sink", "version": "0.3.0", "kind": "sink",
    "inputs": {"media": {"type": "file/path", "required": True}},
    "outputs": {"note": {"type": "text/markdown"}},
    "runtime": {"type": "python", "entry": "sink.py:run"},
}
SINK_CODE = (
    "from pathlib import Path\n"
    "def run(inputs, config, ctx):\n"
    "    src = Path(inputs['media'])\n"
    "    out = ctx.artifacts_dir / 'note.md'\n"
    "    out.parent.mkdir(parents=True, exist_ok=True)\n"
    "    out.write_text(src.read_text(errors='replace')[:50], encoding='utf-8')\n"
    "    ctx.log('sink done')\n"
    "    return {'note': str(out)}\n"
)


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "musespiral-home"
    plugins = home / "plugins"
    for mid, manifest, code, entry in (
        ("demo-source", SRC_MANIFEST, SRC_CODE, "plugin.py"),
        ("demo-sink", SINK_MANIFEST, SINK_CODE, "sink.py"),
    ):
        d = plugins / mid
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / entry).write_text(code, encoding="utf-8")
    # 坏插件目录：进 skipped 不炸整面（list_plugins 三道门纪律）
    bad = plugins / "broken-x"
    bad.mkdir()
    (bad / "manifest.json").write_text("{}", encoding="utf-8")
    # 内置区隔离：root=None 生产路径会合并官方内置区（plugins_builtin/），
    # 本文件只断言沙箱用户区（demo-* 精确清单）——内置区指向空目录
    monkeypatch.setattr(
        plugins_loader, "builtin_plugins_dir", lambda: tmp_path / "no-builtin-plugins"
    )
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    return home


@pytest.fixture
def server(tmp_path: Path):
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(server, method: str, path: str, body: dict | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=15)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, raw.decode("utf-8", "replace")


# ── a) GET 读面 ────────────────────────────────────────────────


def test_get_plugins_list_shape_and_skipped(server) -> None:
    status, data = _call(server, "GET", "/api/plugins")
    assert status == 200
    ids = {p["manifest"]["id"] for p in data["plugins"]}
    assert ids == {"demo-source", "demo-sink"}
    assert len(data["skipped"]) == 1 and "broken-x" in data["skipped"][0]["dir"]
    assert data["skipped"][0]["reason"]


def test_plugin_check_ok_and_404(server) -> None:
    status, data = _call(server, "GET", "/api/plugins/demo-source/check")
    assert status == 200 and data["id"] == "demo-source" and data["deps"] == []
    status, data = _call(server, "GET", "/api/plugins/nope/check")
    assert status == 404 and data["ok"] is False
    # 穿越形态拒绝（动态段内不得再含 /）
    assert _call(server, "GET", "/api/plugins/a/b/check")[0] == 404


def test_pipelines_list_and_detail_rehydrate_manifest(server) -> None:
    nodes = [{"id": "demo-source", "plugin": dict(SRC_MANIFEST)},
             {"id": "demo-sink", "plugin": dict(SINK_MANIFEST)}]
    assert _call(server, "POST", "/api/pipelines",
                 {"id": "p1", "name": "链一", "nodes": nodes, "confirm": True})[0] == 200
    status, data = _call(server, "GET", "/api/pipelines")
    assert status == 200
    saved = [p for p in data["pipelines"] if p["id"] == "p1"]
    assert len(saved) == 1 and saved[0]["name"] == "链一" and len(saved[0]["nodes"]) == 2
    # 再水化：plugin 回到完整 manifest（画布 in/out 类型与 hasSegs 门控的数据源）
    assert saved[0]["nodes"][0]["plugin"]["id"] == "demo-source"
    assert saved[0]["nodes"][0]["plugin"]["outputs"]["media"]["type"] == "file/path"
    status, detail = _call(server, "GET", "/api/pipelines/p1")
    assert status == 200 and detail["nodes"][1]["plugin"]["id"] == "demo-sink"
    assert _call(server, "GET", "/api/pipelines/none")[0] == 404


# ── b) POST 保存（归一 + 后端真拒非法顺序）─────────────────────


def test_save_persists_engine_shape_on_disk(server, _isolate_home: Path) -> None:
    nodes = [{"id": "demo-source", "plugin": dict(SRC_MANIFEST)},
             {"id": "demo-sink", "plugin": dict(SINK_MANIFEST)}]
    status, data = _call(server, "POST", "/api/pipelines",
                         {"id": "p2", "nodes": nodes, "confirm": True})
    assert status == 200 and data["ok"] is True and data["nodes"] == 2
    raw = json.loads((_isolate_home / "pipelines" / "p2.json").read_text(encoding="utf-8"))
    # 落盘=引擎形状（plugin 是 id 字符串——plan_pipeline 直消费）
    assert raw["nodes"] == [{"id": "demo-source", "plugin": "demo-source"},
                            {"id": "demo-sink", "plugin": "demo-sink"}]


def test_save_rejects_illegal_order_backend_400(server, _isolate_home: Path) -> None:
    """后端真拒非法顺序（复核 high 验收点）：sink 在前 → 400 契约文案，零落盘。"""
    bad = [{"id": "demo-sink", "plugin": dict(SINK_MANIFEST)},
           {"id": "demo-source", "plugin": dict(SRC_MANIFEST)}]
    status, data = _call(server, "POST", "/api/pipelines",
                         {"id": "bad-order", "nodes": bad, "confirm": True})
    assert status == 400
    assert "契约校验未过" in data["error"]
    assert "只能位于流水线结尾" in data["error"] and "只能位于流水线开头" in data["error"]
    assert not (_isolate_home / "pipelines" / "bad-order.json").exists()


def test_save_rejects_bad_id_unknown_plugin_and_missing_confirm(server) -> None:
    nodes = [{"id": "demo-source", "plugin": "demo-source"}]
    for pid in ("", "../evil", "带空格 的id"):
        status, data = _call(server, "POST", "/api/pipelines",
                             {"id": pid, "nodes": nodes, "confirm": True})
        assert status == 400, pid
    unknown = [{"id": "x", "plugin": {"id": "not-installed"}}]
    status, data = _call(server, "POST", "/api/pipelines",
                         {"id": "p3", "nodes": unknown, "confirm": True})
    assert status == 400 and "不可用" in data["error"]
    # confirm 闸门（全局纪律：缺省拒绝执行）
    status, _ = _call(server, "POST", "/api/pipelines",
                      {"id": "p4", "nodes": nodes})
    assert status == 400


def test_plugins_refresh_confirm_gate_and_shape(server) -> None:
    status, _ = _call(server, "POST", "/api/plugins/refresh", {})
    assert status == 400  # confirm 闸门
    status, data = _call(server, "POST", "/api/plugins/refresh", {"confirm": True})
    assert status == 200 and data["ok"] is True and len(data["plugins"]) == 2


# ── c) POST 运行 + runs 读面 ───────────────────────────────────


def _save_and_run(server, pid: str = "r1") -> dict:
    nodes = [{"id": "demo-source", "plugin": dict(SRC_MANIFEST)},
             {"id": "demo-sink", "plugin": dict(SINK_MANIFEST)}]
    assert _call(server, "POST", "/api/pipelines",
                 {"id": pid, "nodes": nodes, "confirm": True})[0] == 200
    status, data = _call(server, "POST", "/api/pipelines/run",
                         {"id": pid, "confirm": True})
    assert status == 200 and data["ok"] is True
    assert data["job_id"] and data["run_id"]
    return data


def _wait_job_done(server, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, job = _call(server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        if job.get("status") in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"job 未在 {timeout}s 内终态: {job}")


def test_run_full_path_and_runs_reader(server, _isolate_home: Path) -> None:
    out = _save_and_run(server)
    job = _wait_job_done(server, out["job_id"])
    assert job["status"] == "done", job
    status, run = _call(server, "GET", f"/api/pipelines/runs/{out['run_id']}")
    assert status == 200
    assert run["status"] == "done"
    assert [s["status"] for s in run["steps"]] == ["ok", "ok"]
    assert run["steps"][0]["step"] == "demo-source"
    assert run["artifacts"]
    # plugins 输出类型表（前端 hasSegs 门控数据源）：本链无 segments 型输出
    outs = {p["id"]: {k: v["type"] for k, v in p["outputs"].items()} for p in run["plugins"]}
    assert outs == {"demo-source": {"media": "file/path"},
                    "demo-sink": {"note": "text/markdown"}}
    # meta.json 终态写全（psp 扩展：steps/artifacts 落盘为唯一真相）
    meta = json.loads((_isolate_home / "pipeline_runs" / out["run_id"] / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["status"] == "done" and len(meta["steps"]) == 2 and meta["artifacts"]


def test_run_404_and_traversal_guard(server) -> None:
    assert _call(server, "POST", "/api/pipelines/run",
                 {"id": "nope", "confirm": True})[0] == 404
    assert _call(server, "GET", "/api/pipelines/runs/zz-none")[0] == 404
    assert _call(server, "GET", "/api/pipelines/runs/../evil")[0] == 404
    assert _call(server, "GET", "/api/pipelines/runs/a/segments")[0] == 404  # E-4 已接驳：run a 不存在 → 404


def test_run_failure_reader_carries_error(server, _isolate_home: Path) -> None:
    """失败语义读面：source 因输入文件缺失失败 → runs 读面 status=failed +
    error 带失败节点与原因（前端 toast 消费）。

    坏路径以引擎形状 inputs 字面值直供（plan 不校验字面值存在性——运行期
    check_input/插件自校验拒），直跑引擎后走 HTTP 读面对拍。"""
    nodes_bad = [{"id": "demo-source", "plugin": "demo-source",
                  "inputs": {"path": str(_isolate_home / "no-such.bin")}},
                 {"id": "demo-sink", "plugin": "demo-sink"}]
    from musespiral.services import pipeline_service_plugins as psp
    psp.plan_pipeline(nodes_bad)  # 阶段一过（字面值存在性属运行期）
    result = psp.run_pipeline(nodes_bad, run_id="rf-fail-0001")
    # source 直传坏路径不查存在性（fixture 语义），失败定位在消费方 demo-sink
    # 读文件处——失败即停，sink 之后无节点
    assert result["status"] == "failed" and result["failed_at"] == "demo-sink"
    status, run = _call(server, "GET", "/api/pipelines/runs/rf-fail-0001")
    assert status == 200 and run["status"] == "failed"
    assert "demo-sink" in run.get("error", "")


# ── e) E-4 转写编辑接驳（PL-6 批 2026-11-20，契约 §3.19 as-built 修订 2）──
# GET segments（编辑版优先）/ POST /api/pipelines/runs/segments（精确路径 +
# body run_id——PL-3 先例）/ GET audio?start=&end=（裁段 wav 字节流）。


def _make_transcript_run(
    home: Path, run_id: str = "pl-e4-0001", *, with_ranges: bool = True,
    with_src_ref: str | None = None,
) -> Path:
    """手工造带转写/ranges（可选源媒体 path 引用）的 run 目录——segments/
    audio 端点只读 workspace 与 meta.json，无需真跑引擎。"""
    run_dir = home / "pipeline_runs" / run_id
    tr = run_dir / "workspace" / "tr"
    tr.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    (tr / "outputs.json").write_text(json.dumps({
        "inline": {"transcript": [
            {"start": 0.0, "end": 2.0, "text": "第一段",
             "avg_logprob": -0.2, "no_speech_prob": 0.05},
            {"start": 3.0, "end": 5.5, "text": "第二段",
             "avg_logprob": -0.3, "no_speech_prob": 0.04},
        ], "duration_s": 5.5}, "refs": {}}), encoding="utf-8")
    if with_ranges:
        ks = run_dir / "workspace" / "ks"
        ks.mkdir(parents=True, exist_ok=True)
        (ks / "outputs.json").write_text(json.dumps({
            "inline": {"ranges": [{"start": 0.0, "end": 2.0, "label": "开头"}]},
            "refs": {}}), encoding="utf-8")
    if with_src_ref is not None:
        src = run_dir / "workspace" / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "outputs.json").write_text(json.dumps({
            "inline": {"size": 1},
            "refs": {"file": {"kind": "path", "file": with_src_ref}}}),
            encoding="utf-8")
    return run_dir


def test_segments_get_post_and_edited_preference(server, _isolate_home: Path) -> None:
    """GET 原始 → POST 编辑版（落产出节点目录——key-segment/frame-grab 的
    既有消费位置，原始 outputs.json 不覆盖）→ GET 编辑版优先。"""
    run_dir = _make_transcript_run(_isolate_home)
    status, data = _call(server, "GET", "/api/pipelines/runs/pl-e4-0001/segments")
    assert status == 200
    assert [s["text"] for s in data["segments"]] == ["第一段", "第二段"]
    assert data["ranges"] == [{"start": 0.0, "end": 2.0, "label": "开头"}]

    edited = [{"start": 0.0, "end": 2.0, "text": "第一段（改）"},
              {"start": 3.0, "end": 5.5, "text": "第二段"}]
    rng = [{"start": 3.0, "end": 5.5, "label": "后段"}]
    status, saved = _call(server, "POST", "/api/pipelines/runs/segments",
                          {"run_id": "pl-e4-0001", "segments": edited,
                           "ranges": rng, "confirm": True})
    assert status == 200 and saved["ok"]
    assert saved["saved"] == ["segments.edited.json", "ranges.edited.json"]
    # 落盘位置 = 产出该端口的节点目录（下游插件 _read_edited_file 的读取位置）
    assert json.loads((run_dir / "workspace" / "tr" / "segments.edited.json")
                      .read_text(encoding="utf-8")) == edited
    assert json.loads((run_dir / "workspace" / "ks" / "ranges.edited.json")
                      .read_text(encoding="utf-8")) == rng
    assert (run_dir / "workspace" / "tr" / "outputs.json").is_file()  # 原始不覆盖

    status, data = _call(server, "GET", "/api/pipelines/runs/pl-e4-0001/segments")
    assert [s["text"] for s in data["segments"]] == ["第一段（改）", "第二段"]
    assert data["ranges"] == rng


def test_segments_validation_and_error_faces(server, _isolate_home: Path) -> None:
    """POST 校验面（空列表/start≥end/缺 text/缺 confirm/run 不存在）+
    GET 404 面（run 无转写产物）。"""
    _make_transcript_run(_isolate_home)
    base = {"run_id": "pl-e4-0001", "confirm": True}
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {**base, "segments": []})[0] == 400
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {**base, "segments": [{"start": 2.0, "end": 1.0, "text": "x"}]})[0] == 400
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {**base, "segments": [{"start": 0.0, "end": 1.0}]})[0] == 400
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {"run_id": "pl-e4-0001",
                  "segments": [{"start": 0.0, "end": 1.0, "text": "t"}]})[0] == 400
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {"run_id": "no-such-run",
                  "segments": [{"start": 0.0, "end": 1.0, "text": "t"}],
                  "confirm": True})[0] == 404
    # 无 ranges 产出节点但 body 带 ranges → 400 可读（无处落盘）
    _make_transcript_run(_isolate_home, "pl-e4-norng", with_ranges=False)
    assert _call(server, "POST", "/api/pipelines/runs/segments",
                 {"run_id": "pl-e4-norng",
                  "segments": [{"start": 0.0, "end": 1.0, "text": "t"}],
                  "ranges": [], "confirm": True})[0] == 400
    # run 无转写产物（workspace 空）→ GET 404
    empty = _isolate_home / "pipeline_runs" / "pl-e4-empty"
    (empty / "workspace").mkdir(parents=True)
    (empty / "meta.json").write_text("{}", encoding="utf-8")
    assert _call(server, "GET", "/api/pipelines/runs/pl-e4-empty/segments")[0] == 404
    assert _call(server, "GET", "/api/pipelines/runs/none-such/segments")[0] == 404


def test_audio_clip_end_to_end(server, _isolate_home: Path, tmp_path: Path) -> None:
    """裁段音频：真 ffmpeg 造 2s 正弦源 → GET ?start=&end= 回 wav 字节流
    （RIFF 魔数 + Content-Type）；错误面（start≥end/非数值/超 120s 400、
    源媒体缺失 404）。ffmpeg 缺席跳过（不造假通过）。"""
    import shutil as _shutil
    import subprocess as _sp

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg 不可用——裁段音频端到端跳过")
    media = tmp_path / "tone.wav"
    proc = _sp.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2", "-ac", "1", "-ar", "16000",
                    str(media)], capture_output=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-300:]
    _make_transcript_run(_isolate_home, "pl-e4-audio", with_src_ref=str(media))

    def _raw_get(path: str):
        host, port = server.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=30)
        conn.request("GET", path)
        resp = conn.getresponse()
        raw = resp.read()
        ctype = resp.getheader("Content-Type")
        conn.close()
        return resp.status, ctype, raw

    status, ctype, raw = _raw_get(
        "/api/pipelines/runs/pl-e4-audio/audio?start=0.2&end=0.8")
    assert status == 200 and ctype == "audio/wav"
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    assert 16000 <= len(raw) <= 64000  # 0.6s×16kHz×2B≈19k（±头部/编码余量）

    assert _raw_get("/api/pipelines/runs/pl-e4-audio/audio?start=2&end=1")[0] == 400
    assert _raw_get("/api/pipelines/runs/pl-e4-audio/audio?start=x&end=1")[0] == 400
    assert _raw_get("/api/pipelines/runs/pl-e4-audio/audio?start=0&end=121")[0] == 400
    assert _raw_get("/api/pipelines/runs/none-such/audio?start=0&end=1")[0] == 404
    _make_transcript_run(_isolate_home, "pl-e4-deadref",
                         with_src_ref=str(tmp_path / "moved-away.wav"))
    assert _raw_get("/api/pipelines/runs/pl-e4-deadref/audio?start=0&end=1")[0] == 404


# ── f) 三态裁决端点（AI-1 批 2026-11-21，契约 §3.19 修订 3；正典=
# 《mdpub-AI产物裁决机制设计-2026-11-21》§二/§五）────────────────────
# POST /api/pipelines/runs/<run_id>/adopt|discard（动态段 POST，run_id 在
# URL 段内；body {step:'ks'|'aw', indices?}；confirm 闸门走 server 通用层）。
# 落点=生效文档：ranges 有编辑版改编辑版（E-4 生效面）、无编辑版改原始
# outputs.json inline；article 直改 inline 且顶层 article_status 同步。


def _make_adjudication_run(
    home: Path, run_id: str = "pl-adj-0001", *,
    with_ranges: bool = True, with_edited: bool = False, with_article: bool = True,
) -> Path:
    """手工造带 ks ranges / aw article（可选 E-4 编辑版）的 run 目录——
    裁决端点只读写 workspace 与 meta.json，无需真跑引擎。"""
    run_dir = home / "pipeline_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # 最小 transcript 节点（GET segments 读面要求有转写产物才回 ranges）
    tr = run_dir / "workspace" / "tr"
    tr.mkdir(parents=True, exist_ok=True)
    (tr / "outputs.json").write_text(json.dumps({
        "inline": {"transcript": [{"start": 0.0, "end": 2.0, "text": "第一段"}]},
        "refs": {}}), encoding="utf-8")
    if with_ranges:
        ks = run_dir / "workspace" / "ks"
        ks.mkdir(parents=True, exist_ok=True)
        (ks / "outputs.json").write_text(json.dumps({
            "inline": {"ranges": [
                {"start": 0.0, "end": 2.0, "label": "开头", "status": "draft"},
                {"start": 3.0, "end": 5.5, "label": "后段", "status": "draft"},
            ]}, "refs": {}}), encoding="utf-8")
        if with_edited:  # E-4 编辑版在场（1 项）→ 裁决落编辑版
            (ks / "ranges.edited.json").write_text(json.dumps(
                [{"start": 3.0, "end": 5.5, "label": "后段（改）", "status": "draft"}]),
                encoding="utf-8")
    if with_article:
        aw = run_dir / "workspace" / "aw"
        aw.mkdir(parents=True, exist_ok=True)
        (aw / "outputs.json").write_text(json.dumps({
            "inline": {"article": {"path": "/tmp/a.md", "status": "draft"},
                       "article_status": "draft", "anchor_map": {"p1": [0.0]}},
            "refs": {}}), encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    return run_dir


def _ks_outputs(home: Path, run_id: str) -> list[dict]:
    ks = home / "pipeline_runs" / run_id / "workspace" / "ks"
    return json.loads((ks / "outputs.json").read_text(encoding="utf-8"))["inline"]["ranges"]


def test_adjudicate_ks_adopt_all_and_discard_subset(server, _isolate_home: Path) -> None:
    """裁决批硬要求 18（金路径）：adopt 全集（indices 省略）→ 全部 adopted +
    run log 留痕；discard 子集只翻选中项；重复裁决幂等 changed=0。"""
    home = _isolate_home
    _make_adjudication_run(home, "pl-adj-a1")
    status, data = _call(server, "POST",
                         "/api/pipelines/runs/pl-adj-a1/adopt",
                         {"step": "ks", "confirm": True})
    assert status == 200
    assert data == {"ok": True, "run_id": "pl-adj-a1", "step": "ks",
                    "status": "adopted", "changed": 2, "total": 2}
    assert all(r["status"] == "adopted" for r in _ks_outputs(home, "pl-adj-a1"))
    log = (home / "pipeline_runs" / "pl-adj-a1" / "log.jsonl").read_text("utf-8")
    assert "裁决" in log and "adopted" in log and "ranges" in log

    status, data = _call(server, "POST",
                         "/api/pipelines/runs/pl-adj-a1/discard",
                         {"step": "ks", "indices": [1], "confirm": True})
    assert status == 200 and data["status"] == "discarded" and data["changed"] == 1
    ranges = _ks_outputs(home, "pl-adj-a1")
    assert ranges[0]["status"] == "adopted" and ranges[1]["status"] == "discarded"

    _, data = _call(server, "POST", "/api/pipelines/runs/pl-adj-a1/discard",
                    {"step": "ks", "indices": [1], "confirm": True})
    assert data["changed"] == 0  # 已 discarded 再 discard → 幂等零改


def test_adjudicate_ks_edited_file_preferred(server, _isolate_home: Path) -> None:
    """E-4 编辑版在场 → 裁决落编辑版（生效文档），原始 outputs.json 保留
    对照不覆盖；GET segments 读面（编辑版优先）回裁决后的态——徽章数据源
    与裁决写面同源。"""
    home = _isolate_home
    _make_adjudication_run(home, "pl-adj-e1", with_edited=True)
    status, data = _call(server, "POST", "/api/pipelines/runs/pl-adj-e1/adopt",
                         {"step": "ks", "confirm": True})
    assert status == 200 and data["changed"] == 1 and data["total"] == 1  # 编辑版仅 1 项
    ks = home / "pipeline_runs" / "pl-adj-e1" / "workspace" / "ks"
    edited = json.loads((ks / "ranges.edited.json").read_text(encoding="utf-8"))
    assert edited[0]["status"] == "adopted" and edited[0]["label"] == "后段（改）"
    assert all(r["status"] == "draft" for r in _ks_outputs(home, "pl-adj-e1"))  # 原始对照
    status, seg = _call(server, "GET", "/api/pipelines/runs/pl-adj-e1/segments")
    assert status == 200 and seg["ranges"][0]["status"] == "adopted"


def test_adjudicate_aw_article_and_status_mirror(server, _isolate_home: Path) -> None:
    """aw 成稿裁决：单产物整稿翻态，article.status 与顶层 article_status
    直判面同步（下游免解包判态）。"""
    home = _isolate_home
    _make_adjudication_run(home, "pl-adj-w1")
    status, data = _call(server, "POST", "/api/pipelines/runs/pl-adj-w1/discard",
                         {"step": "aw", "confirm": True})
    assert status == 200 and data["status"] == "discarded"
    assert data["changed"] == 1 and data["total"] == 1
    doc = json.loads((home / "pipeline_runs" / "pl-adj-w1" / "workspace" / "aw"
                      / "outputs.json").read_text(encoding="utf-8"))
    assert doc["inline"]["article"]["status"] == "discarded"
    assert doc["inline"]["article_status"] == "discarded"
    status, data = _call(server, "POST", "/api/pipelines/runs/pl-adj-w1/adopt",
                         {"step": "aw", "confirm": True})
    assert status == 200 and data["changed"] == 1  # discarded → adopted 可逆


def test_adjudicate_rejections(server, _isolate_home: Path) -> None:
    """裁决批硬要求 18（非法参数可读拒绝）：step 枚举外/缺 step/indices 非数
    组/空数组/非整数/布尔/负数/越界/aw+indices 全 400 且文案可读；confirm
    闸门 400；run 不存在与穿越 404；无产物节点 400；GET 该动态路径 404。"""
    home = _isolate_home
    _make_adjudication_run(home, "pl-adj-b1")
    adopt = "/api/pipelines/runs/pl-adj-b1/adopt"
    discard = "/api/pipelines/runs/pl-adj-b1/discard"
    cases = [
        (adopt, {"step": "fg", "confirm": True}),       # step 枚举外
        (adopt, {"confirm": True}),                      # 缺 step
        (adopt, {"step": "ks", "indices": "0", "confirm": True}),    # 非数组
        (adopt, {"step": "ks", "indices": [], "confirm": True}),     # 空数组
        (adopt, {"step": "ks", "indices": [0, "x"], "confirm": True}),  # 非整数
        (adopt, {"step": "ks", "indices": [True], "confirm": True}),    # 布尔拒
        (adopt, {"step": "ks", "indices": [-1], "confirm": True}),      # 负数
        (adopt, {"step": "ks", "indices": [5], "confirm": True}),       # 越界
        (discard, {"step": "aw", "indices": [0], "confirm": True}),     # aw 不接受 indices
    ]
    for path, body in cases:
        code, err = _call(server, "POST", path, body)
        assert code == 400, (path, body, code, err)
        assert err["ok"] is False and isinstance(err["error"], str) and err["error"]
    # 落盘未被动过（拒绝路径零副作用）
    assert all(r["status"] == "draft" for r in _ks_outputs(home, "pl-adj-b1"))
    # confirm 闸门（全局纪律）
    assert _call(server, "POST", adopt, {"step": "ks"})[0] == 400
    assert _call(server, "POST", discard, {"step": "aw"})[0] == 400
    # 404 面：run 不存在 / 穿越形态（run_id 白名单回验——两段形 ../adopt 进
    # 裁决分派后 _RUN_ID_RE 拒 → 404 fail-closed；多段形 ..​/evil/adopt 不合
    # 裁决路径形 → 落 GET 族守卫 405，与 POST 打 GET 动态路径既有语义一致）
    assert _call(server, "POST", "/api/pipelines/runs/no-such-run/adopt",
                 {"step": "ks", "confirm": True})[0] == 404
    assert _call(server, "POST", "/api/pipelines/runs/../adopt",
                 {"step": "ks", "confirm": True})[0] == 404
    assert _call(server, "POST", "/api/pipelines/runs/../evil/adopt",
                 {"step": "ks", "confirm": True})[0] == 405
    # 无产物节点 → 400 可读（无处裁决）
    _make_adjudication_run(home, "pl-adj-b2", with_ranges=False, with_article=False)
    code, err = _call(server, "POST", "/api/pipelines/runs/pl-adj-b2/adopt",
                      {"step": "ks", "confirm": True})
    assert code == 400 and "ranges" in err["error"]
    code, err = _call(server, "POST", "/api/pipelines/runs/pl-adj-b2/discard",
                      {"step": "aw", "confirm": True})
    assert code == 400 and "article" in err["error"]
    # 方法面：GET 裁决路径走 runs 读面（run_id 段含 /adopt 不合白名单）→ 404
    assert _call(server, "GET", "/api/pipelines/runs/pl-adj-b1/adopt")[0] == 404


# ── g) ai_verdicts 读面（AI-1 批 2026-11-21，④ 前端徽章的数据前提）──────
# GET /api/pipelines/runs/<run_id> 新增 ai_verdicts={节点id:{端口:态}}：只放
# 标量裁决态（零正文，与「计数无正文」纪律一致）。ranges 走**编辑版优先**
# 的整组态（与 GET segments 同源——只读 outputs.json 会把已裁决的 run 读回
# draft）；article 取 inline.article.status。老 run（本批前的产物无 status
# 字段）该端口键**不存在**（不是 null）——前端据此不渲染徽章。


def test_ai_verdicts_draft_then_adopted(server, _isolate_home: Path) -> None:
    """硬要求：真跑一条 auto_adopt=false 的链 → ai_verdicts 报 draft；
    调 adopt 后 → 报 adopted（读面与裁决写面同源）。"""
    home = _isolate_home
    _make_adjudication_run(home, "pl-av-0001")
    status, run = _call(server, "GET", "/api/pipelines/runs/pl-av-0001")
    assert status == 200
    assert run["ai_verdicts"]["ks"]["ranges"] == "draft"
    assert run["ai_verdicts"]["aw"]["article"] == "draft"

    _call(server, "POST", "/api/pipelines/runs/pl-av-0001/adopt",
          {"step": "ks", "confirm": True})
    _call(server, "POST", "/api/pipelines/runs/pl-av-0001/adopt",
          {"step": "aw", "confirm": True})
    _, run = _call(server, "GET", "/api/pipelines/runs/pl-av-0001")
    assert run["ai_verdicts"]["ks"]["ranges"] == "adopted"
    assert run["ai_verdicts"]["aw"]["article"] == "adopted"


def test_ai_verdicts_old_run_absent_not_null(server, _isolate_home: Path) -> None:
    """硬要求（老 run 兼容）：产物无 status 字段 → ai_verdicts 里该键**不存在**
    （不是 null/空串）——前端据缺席判「无状态可显示」不渲染徽章，零回归。"""
    home = _isolate_home
    run_dir = home / "pipeline_runs" / "pl-av-old1"
    ks = run_dir / "workspace" / "ks"
    ks.mkdir(parents=True)
    (ks / "outputs.json").write_text(json.dumps({
        "inline": {"ranges": [{"start": 0.0, "end": 2.0, "label": "开头"}]},
        "refs": {}}), encoding="utf-8")
    aw = run_dir / "workspace" / "aw"
    aw.mkdir(parents=True)
    (aw / "outputs.json").write_text(json.dumps({
        "inline": {"article": {"path": "/tmp/a.md"}, "anchor_map": {}},
        "refs": {}}), encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    status, run = _call(server, "GET", "/api/pipelines/runs/pl-av-old1")
    assert status == 200
    assert run["ai_verdicts"] == {}, run["ai_verdicts"]


def test_ai_verdicts_ranges_group_mixed_and_edited_first(server, _isolate_home: Path) -> None:
    """ranges 整组态：全同→该态；混态→mixed；编辑版在场→读编辑版（不是原始
    outputs.json，否则已裁决的 run 会被读回 draft）。"""
    home = _isolate_home
    run_dir = home / "pipeline_runs" / "pl-av-mix1"
    ks = run_dir / "workspace" / "ks"
    ks.mkdir(parents=True)
    # 原始 outputs.json 全 draft；编辑版两项全 adopted → 读面应报 adopted
    (ks / "outputs.json").write_text(json.dumps({
        "inline": {"ranges": [
            {"start": 0.0, "end": 2.0, "status": "draft"},
            {"start": 3.0, "end": 5.0, "status": "draft"},
        ]}, "refs": {}}), encoding="utf-8")
    (ks / "ranges.edited.json").write_text(json.dumps([
        {"start": 0.0, "end": 2.0, "status": "adopted"},
        {"start": 3.0, "end": 5.0, "status": "adopted"},
    ]), encoding="utf-8")
    (run_dir / "meta.json").write_text(
        json.dumps({"status": "done", "steps": [], "plugins": []}), encoding="utf-8")
    _, run = _call(server, "GET", "/api/pipelines/runs/pl-av-mix1")
    assert run["ai_verdicts"]["ks"]["ranges"] == "adopted"

    # 改为异态（一项 adopted 一项 discarded）→ mixed
    (ks / "ranges.edited.json").write_text(json.dumps([
        {"start": 0.0, "end": 2.0, "status": "adopted"},
        {"start": 3.0, "end": 5.0, "status": "discarded"},
    ]), encoding="utf-8")
    _, run = _call(server, "GET", "/api/pipelines/runs/pl-av-mix1")
    assert run["ai_verdicts"]["ks"]["ranges"] == "mixed"
