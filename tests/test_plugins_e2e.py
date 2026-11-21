"""插件系统集成轮测试（首插件族收官，2026-11-20）。

PL-5 全 mock 链（test_plugins_builtin_pl5.py §f）之上补三类真集成：

  ① 四节点端到端（真实媒体工具链）：ffmpeg lavfi 生成 6s 小视频 fixture，
     transcribe 仅 mock whisper 推理（faster_whisper 假件——真实 1.5GB 模型
     不进 CI），ffmpeg 抽音轨/抽帧、ffprobe 时长、key-segment 规则切片、
     engine 四阶段全部走真代码；断言 **最终 images 每一项的 ts 三级可回溯**
     （images[k].ts → ranges[j].start(+0.3) → segments[i].start，正典 §9.3
     ——「时间戳能一路追到底」是视频转文章的核心增量，本测试是它的
     机器锁）。plugins_root=None 走生产合并扫描（内置区+用户区）——
     同时锁住「内置插件经生产路径可被发现并执行」。
  ② 插件加载实测：studio 真服务（make_server 线程起，非函数直调），
     GET /api/plugins 能看到 6 个 builtin 插件（PL-6 批 +article-writer/
     +material-sink；与 test_plugin_endpoints 的沙箱用户区用例互补——
     那里把内置区指向空目录，这里保真内置区）。
  ③ 依赖检查 MISSING_DEP 面：transcribe 入口 PYTHON_DEPS 声明
     faster_whisper，check 面在包缺席时 status="MISSING_DEP"（探测零执行
     零崩溃——faster-whisper 是可选 pip 依赖，venv 默认不带，本机即缺席
     真态；绿态/缺席态都用 monkeypatch 钉死，机器无关）。
"""
from __future__ import annotations

import http.client
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from musespiral import studio
from musespiral.infra import plugins_loader as loader
from musespiral.infra.ai import gateway as ai_gateway
from musespiral.infra.ai import profiles as ai_profiles
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services import plugins_service as svc

BUILTIN_DIR = loader.builtin_plugins_dir()
# PL-6（2026-11-20）起内置区六插件：四节点链 + article-writer + material-sink
_BUILTIN_IDS = {"local-file", "transcribe", "key-segment", "frame-grab", "article-writer", "material-sink"}

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


# ── 共享：MUSESPIRAL_HOME 沙箱 + AI 未配置钉死 ─────────────────────────


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUSESPIRAL_HOME 沙箱：用户插件区清空（只有内置区）+ ai.json 不存在
    （key-segment 走规则切片，不触真实 ~/.musespiral）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture(autouse=True)
def _ai_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: False)


# ── ① 四节点端到端（真实媒体工具链）────────────────────────────────


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str,
                 avg_logprob: float, no_speech_prob: float) -> None:
        self.start, self.end, self.text = start, end, text
        self.avg_logprob, self.no_speech_prob = avg_logprob, no_speech_prob


class _FakeInfo:
    duration = 6.0
    language = "zh"
    language_probability = 0.97


class _FakeWhisperModel:
    """whisper 推理假件（唯一 mock 点）：按 model_ref 关键字返回三套段落，
    时间戳与 6s fixture 视频对齐——抽帧真发生在这些秒上。"""

    def __init__(self, model_ref: str, device: str = "cpu", compute_type: str = "int8") -> None:
        assert Path(model_ref).exists(), "config.model 必须指向存在的本地目录（探测真态）"
        self.model_ref = model_ref

    def transcribe(self, audio: Any, **kwargs: Any):
        # 2026-11-20 起插件传 float32 ndarray（16kHz mono，绕 PyAV 19 断裂），
        # 不再是 wav 路径——真态断言改为「上游真实抽出音轨并解码成数组」
        assert isinstance(audio, np.ndarray) and audio.dtype == np.float32, (
            "上游必须真实解码出 float32 音频数组（16kHz mono PCM）"
        )
        assert audio.ndim == 1 and 90000 <= len(audio) <= 102000, (
            f"6s fixture @16kHz 应 ≈96000 样本，实测 {len(audio)}"
        )
        assert float(np.abs(audio).max()) <= 1.0 + 1e-6, "PCM 幅度已归一到 [-1,1]"
        assert kwargs.get("vad_filter") is True
        assert kwargs.get("condition_on_previous_text") is False
        if "m-a" in self.model_ref:  # 双转写节点场景·A 路
            segments = [
                _FakeSegment(0.0, 1.0, " A-原始开头 ", -0.22, 0.02),
                _FakeSegment(3.0, 4.5, " A-原始结尾 ", -0.31, 0.03),
            ]
        elif "m-b" in self.model_ref:  # 双转写节点场景·B 路
            segments = [
                _FakeSegment(0.5, 2.0, " B-原始转写 ", -0.25, 0.02),
                _FakeSegment(5.0, 6.0, " B-原始后段 ", -0.29, 0.03),
            ]
        elif "hole" in self.model_ref:  # 语音空洞判例：首段极短+组内停顿<1.2s、
            # 无标题词（不打第二切点）→ 不切组，组内留 (0.1,1.1) 空洞
            segments = [
                _FakeSegment(0.0, 0.1, " 嗯 ", -0.40, 0.05),
                _FakeSegment(1.1, 6.0, " 这个那个东西啊 ", -0.26, 0.03),
            ]
        else:  # 默认三话题组
            segments = [
                _FakeSegment(0.0, 1.5, " 首先讲重点：Doris 列式存储 ", -0.21, 0.02),
                _FakeSegment(1.5, 3.0, " 数据写内存再刷盘 ", -0.35, 0.04),
                _FakeSegment(4.2, 5.8, " 总结：查询路由 ", -0.28, 0.03),
            ]
        return iter(segments), _FakeInfo()


def _install_fake_fw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeWhisperModel))


@pytest.fixture(scope="module")
def small_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """真实 6s 小视频 fixture（lavfi 测试卡+正弦音轨，mp4/h264+aac）。
    生成一次 module 级共享；ffmpeg 缺席的机器跳过（不造假通过）。"""
    if not (_FFMPEG and _FFPROBE):
        pytest.skip("ffmpeg/ffprobe 不可用——真实媒体链端到端跳过")
    out = tmp_path_factory.mktemp("e2e-media") / "fixture_6s.mp4"
    proc = subprocess.run(
        [_FFMPEG, "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         str(out)],
        capture_output=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-500:]
    assert out.stat().st_size > 0
    return out


def _expected_ts(r: dict[str, Any]) -> float:
    """frame-grab._frame_ts 的测试镜像（三支：基线+0.3 / 语音窗起点 / 中点）。"""
    start, end = float(r["start"]), float(r["end"])
    ts = start + 0.3
    if ts >= end:
        return round((start + end) / 2.0, 3)
    wins = r.get("segment_windows") or []
    wins = [w for w in wins if isinstance(w, (list, tuple)) and len(w) == 2]
    if wins and not any(float(w[0]) <= ts < float(w[1]) for w in wins):
        for w in sorted(wins, key=lambda w: float(w[0])):
            if ts < float(w[0]) < end:
                return round(float(w[0]), 3)
    return round(ts, 3)


def _read_outputs(run_dir: Path, node: str) -> dict[str, Any]:
    return json.loads(
        (run_dir / "workspace" / node / "outputs.json").read_text(encoding="utf-8")
    )["inline"]


def test_e2e_four_node_real_media_chain(
    small_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真链路四节点：真实视频/音轨/帧产物，仅 whisper 推理 mock。

    端到端断言核心：**最终 images 每一项的 ts 能追溯到某个 segment 的
    start**——三级链 images[k].ts → ranges[j].start(+0.3) → segments[i].start
    逐环验真（ts 用作成稿锚点 [#t=秒](#t=秒) 的数据面底线）。
    """
    _install_fake_fw(monkeypatch)
    model_dir = tmp_path / "whisper-model"
    model_dir.mkdir()  # 存在的本地目录 → 模型就位探测真态直认（零网络）

    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file",
             "inputs": {"path": str(small_video), "media_kind": "video"}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
        ],
        plugins_root=None,  # 生产合并扫描（内置区+用户区）——加载真态
        run_root=tmp_path / "runs",
        run_id="pl-e2e-4node",
    )
    assert result["status"] == "done", json.dumps(result, ensure_ascii=False)
    assert [s["status"] for s in result["steps"]] == ["ok", "ok", "ok", "ok"]

    run_dir = Path(result["run_dir"])
    segments = _read_outputs(run_dir, "tr")["transcript"]
    ranges = _read_outputs(run_dir, "ks")["ranges"]
    outputs = _read_outputs(run_dir, "fg")
    images, clips = outputs["images"], outputs["clips"]

    # source：真实探测的时长（fixture=6s，ffprobe 实测）；file 是 path 引用
    # （Workspace.put：PathLike → refs.path，原路径不复制——GB 级媒体纪律）
    src_raw = json.loads(
        (run_dir / "workspace" / "src" / "outputs.json").read_text(encoding="utf-8")
    )
    src = src_raw["inline"]
    assert src_raw["refs"]["file"]["kind"] == "path"
    assert src["duration_s"] == pytest.approx(6.0, abs=0.5)
    assert Path(src_raw["refs"]["file"]["file"]).is_file()

    # transcribe：真实音轨 + G-1 质量信号逐段透传（链路下游质量因子的数据面）
    assert len(segments) == 3
    assert all("avg_logprob" in s and "no_speech_prob" in s for s in segments)

    # key-segment 规则切片：每组 start 精确等于组首段 start（回溯锚的源头）
    assert ranges, "6s 三段转写应至少切出一个关键片段"
    seg_starts = {round(float(s["start"]), 3) for s in segments}
    for r in ranges:
        assert round(float(r["start"]), 3) in seg_starts, (
            f"range.start {r['start']} 不等于任何 segment.start（回溯链源头断裂）"
        )

    # frame-grab：真实 jpg 落盘 + 三级回溯逐环验真
    assert images, "images 为空——时间戳链断"
    assert clips == []  # frame 模式（manifest default）不产 clip
    for img in images:
        p = Path(img["path"])
        assert p.is_file() and p.stat().st_size > 0, f"帧产物缺失/空: {p}"
        assert p.suffix == ".jpg"
        assert str(p).startswith(str(run_dir / "workspace")), "产物须落 workspace 受管区"
        ts = float(img["ts"])
        src_r = img["source_range"]
        owner = next(
            (r for r in ranges if abs(_expected_ts(r) - ts) < 1e-6), None
        )
        assert owner is not None, f"ts={ts} 无法回溯到任何 range（第二级断）"
        assert owner["start"] == src_r["start"] and owner["end"] == src_r["end"], (
            "source_range 与命中 range 不一致"
        )
        # 第一级：range.start 就是某个转写段落的 start（「一路追到底」的终点）
        assert round(float(src_r["start"]), 3) in seg_starts, (
            f"source_range.start={src_r['start']} 不等于任何 segment.start"
        )
        # 强形式（复核修 2）：ts 落在真实语音窗内，不落组内语音空洞
        cover = next(
            (s for s in segments if float(s["start"]) <= ts <= float(s["end"])), None
        )
        assert cover is not None, f"ts={ts} 落在语音空洞（不在任何 segment 时间窗内）"

    # 阶段三收集：末节点产物入 <run_dir>/output/（images/clips 内联 json）
    out_dir = run_dir / "output"
    assert (out_dir / "images.json").is_file() and (out_dir / "clips.json").is_file()
    collected = json.loads((out_dir / "images.json").read_text(encoding="utf-8"))
    assert len(collected) == len(images)

    # 运行日志：四节点各留痕（可读失败面的数据源）
    log_nodes = {
        json.loads(line)["node"]
        for line in (run_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert {"src", "tr", "ks", "fg"} <= log_nodes

    # 终态 meta：runs 读面唯一真相（steps/artifacts 终态写全）
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "done" and len(meta["steps"]) == 4


def test_e2e_transcribe_missing_dep_breaks_chain_visibly(
    small_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真视频 + 缺席态钉死 faster-whisper：链在 tr 节点可读失败即停（引擎失败
    语义真态——不是静默吞或崩溃）。缺席用 sys.modules 钉死：2026-11-20 真素材
    实测后本 venv 常装 faster-whisper，「裸跑即缺席」不再成立——对齐本文件
    §③ 既有纪律（绿态/缺席态都 monkeypatch 钉死，机器无关）。"""
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import 即 ImportError
    media = small_video
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
        ],
        run_root=tmp_path / "runs",
        run_id="pl-e2e-nodep",
    )
    assert result["status"] == "failed" and result["failed_at"] == "tr"
    tr_step = next(s for s in result["steps"] if s["step"] == "tr")
    assert "pip install faster-whisper" in tr_step["detail"]
    assert [s["step"] for s in result["steps"]] == ["src", "tr"]  # 失败即停
    # 现场保留：失败态不收集 output/
    assert not (Path(result["run_dir"]) / "output").exists()


# ── ② 插件加载实测（真实 HTTP 面）─────────────────────────────────


@pytest.fixture
def live_server(tmp_path: Path):
    """真 studio 服务（make_server + serve_forever 线程），**内置区保真**
    （不重定向 builtin_plugins_dir）——GET /api/plugins 走生产合并扫描。"""
    root = tmp_path / "articles"
    root.mkdir()
    httpd = studio.make_server(root, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def _get(server, path: str) -> tuple[int, Any]:
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=15)
    conn.request("GET", path)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, json.loads(raw)


def test_live_server_lists_four_builtin_plugins(live_server) -> None:
    """启动后 GET /api/plugins：6 个 builtin 插件全部可见（manifest 完整；函数名沿用历史，断言面已随 PL-6 扩到六插件）。"""
    status, data = _get(live_server, "/api/plugins")
    assert status == 200
    by_id = {p["manifest"]["id"]: p for p in data["plugins"]}
    assert _BUILTIN_IDS <= set(by_id), f"builtin 缺席: {sorted(_BUILTIN_IDS - set(by_id))}"
    for pid in sorted(_BUILTIN_IDS):
        manifest = by_id[pid]["manifest"]
        assert manifest["version"] == "1.0.0"
        assert manifest["runtime"]["type"] == "python"
        assert manifest["runtime"]["entry"] == "plugin.py:run"
    # skipped 不因内置区出现新垃圾（用户区沙箱为空）
    assert data["skipped"] == []


def test_live_server_plugin_check_faces(live_server) -> None:
    """GET /api/plugins/<id>/check 真实 HTTP 面：命令面探测生效，payload
    携带 python_deps 与聚合 status（缺 faster-whisper → MISSING_DEP）。"""
    status, data = _get(live_server, "/api/plugins/transcribe/check")
    assert status == 200 and data["id"] == "transcribe"
    assert data["python_deps"] and data["python_deps"][0]["cmd"] == "faster_whisper"
    # 本 venv 无 faster-whisper（真缺席）→ 顶层 MISSING_DEP；venv 若装了则 ok
    if importlib.util.find_spec("faster_whisper") is None:
        assert data["python_deps"][0]["found"] is False
        assert data["python_deps"][0]["status"] == "MISSING_DEP"
        assert data["status"] == "MISSING_DEP"
    else:
        assert data["status"] == "ok"

    status, data = _get(live_server, "/api/plugins/local-file/check")
    assert status == 200 and data["status"] in ("ok", "MISSING_DEP")
    assert data["python_deps"] == []  # local-file 无 PYTHON_DEPS 声明

    status, data = _get(live_server, "/api/plugins/nope/check")
    assert status == 404


# ── ④ 复核修复回归（AI 对齐 / 语音空洞 / 编辑版接线定向 / 保留回收）──


def test_e2e_ai_path_ranges_align_to_segment_grid(
    small_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核判例全链复现：AI 返回自造数值（start=1.37/end=7.9，不等于任何
    segment.start）→ key-segment 收敛到段落网格 → 三级「一路追到底」在
    AI 路径同样成立（修复前该输入产出 range.start=1.37，第三级断链）。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

    def fake_chat_policed(messages, **kwargs):
        assert kwargs["task"] == "plugin_key_segment"
        return {"text": json.dumps(
            [{"start": 1.37, "end": 7.9, "label": "自造数值", "confidence": 0.8,
              "text_excerpt": "首先讲重点"}], ensure_ascii=False)}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)
    _install_fake_fw(monkeypatch)  # tr 节点仍需 faster_whisper 假件（真缺席会先失败）
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(small_video)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
        ],
        run_root=tmp_path / "runs", run_id="pl-e2e-aisnap",
    )
    assert result["status"] == "done", json.dumps(result, ensure_ascii=False)
    run_dir = Path(result["run_dir"])
    segments = _read_outputs(run_dir, "tr")["transcript"]
    ranges = _read_outputs(run_dir, "ks")["ranges"]
    images = _read_outputs(run_dir, "fg")["images"]

    assert len(ranges) == 1
    assert ranges[0]["start"] == 0.0   # 1.37 → 覆盖段（0.0-1.5）的 start
    assert ranges[0]["end"] == 5.8     # 7.9 → 收敛到末段 end
    assert ranges[0]["start"] in {s["start"] for s in segments}
    assert ranges[0]["segment_windows"]  # 窗口随附（抽帧锚数据面）
    assert images and abs(images[0]["ts"] - 0.3) < 1e-6  # 基线 0.3 已在首段语音内


def test_e2e_rule_path_ts_avoids_speech_hole(
    small_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核判例全链复现：转写 [0.0-0.1「嗯」]+[1.1-6.0]（停顿 1.0s<1.2 不切
    组）→ 规则路径单 range(0.0,6.0)，修复前 ts=0.3 落 (0.1,1.1) 语音空洞
    （锚点指向静音）；修复后抽帧时刻后移到首个覆盖它的语音窗起点 1.1。"""
    _install_fake_fw(monkeypatch)
    model_dir = tmp_path / "whisper-model-hole"
    model_dir.mkdir()  # 名含 "hole" → 假件返回空洞段落
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(small_video)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
        ],
        run_root=tmp_path / "runs", run_id="pl-e2e-hole",
    )
    assert result["status"] == "done", json.dumps(result, ensure_ascii=False)
    run_dir = Path(result["run_dir"])
    segments = _read_outputs(run_dir, "tr")["transcript"]
    ranges = _read_outputs(run_dir, "ks")["ranges"]
    images = _read_outputs(run_dir, "fg")["images"]

    assert len(ranges) == 1 and ranges[0]["start"] == 0.0 and ranges[0]["end"] == 6.0
    assert ranges[0]["segment_windows"] == [[0.0, 0.1], [1.1, 6.0]]  # JSON 往返后 list 形
    assert images, "images 为空"
    ts = images[0]["ts"]
    assert abs(ts - 1.1) < 1e-6, f"ts={ts} 未避开语音空洞（应后移到语音窗起点 1.1）"
    assert any(
        float(s["start"]) <= ts <= float(s["end"]) for s in segments
    ), "ts 仍不在任何 segment 时间窗内"


def test_e2e_edited_file_scoped_to_wired_upstream(
    tmp_path: Path,
) -> None:
    """复核判例直锁（双转写节点）：ks 接线吃 tr_b 的转写，tr_a 目录里的
    segments.edited.json **不得串味**——修复前 mtime 全局扫描会把 A 的编辑
    套到 B 的时间轴上且零报错。

    用真实引擎 ctx（psp._PipelineContext + 真实 Workspace）与引擎同款
    importlib 加载路径调插件入口——run_pipeline 的 exist_ok=False 纪律
    （run 目录恒新造）不允许预植文件，ctx 级是与生产行为同保真度的植入点。
    """
    import importlib.util as _ilu

    from musespiral.services.pipeline_service_plugins import Workspace, _PipelineContext

    spec = _ilu.spec_from_file_location(
        "e2e_ks_plugin", BUILTIN_DIR / "key-segment" / "plugin.py")
    assert spec is not None and spec.loader is not None
    ks_run = _ilu.module_from_spec(spec)
    spec.loader.exec_module(ks_run)

    segs_a_edited = [
        {"start": 0.0, "end": 1.0, "text": " A-编辑后文本 ", "avg_logprob": -0.2, "no_speech_prob": 0.02},
        {"start": 3.0, "end": 4.5, "text": " A-编辑后第二段 ", "avg_logprob": -0.2, "no_speech_prob": 0.02},
    ]
    segs_b_raw = [
        {"start": 0.5, "end": 2.0, "text": " B-原始转写 ", "avg_logprob": -0.25, "no_speech_prob": 0.02},
        {"start": 5.0, "end": 6.0, "text": " B-原始后段 ", "avg_logprob": -0.29, "no_speech_prob": 0.03},
    ]
    segs_b_edited = [
        {"start": 1.0, "end": 2.5, "text": " B-编辑后文本 ", "avg_logprob": -0.2, "no_speech_prob": 0.02},
        {"start": 4.0, "end": 5.5, "text": " B-编辑后第二段 ", "avg_logprob": -0.2, "no_speech_prob": 0.02},
    ]

    def _run_ks(upstream: dict[str, str], plant: dict[str, list] ) -> tuple[list, list[str]]:
        run_dir = tmp_path / f"ws-{abs(hash(tuple(upstream.items())))}"
        for node, segs in plant.items():
            d = run_dir / "workspace" / node
            d.mkdir(parents=True, exist_ok=True)
            (d / "segments.edited.json").write_text(
                json.dumps({"segments": segs}, ensure_ascii=False), encoding="utf-8")
        ws = Workspace(run_dir)
        logs: list[str] = []
        ctx = _PipelineContext(
            node_id="ks", run_dir=run_dir, workspace=ws, cancelled=None,
            ai_client=None, uses_ai=False, on_log=lambda n, m: logs.append(m),
            upstream=upstream,
        )
        out = ks_run.run({"transcript": segs_b_raw}, {}, ctx)
        return out["ranges"], logs

    # 用例一（串味复现）：接线=tr_b，编辑版只在 tr_a → 必须**不读**，吃 B 原始
    ranges, logs = _run_ks({"transcript": "tr_b"}, {"tr_a": segs_a_edited})
    assert ranges
    assert all(r["start"] in {0.5, 5.0} for r in ranges), (
        f"tr_a 的编辑版串味进了 tr_b 时间轴: {[(r['start'], r['label']) for r in ranges]}"
    )
    assert not any("编辑版" in m for m in logs)

    # 用例二（正路）：编辑版在接线上游 tr_b 目录 → 生效，吃编辑后时间轴
    ranges, logs = _run_ks({"transcript": "tr_b"}, {"tr_b": segs_b_edited})
    assert ranges and all(r["start"] in {1.0, 4.0} for r in ranges)
    assert any("tr_b/segments.edited.json" in m for m in logs)

    # 用例三（无接线信息宿主）：ctx.upstream 空面 → 全局 mtime 扫描兜底（老行为）
    ranges, logs = _run_ks({}, {"tr_a": segs_a_edited})
    assert ranges and all(r["start"] in {0.0, 3.0} for r in ranges)  # 兜底读到 tr_a 编辑版
    assert any("编辑版" in m for m in logs)


def test_prune_runs_keeps_latest_n(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """保留回收：LRU 按 meta finished 保留最近 keep 个；坏目录按 mtime 参与
    （防永久留垃圾）；空根零副作用。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home"))  # 与模块沙箱同形
    runs = tmp_path / "home" / "pipeline_runs"
    runs.mkdir(parents=True)
    now = time.time()
    _iso = lambda days_ago: time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(now - days_ago * 86400))
    for rid, finished, age in [
        ("pl-old-1", _iso(3.0), 3.0),
        ("pl-new-1", _iso(2.0), 2.0),
        ("pl-new-2", _iso(1.0), 1.0),
    ]:
        d = runs / rid
        d.mkdir()
        (d / "meta.json").write_text(
            json.dumps({"run_id": rid, "finished": finished, "status": "done"}),
            encoding="utf-8")
    broken = runs / "pl-broken"  # meta 读不出 → mtime 参与
    broken.mkdir()
    old = time.time() - 400 * 86400  # 远老于全部 meta finished
    os.utime(broken, (old, old))

    out = psp.prune_runs(keep=2)
    assert out == {"removed": 2, "kept": 2}
    survivors = {p.name for p in runs.iterdir()}
    assert survivors == {"pl-new-1", "pl-new-2"}  # 最旧 + 坏目录被回收

    assert psp.prune_runs(keep=5) == {"removed": 0, "kept": 2}  # 幂等
    empty = tmp_path / "home2" / "pipeline_runs"  # 根不存在 → 零副作用
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "home2"))
    assert psp.prune_runs() == {"removed": 0, "kept": 0}
    assert not empty.exists()


def test_plrun_cli_list_and_prune(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 面：plrun list 读面 + plrun prune --yes 真回收（MUSESPIRAL_HOME 沙箱）。"""
    from click.testing import CliRunner

    from musespiral.cli import cli

    home = tmp_path / "home"
    runs = home / "pipeline_runs"
    runs.mkdir(parents=True)
    for i in range(3):
        d = runs / f"pl-r{i}"
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({
            "run_id": f"pl-r{i}", "nodes": ["src", "fg"],
            "status": "done", "finished": f"2026-11-{i + 1:02d}T10:00:00",
        }), encoding="utf-8")
    monkeypatch.setenv("MUSESPIRAL_HOME", str(home))
    runner = CliRunner()

    listed = runner.invoke(cli, ["plrun", "list"])
    assert listed.exit_code == 0
    assert "pl-r0" in listed.output and "pl-r2" in listed.output

    pruned = runner.invoke(cli, ["plrun", "prune", "--keep", "1", "--yes"])
    assert pruned.exit_code == 0
    assert "已回收 2 个" in pruned.output
    assert {p.name for p in runs.iterdir()} == {"pl-r2"}


# ── ③ check 面 MISSING_DEP（机器无关红绿）─────────────────────────


def test_check_python_deps_missing_reports_missing_dep(tmp_path: Path) -> None:
    """venv 里真缺席的包名 → status=MISSING_DEP、found=False、不抛不崩。"""
    deps = loader.check_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, tmp_path
    )
    assert deps == []  # 无声明 → 空清单（非 MISSING_DEP）

    plugin_dir = tmp_path / "p"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.py").write_text(
        'PYTHON_DEPS = ["musespiral_e2e_definitely_absent_pkg"]\n'
        "def run(inputs, config, ctx):\n"
        "    return {}\n",
        encoding="utf-8",
    )
    out = loader.check_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, plugin_dir
    )
    assert out == [{"cmd": "musespiral_e2e_definitely_absent_pkg", "found": False,
                    "path": None, "status": "MISSING_DEP"}]


def test_check_python_deps_found_via_sys_modules(tmp_path: Path) -> None:
    """sys.modules 注入即 found（find_spec 语义；不真装包也能验绿态）。
    注：find_spec 读的是 module.__spec__——裸 ModuleType 须补 __spec__。"""
    spec = importlib.machinery.ModuleSpec("musespiral_e2e_fake_pkg", None)
    pkg = types.ModuleType("musespiral_e2e_fake_pkg")
    pkg.__spec__ = spec  # type: ignore[attr-defined]
    monkey_target = sys.modules
    monkey_target["musespiral_e2e_fake_pkg"] = pkg
    try:
        plugin_dir = tmp_path / "p"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text(
            'PYTHON_DEPS = ["musespiral_e2e_fake_pkg"]\n', encoding="utf-8"
        )
        out = loader.check_python_deps(
            {"runtime": {"type": "python", "entry": "plugin.py:run"}}, plugin_dir
        )
        assert out[0]["found"] is True and out[0]["status"] == "ok"
        assert out[0]["path"] == "<namespace>"
    finally:
        monkey_target.pop("musespiral_e2e_fake_pkg", None)


def test_check_python_deps_never_crashes_on_dirty_module(tmp_path: Path) -> None:
    """语法坏/声明形态不符/非 python runtime → []，探测面零崩溃。"""
    d1 = tmp_path / "broken"
    d1.mkdir()
    (d1 / "plugin.py").write_text("def run(:\n", encoding="utf-8")  # 语法坏
    assert loader.check_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, d1) == []

    d2 = tmp_path / "weird"
    d2.mkdir()
    (d2 / "plugin.py").write_text(
        "PYTHON_DEPS = 'faster_whisper'\n"  # 非 list 形态 → 不认
        "PYTHON_DEPS = [42, None]\n",       # 元素非 str → 不认
        encoding="utf-8",
    )
    assert loader.check_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, d2) == []

    assert loader.check_python_deps(
        {"runtime": {"type": "exec", "entry": "run.sh"}}, tmp_path) == []


def test_builtin_transcribe_declares_faster_whisper() -> None:
    """内置 transcribe 的 PYTHON_DEPS 声明在场（check 面数据源；ast 静态读）。"""
    assert loader.declared_python_deps(
        {"runtime": {"type": "python", "entry": "plugin.py:run"}}, BUILTIN_DIR / "transcribe"
    ) == ["faster_whisper"]


def test_check_plugin_aggregates_missing_dep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """check_plugin 聚合面（红态钉死：python 缺席 + 命令缺席 → MISSING_DEP；
    绿态钉死：双面全 found → ok）——机器无关。"""
    monkeypatch.setattr(
        loader, "check_python_deps",
        lambda plugin, d: [{"cmd": "faster_whisper", "found": False, "path": None,
                            "status": "MISSING_DEP"}],
    )
    red = svc.check_plugin("transcribe")
    assert red is not None and red["status"] == "MISSING_DEP"
    assert red["python_deps"][0]["status"] == "MISSING_DEP"

    monkeypatch.setattr(loader, "check_deps", lambda plugin: [
        {"cmd": "ffmpeg", "found": True, "path": "/usr/bin/ffmpeg"}])
    monkeypatch.setattr(
        loader, "check_python_deps",
        lambda plugin, d: [{"cmd": "faster_whisper", "found": True, "path": "/x/fw",
                            "status": "ok"}],
    )
    green = svc.check_plugin("transcribe")
    assert green is not None and green["status"] == "ok"
