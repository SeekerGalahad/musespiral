"""官方内置插件测试（首插件族 local-file / transcribe，2026-11-20 批）。

覆盖（本批测试硬要求 24/25）：
  a) 内置发现：builtin_plugins_dir 指向包内、root=None 生产路径合并内置区
     （显式 root 不混入）、用户区同 id 覆盖内置、内置 manifest 全过三道门、
     local-file→transcribe 静态契约与引擎两节点链（全 mock 零外部依赖）；
  b) local-file：后缀白名单红绿（大小写不敏感）/ 存在·常规·可读三道门 /
     ffprobe 时长探测双态（就位→秒数，缺席→None 留空不失败）；
  c) transcribe：缺依赖可读错误（edge-tts 纪律）/ mock faster_whisper 全流程
     ——**每段必带 avg_logprob 与 no_speech_prob（G-1 数据面）**、
     vad_filter=True 与 condition_on_previous_text=False 传给模型（防幻听/
     防复读）、ffmpeg 抽轨 argv 16kHz mono、模型未就位可读指引（不自动
     下载权重）、取消自查点、VAD 过滤后零段非失败。

插件模块按引擎同款 importlib 按路径加载（目录名带连字符不可直接 import，
与 psp._load_python_entry 同一保真度）；全部 subprocess/模型 mock，
不真跑 ffmpeg、不真跑模型、零网络。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import pytest

from musespiral.core.plugins.context import PluginCancelled, PluginContext, PluginError
from musespiral.core.plugins.manifest import parse_manifest, validate_pipeline_contract
from musespiral.infra import plugins_loader as loader
from musespiral.services import pipeline_service_plugins as psp
from musespiral.services import plugins_service as svc

BUILTIN_DIR = loader.builtin_plugins_dir()


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUSESPIRAL_HOME 沙箱（root=None 生产路径的用户区不触真实 ~/.musespiral）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


# ── 工具：按路径加载插件模块（与引擎 _load_python_entry 同款）──────


def _load_module(dirname: str) -> ModuleType:
    d = BUILTIN_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        f"test_builtin_{dirname.replace('-', '_')}", d / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_of(dirname: str) -> dict[str, Any]:
    return json.loads((BUILTIN_DIR / dirname / "manifest.json").read_text(encoding="utf-8"))


class _FakeCtx(PluginContext):
    """最小 PluginContext（测试面）：logs 收集、tmpdir/artifacts 指向 tmp。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def progress(self, done: int, total: int) -> None:
        self.logs.append(f"progress {done}/{total}")

    def tmpdir(self) -> Path:
        d = self._root / "tmp"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cancelled(self) -> bool:
        return False

    @property
    def artifacts_dir(self) -> Path:
        d = self._root / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def ai(self) -> Any:
        raise PluginError("测试上下文不注入 AI 客户端")


# ── a) 内置发现与契约 ──────────────────────────────────────────────


def test_builtin_dir_points_into_package() -> None:
    assert BUILTIN_DIR.name == "plugins_builtin"
    assert BUILTIN_DIR.parent.name == "musespiral"
    assert (BUILTIN_DIR / "local-file" / "manifest.json").is_file()
    assert (BUILTIN_DIR / "transcribe" / "manifest.json").is_file()


def test_builtin_discovered_in_production_path(tmp_path: Path) -> None:
    """root=None 生产路径：内置区自动可见（免安装），全过三道门。"""
    out = svc.list_plugins()  # 用户区不存在（沙箱 home）→ 只有内置区
    ids = {p["manifest"]["id"] for p in out["plugins"]}
    assert {"local-file", "transcribe"} <= ids  # 子集断言：容忍同批新增官方插件
    assert all("plugins_builtin" in p["dir"] for p in out["plugins"])
    assert out["skipped"] == []


def test_explicit_root_does_not_mix_builtin(tmp_path: Path) -> None:
    empty = tmp_path / "empty-root"
    empty.mkdir()
    assert svc.list_plugins(empty) == {"plugins": [], "skipped": []}


def test_user_plugin_overrides_builtin_same_id(tmp_path: Path) -> None:
    """用户区同 id 覆盖内置（换掉官方实现的逃生门），清单不重复。"""
    user = tmp_path / "musespiral-home" / "plugins" / "local-file"
    user.mkdir(parents=True)
    manifest = _manifest_of("local-file") | {"version": "9.9.9"}
    (user / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (user / "plugin.py").write_text("def run(inputs, config, ctx):\n    return {}\n", encoding="utf-8")

    out = svc.list_plugins()  # root=None → 内置 + 用户合并
    lfs = [p for p in out["plugins"] if p["manifest"]["id"] == "local-file"]
    assert len(lfs) == 1
    assert lfs[0]["manifest"]["version"] == "9.9.9"
    assert "plugins_builtin" not in lfs[0]["dir"]


def test_builtin_manifests_parse_green() -> None:
    for dirname in ("local-file", "transcribe"):
        plugin, errors = parse_manifest(_manifest_of(dirname))
        assert errors == [] and plugin is not None, dirname


def test_local_file_manifest_contract() -> None:
    m = _manifest_of("local-file")
    assert m["kind"] == "source"
    assert m["inputs"]["path"]["type"] == "file/path" and m["inputs"]["path"]["required"] is True
    assert m["inputs"]["media_kind"]["required"] is False
    assert {k: v["type"] for k, v in m["outputs"].items()} == {
        "file": "file/path", "size": "number", "duration_s": "number",
    }


def test_transcribe_manifest_contract() -> None:
    m = _manifest_of("transcribe")
    assert m["kind"] == "process"
    assert m["inputs"]["media_path"]["type"] == "file/path"
    assert m["inputs"]["language"]["default"] == "zh"
    assert m["inputs"]["hotwords"]["type"] == "string[]"
    assert m["config_schema"]["model"]["default"] == "large-v3-turbo"
    assert m["config_schema"]["vad_filter"]["default"] is True
    assert "ffmpeg" in m["requires"]
    assert m.get("uses_ai") is not True  # 本插件不调 AI（key-segment 才声明）


def test_first_two_nodes_static_contract() -> None:
    """正典 §9.1 首两节点链（local-file→transcribe）静态契约绿；反序红。"""
    lf, tr = _manifest_of("local-file"), _manifest_of("transcribe")
    assert validate_pipeline_contract([{"id": "src", "plugin": lf},
                                       {"id": "tr", "plugin": tr}]) == []
    errs = validate_pipeline_contract([{"id": "tr", "plugin": tr},
                                       {"id": "src", "plugin": lf}])
    assert any("只能位于流水线开头" in e for e in errs)


# ── b) local-file：白名单 / 校验门 / 时长双态 ──────────────────────


@pytest.fixture
def lf_mod() -> ModuleType:
    return _load_module("local-file")


def test_local_file_suffix_whitelist_red_green(
    lf_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lf_mod, "_probe_duration", lambda media: 1.5)  # 隔离 ffprobe
    ctx = _FakeCtx(tmp_path / "ctx")
    ok = tmp_path / "clip.MOV"  # 大小写不敏感
    ok.write_bytes(b"xx")
    out = lf_mod.run({"path": str(ok)}, {}, ctx)
    assert out["file"] == ok and out["size"] == 2 and out["duration_s"] == 1.5

    for name in ("notes.txt", "evil.exe", "nosuffix"):
        bad = tmp_path / name
        bad.write_bytes(b"x")
        with pytest.raises(PluginError) as exc_info:
            lf_mod.run({"path": str(bad)}, {}, ctx)
        assert "白名单" in str(exc_info.value), name


def test_local_file_gates_exist_regular_readable(
    lf_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lf_mod, "_probe_duration", lambda media: None)
    ctx = _FakeCtx(tmp_path / "ctx")
    with pytest.raises(PluginError, match="缺少输入 path"):
        lf_mod.run({}, {}, ctx)
    with pytest.raises(PluginError, match="文件不存在"):
        lf_mod.run({"path": str(tmp_path / "no-such.mp4")}, {}, ctx)
    with pytest.raises(PluginError, match="不是常规文件"):
        lf_mod.run({"path": str(tmp_path)}, {} , ctx)  # 目录
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    monkeypatch.setattr("os.access", lambda p, m: False)  # 模拟权限不足
    with pytest.raises(PluginError, match="不可读"):
        lf_mod.run({"path": str(media)}, {}, ctx)


def test_local_file_duration_probe_dual_state(
    lf_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """时长探测双态：ffprobe 就位→秒数；缺席（FileNotFoundError）→None 留空。"""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"ffprobe-target")
    calls: list[list[str]] = []

    def fake_run_found(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=b"12.500000\n", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run_found)
    out = lf_mod.run({"path": str(media)}, {}, _FakeCtx(tmp_path / "ctx"))
    assert out["duration_s"] == 12.5 and out["size"] == 14
    assert calls and calls[0][0] == "ffprobe"
    assert "-show_entries" in calls[0] and "format=duration" in calls[0]
    assert calls[0][-1] == str(media)

    def fake_run_absent(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("subprocess.run", fake_run_absent)
    out2 = lf_mod.run({"path": str(media)}, {}, _FakeCtx(tmp_path / "ctx2"))
    assert out2["duration_s"] is None  # 失败留空，不失败
    assert out2["size"] == 14


# ── c) transcribe：缺依赖 / mock 全流程 / G-1 信号 / 防幻听防复读 ──


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str,
                 avg_logprob: float, no_speech_prob: float) -> None:
        self.start, self.end, self.text = start, end, text
        self.avg_logprob, self.no_speech_prob = avg_logprob, no_speech_prob


class _FakeInfo:
    duration = 63.0
    language = "zh"
    language_probability = 0.98


class _FakeWhisperModel:
    """记录构造与 transcribe kwargs 的假模型（断言面，见各用例）。"""

    init_calls: ClassVar[list[tuple]] = []
    transcribe_calls: ClassVar[list[dict]] = []
    segments: ClassVar[list[_FakeSegment]] = []

    def __init__(self, model_ref: str, device: str = "cpu", compute_type: str = "int8") -> None:
        type(self).init_calls.append((model_ref, device, compute_type))

    def transcribe(self, audio_path: str, **kwargs: Any):
        type(self).transcribe_calls.append(kwargs)
        return iter(list(type(self).segments)), _FakeInfo()


@pytest.fixture
def tr_mod() -> ModuleType:
    return _load_module("transcribe")


@pytest.fixture(autouse=True)
def _reset_fake_model() -> None:
    _FakeWhisperModel.init_calls.clear()
    _FakeWhisperModel.transcribe_calls.clear()
    _FakeWhisperModel.segments.clear()


def _install_fake_fw(monkeypatch: pytest.MonkeyPatch, segments: list[_FakeSegment]) -> None:
    _FakeWhisperModel.segments.clear()
    _FakeWhisperModel.segments.extend(segments)
    fake = SimpleNamespace(WhisperModel=_FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)


def _mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"RIFFfake-wav")  # 产出空壳 wav 让流程继续
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


def test_transcribe_missing_dep_readable(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """缺依赖给可读安装引导（edge-tts 纪律），插件模块本身加载正常。"""
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import 即 ImportError
    with pytest.raises(tr_mod.TranscribeUnavailableError) as exc_info:
        tr_mod.run({"media_path": str(media)}, {}, _FakeCtx(tmp_path / "ctx"))
    assert "pip install faster-whisper" in str(exc_info.value)
    assert issubclass(tr_mod.TranscribeUnavailableError, PluginError)


def test_transcribe_mock_model_quality_signals(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-1 数据面：每段带 avg_logprob/no_speech_prob；防幻听/防复读旗标传参。"""
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake")
    model_dir = tmp_path / "whisper-model"
    model_dir.mkdir()  # 存在的本地目录 → 模型就位（探测直认）
    ff_calls = _mock_ffmpeg(monkeypatch)
    segs = [
        _FakeSegment(0.0, 2.5, " 你好世界 ", -0.21, 0.02),
        _FakeSegment(2.5, 5.1, "（杂音段）", -1.42, 0.73),  # 低置信 + 疑似非语音样本
    ]
    _install_fake_fw(monkeypatch, segs)
    ctx = _FakeCtx(tmp_path / "ctx")

    out = tr_mod.run(
        {"media_path": str(media), "language": "zh", "hotwords": ["Doris", "Flink"]},
        {"model": str(model_dir), "vad_filter": True},
        ctx,
    )

    # G-1 数据面（硬要求 25）：segments 每项含质量信号，原样透传不丢弃
    assert len(out["transcript"]) == 2
    for src, got in zip(segs, out["transcript"]):
        assert {"start", "end", "text", "avg_logprob", "no_speech_prob"} <= set(got)
        assert got["avg_logprob"] == src.avg_logprob
        assert got["no_speech_prob"] == src.no_speech_prob
        assert got["text"] == src.text.strip()
        assert got["start"] == src.start and got["end"] == src.end
    assert out["duration_s"] == 63.0

    # 防幻听/防复读（硬要求 25）：两旗标原样传给模型
    kwargs = _FakeWhisperModel.transcribe_calls[-1]
    assert kwargs["vad_filter"] is True
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["language"] == "zh"
    assert "Doris" in kwargs["initial_prompt"] and "Flink" in kwargs["initial_prompt"]

    # 模型构造：cpu + int8（精度手册 §6 硬件参考），config.model 本地目录直传
    assert _FakeWhisperModel.init_calls[-1] == (str(model_dir), "cpu", "int8")

    # 抽轨 argv：ffmpeg 16kHz 单声道（手册 §2 万能抽轨）
    assert ff_calls and ff_calls[0][0] == "ffmpeg"
    assert "-ar" in ff_calls[0] and "16000" in ff_calls[0]
    assert "-ac" in ff_calls[0] and ff_calls[0][ff_calls[0].index("-ac") + 1] == "1"


def test_transcribe_defaults_vad_on_auto_language_no_hotwords(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config 省略 vad_filter → 默认 True；language=auto → 不传语言键。"""
    media = tmp_path / "v.mkv"
    media.write_bytes(b"x")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    _mock_ffmpeg(monkeypatch)
    _install_fake_fw(monkeypatch, [_FakeSegment(0.0, 1.0, "hi", -0.3, 0.1)])

    tr_mod.run(
        {"media_path": str(media), "language": "auto"},
        {"model": str(model_dir)},  # vad_filter 缺省
        _FakeCtx(tmp_path / "ctx"),
    )
    kwargs = _FakeWhisperModel.transcribe_calls[-1]
    assert kwargs["vad_filter"] is True  # 默认开（防幻听）
    assert "language" not in kwargs  # auto → 交引擎自检
    assert "initial_prompt" not in kwargs  # 无热词不设提示


def test_transcribe_model_not_local_readable_error(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型未就位 → 可读指引（不自动下载权重，硬要求 11 的测试面）。"""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"x")
    _install_fake_fw(monkeypatch, [])  # 依赖在（先过依赖门），模型不在
    monkeypatch.setattr(tr_mod, "_model_locally_available", lambda ref: False)
    with pytest.raises(PluginError) as exc_info:
        tr_mod.run({"media_path": str(media)}, {}, _FakeCtx(tmp_path / "ctx"))
    msg = str(exc_info.value)
    assert "不自动下载" in msg
    assert "hf-mirror" in msg  # 境内镜像指引
    assert "large-v3-turbo" in msg  # 报出所配模型名（config 缺省值）


def test_transcribe_cancelled_mid_stream(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "v.mp4"
    media.write_bytes(b"x")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    _mock_ffmpeg(monkeypatch)
    _install_fake_fw(monkeypatch, [_FakeSegment(0.0, 1.0, "a", -0.3, 0.1)])
    ctx = _FakeCtx(tmp_path / "ctx")
    monkeypatch.setattr(_FakeCtx, "cancelled", property(lambda self: True))

    with pytest.raises(PluginCancelled):
        tr_mod.run({"media_path": str(media)}, {"model": str(model_dir)}, ctx)


def test_transcribe_empty_after_vad_is_not_failure(
    tr_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VAD 过滤后零段是真实结果（纯音乐/静音）——不制造假失败。"""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"x")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    _mock_ffmpeg(monkeypatch)
    _install_fake_fw(monkeypatch, [])
    ctx = _FakeCtx(tmp_path / "ctx")
    out = tr_mod.run({"media_path": str(media)}, {"model": str(model_dir)}, ctx)
    assert out == {"transcript": [], "duration_s": 63.0}
    assert any("0 段" in line for line in ctx.logs)


# ── d) 引擎两节点链（真实内置 manifest × 全 mock 执行）────────────


def test_engine_two_node_builtin_chain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """正典 §9.1 首两节点经引擎全链（plan→gather→execute→workspace）：
    media_path 跨端口接线（local-file.file → transcribe.media_path）、
    段质量信号入 workspace。"""
    media = tmp_path / "v.mp4"
    media.write_bytes(b"x")
    model_dir = tmp_path / "m"
    model_dir.mkdir()
    _mock_ffmpeg(monkeypatch)
    _install_fake_fw(monkeypatch, [_FakeSegment(0.0, 2.0, " 深入浅出 ", -0.4, 0.05)])

    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
        ],
        plugins_root=BUILTIN_DIR,
        run_root=tmp_path / "runs",
        run_id="pl-builtin-0001",
    )
    assert result["status"] == "done", result
    assert [s["status"] for s in result["steps"]] == ["ok", "ok"]
    # workspace 读面走落盘真相（outputs.json；跨进程续读非 v1 能力，psp 诚实边界）
    outputs = json.loads(
        (Path(result["run_dir"]) / "workspace" / "tr" / "outputs.json").read_text(encoding="utf-8")
    )
    transcript = outputs["inline"]["transcript"]
    assert transcript[0]["avg_logprob"] == -0.4
    assert transcript[0]["no_speech_prob"] == 0.05
