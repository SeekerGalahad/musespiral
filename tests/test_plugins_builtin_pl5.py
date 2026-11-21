"""官方内置插件测试·PL-5 批（key-segment / frame-grab，2026-11-20）。

覆盖（本批测试硬要求 26/27 + 双路径/时间戳锚面）：
  a) 任务键接线：plugin_key_segment 已入 profiles.TASKS（24 键）、
     prompt_tasks() 派生面可见、ai.json routing 可按插件独立路由
     （一插件一键，不共用泛化 plugin 键的数据面证明）；
  b) key-segment AI 路径：mock chat_policed → **断言 task='plugin_key_segment'**
     且经治理层参数（temperature/cache）生效；非法/越界片段丢弃、超上限
     裁断、低置信标 ⚠️ 不丢弃；回复非 JSON / 治理层 AIError / 全非法 →
     **降级规则切片**（不报错卡死，日志留降级原因）；
  c) key-segment 规则路径：AI 未配置不调 AI；分组（停顿/标题词）→ 打分 →
     Top-N 截断；转写质量因子吃 G-1 信号（avg_logprob）；min_confidence
     双态（0.95 全标 ⚠️ / 0.1 全不标）；编辑版 segments.edited.json 优先读、
     损坏回退；空转写置空不失败；非法输入可读报错；
  d) frame-grab：frame 模式 argv 断言（**-ss = start+0.3 非原值**、
     -frames:v 1、-i 媒体）+ 产物落 artifacts_dir + ts/source_range 锚；
     短区间退守中点；clip 模式 -ss/-t/-c copy；ffmpeg 失败/缺席可读错误；
     编辑版 ranges.edited.json 优先；取消自查点；空 ranges 置空；
  e) 接力：key-segment（规则）输出直喂 frame-grab——images[i].ts 在
     ranges 与 segments 中三级可回溯（正典 §9.3 时间戳锚）；
  f) 引擎四节点链（local-file→transcribe→key-segment→frame-grab）：静态
     契约绿 + 全 mock 执行（ffprobe/ffmpeg/faster_whisper/chat_policed 全
     假件，零真模型零网络），media_path 跨两节点自动接线，产物入 workspace。

插件模块按引擎同款 importlib 按路径加载（与 psp._load_python_entry 同一
保真度）；AI 面 monkeypatch 在 gateway/profiles 模块对象上（插件调用点是
模块属性访问，对引擎加载的副本同样生效）。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from musespiral.core.plugins.context import PluginCancelled, PluginContext, PluginError
from musespiral.core.plugins.manifest import validate_pipeline_contract
from musespiral.infra import plugins_loader as loader
from musespiral.infra.ai import gateway as ai_gateway
from musespiral.infra.ai import profiles as ai_profiles
from musespiral.infra.ai.profiles import TASKS
from musespiral.infra.ai.prompts import prompt_tasks
from musespiral.services import pipeline_service_plugins as psp

BUILTIN_DIR = loader.builtin_plugins_dir()


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUSESPIRAL_HOME 沙箱：AI 未配置态可复现（不触真实 ~/.musespiral/ai.json）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


@pytest.fixture(autouse=True)
def _ai_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认钉死「AI 未配置」——AI 路径用例显式翻 True，互不渗漏。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: False)


def _load_module(dirname: str) -> ModuleType:
    d = BUILTIN_DIR / dirname
    spec = importlib.util.spec_from_file_location(
        f"test_pl5_{dirname.replace('-', '_')}", d / "plugin.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest_of(dirname: str) -> dict[str, Any]:
    return json.loads((BUILTIN_DIR / dirname / "manifest.json").read_text(encoding="utf-8"))


class _FakeCtx(PluginContext):
    """最小 PluginContext（测试面）；artifacts_dir 固定 root/artifacts，
    其父目录即 workspace 根——edited 文件落 root/<node>/ 下可被扫到。"""

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


@pytest.fixture
def ks_mod() -> ModuleType:
    return _load_module("key-segment")


@pytest.fixture
def fg_mod() -> ModuleType:
    return _load_module("frame-grab")


def _seg(start: float, end: float, text: str,
         avg_logprob: float = -0.2, no_speech_prob: float = 0.05) -> dict[str, Any]:
    """transcribe 输出形段落（含 G-1 质量信号）。"""
    return {"start": start, "end": end, "text": text,
            "avg_logprob": avg_logprob, "no_speech_prob": no_speech_prob}


def _three_group_segments() -> list[dict[str, Any]]:
    """三个话题组（组内无停顿、组间停顿 ≥1.2s）：
    A(0-10) 首段+标题词 / B(12-24) 无标题词、文本稀 / C(25.5-36) 标题词×2。
    规则打分期望 C > A > B（标题词命中差异是主因子）。"""
    return [
        _seg(0.0, 5.0, "首先讲重点：Doris 的列式存储原理"),
        _seg(5.0, 10.0, "数据先写入内存 tablet 再异步刷盘"),
        _seg(12.0, 18.0, "呃那个我们看一下这个"),
        _seg(18.0, 24.0, "对吧就这样子吧嗯"),
        _seg(25.5, 30.0, "第二个关键点是查询路由"),
        _seg(30.0, 36.0, "Frontend 负责解析 SQL 生成执行计划"),
    ]


# ── a) 任务键接线（阻塞点数据面）──────────────────────────────────


def test_plugin_key_segment_task_registered() -> None:
    assert "plugin_key_segment" in TASKS
    assert "plugin_key_segment" in prompt_tasks()  # prompts.d 覆盖级联面派生


def test_routing_per_plugin_key(tmp_path: Path) -> None:
    """ai.json routing 可把 plugin_key_segment 独立路由（一插件一键不共用）。"""
    home = tmp_path / "musespiral-home"
    (home).mkdir(parents=True)
    (home / "ai.json").write_text(json.dumps({
        "default": "cloud",
        "profiles": {"cloud": {"base_url": "https://a", "api_key": "k", "model": "m"},
                     "local": {"base_url": "http://b", "api_key": "", "model": "n"}},
        "routing": {"plugin_key_segment": "local"},
    }), encoding="utf-8")
    import os

    old = os.environ.get("MUSESPIRAL_HOME")
    os.environ["MUSESPIRAL_HOME"] = str(home)
    try:
        assert ai_profiles.resolve_profile_name("plugin_key_segment") == "local"
        assert ai_profiles.resolve_profile_name("rewrite") == "cloud"  # 其他任务不受影响
    finally:
        if old is None:
            os.environ.pop("MUSESPIRAL_HOME", None)
        else:
            os.environ["MUSESPIRAL_HOME"] = old


# ── b) key-segment：manifest / AI 路径 / 降级 ─────────────────────


def test_key_segment_manifest_contract() -> None:
    m = _manifest_of("key-segment")
    assert m["kind"] == "process"
    assert m["inputs"]["transcript"]["type"] == "segments"
    assert m["outputs"]["ranges"]["type"] == "ranges"
    assert m["config_schema"]["max_segments"]["default"] == 8
    assert m["config_schema"]["min_confidence"]["default"] == 0.4
    assert m["uses_ai"] is True  # 声明面（前端/平台可见此插件会调 AI）


def test_key_segment_ai_path_happy(
    ks_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 路径：经治理层一次调用；task 键钉死；非法项丢弃；**数值收敛到段落
    时间戳网格**（提示词硬性要求的执行面——AI 自造数值被 snap 回段落边界，
    「range.start 精确等于某 segment.start」的三级回溯不变量因此恒成立，
    2026-11-20 复核判例）；超限裁断、⚠️ 标注。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    calls: list[dict[str, Any]] = []

    def fake_chat_policed(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        calls.append({"messages": messages, **kwargs})
        return {"text": json.dumps([
            {"start": 0.0, "end": 5.0, "label": "存储原理", "confidence": 0.9,
             "text_excerpt": "列式存储原理"},
            {"start": 10.0, "end": 20.0, "label": "路由", "confidence": 0.2},
            {"start": 30.0, "end": 20.0, "label": "倒挂", "confidence": 0.9},   # start≥end → 弃
            # AI 自造越界数值（9999 > 素材末段）→ **收敛**而非丢弃：start 落首段
            # 边界 0.0、end 收敛到末段 end=36.0
            {"start": 0.0, "end": 9999.0, "label": "越界", "confidence": 0.9},
            {"start": 25.5, "end": 36.0, "label": "总结", "confidence": 0.95},
            {"label": "缺 start", "confidence": 0.9},                            # 缺字段 → 弃
        ], ensure_ascii=False), "cache_hit": False, "chars_in": 1, "chars_out": 1}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ks_mod.run({"transcript": _three_group_segments()},
                     {"max_segments": 3, "min_confidence": 0.5}, ctx)

    # 治理面（硬要求 26）：一次调用、任务键、切片稳态参数、缓存 opt-in
    assert len(calls) == 1
    assert calls[0]["task"] == "plugin_key_segment"
    assert calls[0]["temperature"] == 0.2
    assert calls[0]["cache"] is True
    user_text = calls[0]["messages"][-1]["content"]
    assert "至多选 3 个" in user_text  # 上限进了提示词
    assert "0|0.00-5.00|" in user_text  # 段落序号|时间戳|文本 形态

    seg_starts = {s["start"] for s in _three_group_segments()}
    ranges = out["ranges"]
    assert len(ranges) == 3  # 4 个合法项按 AI 价值序裁断为 3（超 max_segments，「总结」被裁）
    assert [r["start"] for r in ranges] == [0.0, 0.0, 5.0]  # 按时间重排
    # 收敛不变量（复核修 1）：每个 range.start 精确等于某 segment.start
    assert all(r["start"] in seg_starts for r in ranges)
    snapped = {r["label"].replace("⚠️ ", ""): r for r in ranges}
    assert snapped["越界"]["start"] == 0.0 and snapped["越界"]["end"] == 36.0  # 9999→末段 end
    assert snapped["路由"]["start"] == 5.0 and snapped["路由"]["end"] == 24.0  # 10/20→覆盖段边界
    for r in ranges:
        assert r["segment_windows"]  # 每项携带覆盖段落时间窗（frame-grab 锚数据面）
    # ⚠️ 标注（提示不拦截）：0.2 < 0.5 标注、0.9 不标
    assert snapped["路由"]["label"].startswith("⚠️")
    assert snapped["路由"]["confidence"] == 0.2  # 不丢弃，原值保留
    assert not snapped["存储原理"]["label"].startswith("⚠️")
    # 摘录缺失（路由项）→ 从覆盖区间段落原文回填，不虚构
    assert snapped["路由"]["text_excerpt"]
    assert any("AI 切片" in line for line in ctx.logs)


def test_key_segment_ai_offgrid_values_snap_to_segment_grid(
    ks_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """复核判例直锁：AI 自造非网格数值（1.37/7.9，段落仅 0.0-1.5 与 4.2-5.8
    的网格）→ snap 回覆盖段边界，range.start 回到段落网格——第三级「一路
    追到底」不再被 AI 数值打断。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    monkeypatch.setattr(ai_gateway, "chat_policed", lambda *a, **kw: {"text": json.dumps(
        [{"start": 1.37, "end": 7.9, "label": "自造", "confidence": 0.8}]
    )})
    segs = [_seg(0.0, 1.5, "首先讲重点"), _seg(1.5, 3.0, "继续展开"),
            _seg(4.2, 5.8, "总结收尾")]
    out = ks_mod.run({"transcript": segs}, {}, _FakeCtx(tmp_path / "ctx"))["ranges"]
    assert len(out) == 1
    assert out[0]["start"] == 0.0   # 1.37 → 覆盖段（0.0-1.5）的 start
    assert out[0]["end"] == 5.8     # 7.9 → ≥7.9 的最早段 end 不存在 → 收敛到末段 end
    assert out[0]["start"] in {s["start"] for s in segs}


def test_key_segment_ai_response_marks_low_quality_segments(
    ks_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G-1 信号进提示词：低置信/疑似非语音段落带标记，AI 据此避雷。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    seen: dict[str, Any] = {}

    def fake(messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
        seen["user"] = messages[-1]["content"]
        return {"text": json.dumps(
            [{"start": 0.0, "end": 2.0, "label": "a", "confidence": 0.9, "text_excerpt": "x"}]
        )}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    ks_mod.run({"transcript": [
        _seg(0.0, 2.0, "正常段落"),
        _seg(2.0, 4.0, "含糊段", avg_logprob=-1.4),
        _seg(4.0, 6.0, "杂音段", no_speech_prob=0.8),
    ]}, {}, _FakeCtx(tmp_path / "ctx"))
    assert "[低置信]" in seen["user"] and "[疑似非语音]" in seen["user"]


@pytest.mark.parametrize("ai_text,ai_exc", [
    ("抱歉，我不会输出 JSON", None),                       # 回复非 JSON
    (json.dumps([{"start": 30.0, "end": 20.0}]), None),    # 全非法 → 零有效片段
    (None, ai_profiles.AIError("调用 AI 失败: 网络超时")),  # 治理层 AIError
])
def test_key_segment_ai_failure_degrades_to_rules(
    ks_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ai_text: str | None, ai_exc: Exception | None,
) -> None:
    """双路径底线：AI 已配置但调用/解析失败 → 降级规则切片，绝不报错卡死。"""
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)

    def fake(messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
        if ai_exc is not None:
            raise ai_exc
        return {"text": ai_text}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ks_mod.run({"transcript": _three_group_segments()}, {}, ctx)
    assert out["ranges"], "降级路径必须仍产出可用结果"
    assert any("降级规则切片" in line for line in ctx.logs)  # 降级原因留痕
    for r in out["ranges"]:
        # 五要素在场（segment_windows 是复核修后新增的结构键，一并锁定）
        assert {"start", "end", "label", "confidence", "text_excerpt",
                "segment_windows"} <= set(r)


# ── c) key-segment：规则路径 / 编辑版 / 输入校验 ──────────────────


def test_key_segment_rules_path_unconfigured_no_ai_call(
    ks_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AI 未配置 → 不发起任何治理层调用（_ai_not_configured 钉死 False）。"""
    calls: list[Any] = []
    monkeypatch.setattr(ai_gateway, "chat_policed",
                        lambda *a, **kw: calls.append(1) or {})
    ctx = _FakeCtx(tmp_path / "ctx")
    out = ks_mod.run({"transcript": _three_group_segments()}, {}, ctx)
    assert calls == []  # 未配置零调用
    assert any("AI 未配置" in line for line in ctx.logs)
    assert len(out["ranges"]) == 3  # 三个话题组全中（max 缺省 8）
    assert [r["start"] for r in out["ranges"]] == [0.0, 12.0, 25.5]  # 按时间排


def test_key_segment_rules_scoring_and_top_n(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    """规则打分：标题词组（C）> 无标题词组（B）；max_segments=1 只留最高分。"""
    out = ks_mod.run({"transcript": _three_group_segments()}, {"max_segments": 1},
                     _FakeCtx(tmp_path / "ctx"))
    picked = out["ranges"]
    assert len(picked) == 1
    assert picked[0]["start"] == 25.5  # C 组（标题词×2）胜出
    assert "第二" in picked[0]["label"]  # 标签取标题词段落文本
    assert 0.0 <= picked[0]["confidence"] <= 1.0
    assert picked[0]["text_excerpt"]  # 摘录非空


def test_key_segment_rules_confidence_marks_both_states(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    """min_confidence 双态：0.95 → 全标 ⚠️；0.1 → 全不标（提示不拦截）。"""
    segs = _three_group_segments()
    strict = ks_mod.run({"transcript": segs}, {"min_confidence": 0.95},
                        _FakeCtx(tmp_path / "ctx1"))["ranges"]
    loose = ks_mod.run({"transcript": segs}, {"min_confidence": 0.1},
                       _FakeCtx(tmp_path / "ctx2"))["ranges"]
    assert strict and all(r["label"].startswith("⚠️") for r in strict)
    assert loose and all(not r["label"].startswith("⚠️") for r in loose)


def test_key_segment_rules_quality_factor_uses_g1_signals(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    """质量因子吃 transcribe G-1 信号：同文本两组，坏信号组置信度更低。"""
    def _pair(logprob: float) -> list[dict[str, Any]]:
        return [_seg(0.0, 5.0, "同一段文本内容完全一样", avg_logprob=logprob),
                _seg(7.0, 12.0, "同一段文本内容完全一样", avg_logprob=logprob)]

    good = ks_mod.run({"transcript": _pair(-0.2)}, {}, _FakeCtx(tmp_path / "g"))["ranges"]
    bad = ks_mod.run({"transcript": _pair(-1.4)}, {}, _FakeCtx(tmp_path / "b"))["ranges"]
    assert len(good) == len(bad) == 2
    assert bad[0]["confidence"] < good[0]["confidence"]  # 质量因子确实生效


def test_key_segment_prefers_edited_segments(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    """编辑版优先（评审 G-2）：segments.edited.json 存在即覆盖原始输入。"""
    root = tmp_path / "ctx"
    _FakeCtx(root).artifacts_dir.mkdir(parents=True, exist_ok=True)  # 建 workspace 形
    edited_dir = root / "tr-node"
    edited_dir.mkdir()
    (edited_dir / "segments.edited.json").write_text(json.dumps([
        _seg(0.0, 6.0, "编辑后独有文本：修正过的内容")
    ], ensure_ascii=False), encoding="utf-8")

    ctx = _FakeCtx(root)
    out = ks_mod.run({"transcript": _three_group_segments()}, {}, ctx)
    excerpts = " ".join(r["text_excerpt"] for r in out["ranges"])
    assert "编辑后独有文本" in excerpts  # 吃的是编辑版
    assert "首先讲重点" not in excerpts  # 原始版被替换（非叠加）
    assert any("编辑版转写" in line for line in ctx.logs)


def test_key_segment_edited_corrupt_falls_back(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    """编辑版损坏 → 回退原始输入（编辑是增强不是依赖）。"""
    root = tmp_path / "ctx"
    _FakeCtx(root).artifacts_dir.mkdir(parents=True, exist_ok=True)
    (root / "tr-node").mkdir()
    (root / "tr-node" / "segments.edited.json").write_text("{broken json", encoding="utf-8")
    ctx = _FakeCtx(root)
    out = ks_mod.run({"transcript": _three_group_segments()}, {}, ctx)
    excerpts = " ".join(r["text_excerpt"] for r in out["ranges"])
    assert "首先讲重点" in excerpts  # 原始输入兜底生效
    assert any("回退原始输入" in line for line in ctx.logs)


def test_key_segment_empty_and_invalid_transcript(
    ks_mod: ModuleType, tmp_path: Path
) -> None:
    ctx = _FakeCtx(tmp_path / "ctx")
    assert ks_mod.run({"transcript": []}, {}, ctx) == {"ranges": []}  # 空转写=真实结果
    with pytest.raises(PluginError, match="缺少输入 transcript"):
        ks_mod.run({}, {}, ctx)
    with pytest.raises(PluginError, match="须为段落列表"):
        ks_mod.run({"transcript": "不是列表"}, {}, ctx)
    with pytest.raises(PluginError, match="start ≥ end"):
        ks_mod.run({"transcript": [_seg(5.0, 1.0, "倒挂")]}, {}, ctx)
    with pytest.raises(PluginError, match="max_segments"):
        ks_mod.run({"transcript": [_seg(0, 1, "x")]}, {"max_segments": "八"}, ctx)


# ── d) frame-grab：argv 断言 / artifacts_dir / clip / 校验 ─────────


def _mock_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """mock subprocess.run：记录 argv 并按 argv[-1] 产出非空文件（帧/片段壳）。"""
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        calls.append(list(argv))
        Path(argv[-1]).write_bytes(b"JFIF-fake-bytes")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)
    return calls


_RANGES: list[dict[str, Any]] = [
    {"start": 10.0, "end": 20.0, "label": "要点A", "confidence": 0.8, "text_excerpt": "x"},
    {"start": 65.0, "end": 80.0, "label": "要点B", "confidence": 0.3, "text_excerpt": "y"},
]


def _media(tmp_path: Path) -> Path:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"fake-media")
    return media


def test_frame_grab_manifest_contract() -> None:
    m = _manifest_of("frame-grab")
    assert m["kind"] == "process"
    assert m["inputs"]["ranges"]["type"] == "ranges"
    assert m["inputs"]["media_path"]["type"] == "file/path"
    assert {k: v["type"] for k, v in m["outputs"].items()} == {"images": "images", "clips": "clips"}
    assert m["config_schema"]["mode"]["default"] == "frame"
    assert "ffmpeg" in m["requires"]
    assert set(m["permissions"]) == {"fs:read", "fs:write:output", "exec:subprocess"}


def test_frame_grab_frame_mode_argv_and_artifacts(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """硬要求 27：-ss 偏移非原值（start+0.3）+ -frames:v 1 + 产物在 artifacts_dir。"""
    calls = _mock_ffmpeg(monkeypatch)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = fg_mod.run(
        {"ranges": _RANGES, "media_path": str(_media(tmp_path))}, {}, ctx
    )

    assert len(calls) == 2
    argv = calls[0]
    assert argv[0] == "ffmpeg"
    ss = float(argv[argv.index("-ss") + 1])
    assert ss == pytest.approx(10.0 + 0.3)  # 避开转场：偏移 0.3s
    assert ss != 10.0  # 非原值（硬要求 27 字面）
    assert argv[argv.index("-i") + 1] == str(_media(tmp_path))
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert "-q:v" in argv  # jpg 质量档
    out_path = Path(argv[-1])
    assert str(out_path).startswith(str(ctx.artifacts_dir))  # 产物受管（不污染用户目录）
    assert out_path.suffix == ".jpg"

    images = out["images"]
    assert out["clips"] == []  # frame 模式 clips 置空（两键恒在，契约不缺）
    assert len(images) == 2
    assert images[0]["ts"] == pytest.approx(10.3)
    assert images[0]["source_range"] == {"start": 10.0, "end": 20.0,
                                         "label": "要点A", "confidence": 0.8}
    assert Path(images[0]["path"]).is_file()  # 产物真落盘（mock 写入）
    assert all(str(Path(i["path"])).startswith(str(ctx.artifacts_dir)) for i in images)


def test_frame_grab_short_range_falls_back_to_midpoint(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """区间短于转场偏移窗 → 抽帧时刻退守中点（宁中点不越界）。"""
    calls = _mock_ffmpeg(monkeypatch)
    out = fg_mod.run(
        {"ranges": [{"start": 5.0, "end": 5.2, "label": "短", "confidence": 0.9}],
         "media_path": str(_media(tmp_path))},
        {}, _FakeCtx(tmp_path / "ctx"),
    )
    argv = calls[0]
    assert float(argv[argv.index("-ss") + 1]) == pytest.approx(5.1)  # (5.0+5.2)/2
    assert out["images"][0]["ts"] == pytest.approx(5.1)


def test_frame_grab_clip_mode_copy(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clip 模式：-ss 原点/-t 时长/-c copy 流复制；产物 mp4 落 artifacts_dir。"""
    calls = _mock_ffmpeg(monkeypatch)
    ctx = _FakeCtx(tmp_path / "ctx")
    out = fg_mod.run(
        {"ranges": _RANGES, "media_path": str(_media(tmp_path))},
        {"mode": "clip"}, ctx,
    )
    assert out["images"] == []
    assert len(calls) == 2 and len(out["clips"]) == 2
    argv = calls[0]
    assert argv[argv.index("-ss") + 1] == "10.000"  # clip 用原起点
    assert float(argv[argv.index("-t") + 1]) == pytest.approx(10.0)  # 时长=end-start
    assert argv[argv.index("-c") + 1] == "copy"
    clip = out["clips"][0]
    assert clip["start"] == 10.0 and clip["end"] == 20.0
    assert str(Path(clip["path"])).startswith(str(ctx.artifacts_dir))
    assert Path(clip["path"]).suffix == ".mp4"


def test_frame_grab_failures_readable(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = _media(tmp_path)
    ctx = _FakeCtx(tmp_path / "ctx")
    # 输入校验
    with pytest.raises(PluginError, match="media_path"):
        fg_mod.run({"ranges": _RANGES}, {}, ctx)
    with pytest.raises(PluginError, match="不存在"):
        fg_mod.run({"ranges": _RANGES, "media_path": str(tmp_path / "no.mp4")}, {}, ctx)
    with pytest.raises(PluginError, match="ranges"):
        fg_mod.run({"media_path": str(media)}, {}, ctx)
    with pytest.raises(PluginError, match="mode"):
        fg_mod.run({"ranges": _RANGES, "media_path": str(media)}, {"mode": "gif"}, ctx)
    # ffmpeg 非零退出 → 可读失败（stderr 尾行）
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: subprocess.CompletedProcess(
        argv, 1, stdout=b"", stderr=b"Error opening input file"))
    with pytest.raises(PluginError, match="Error opening input file"):
        fg_mod.run({"ranges": _RANGES, "media_path": str(media)}, {}, ctx)
    # ffmpeg 缺席 → 安装引导
    monkeypatch.setattr("subprocess.run", lambda argv, **kw: (_ for _ in ()).throw(
        FileNotFoundError("ffmpeg")))
    with pytest.raises(PluginError, match="brew install ffmpeg"):
        fg_mod.run({"ranges": _RANGES, "media_path": str(media)}, {}, ctx)


def test_frame_grab_empty_ranges_and_cancel(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = _FakeCtx(tmp_path / "ctx")
    out = fg_mod.run({"ranges": [], "media_path": str(_media(tmp_path))}, {}, ctx)
    assert out == {"images": [], "clips": []}  # 上游空选段=真实结果，不失败

    monkeypatch.setattr(_FakeCtx, "cancelled", property(lambda self: True))
    with pytest.raises(PluginCancelled):
        fg_mod.run({"ranges": _RANGES, "media_path": str(_media(tmp_path))}, {}, ctx)


def test_frame_grab_prefers_edited_ranges(
    fg_mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """编辑版优先（评审 G-3）：ranges.edited.json 只留一段 → 只抽一段。"""
    calls = _mock_ffmpeg(monkeypatch)
    root = tmp_path / "ctx"
    _FakeCtx(root).artifacts_dir.mkdir(parents=True, exist_ok=True)
    (root / "ks-node").mkdir()
    (root / "ks-node" / "ranges.edited.json").write_text(json.dumps([
        {"start": 30.0, "end": 40.0, "label": "勾选段", "confidence": 0.9}
    ], ensure_ascii=False), encoding="utf-8")

    ctx = _FakeCtx(root)
    out = fg_mod.run({"ranges": _RANGES, "media_path": str(_media(tmp_path))}, {}, ctx)
    assert len(calls) == 1  # 编辑版一段，原始两段被替换
    assert float(calls[0][calls[0].index("-ss") + 1]) == pytest.approx(30.3)
    assert out["images"][0]["source_range"]["label"] == "勾选段"
    assert any("编辑版 ranges" in line for line in ctx.logs)


# ── e) 接力：key-segment → frame-grab 三级时间戳锚 ────────────────


def test_relay_key_segment_to_frame_grab_traceable(
    ks_mod: ModuleType, fg_mod: ModuleType,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正典 §9.3 时间戳锚：images[k].ts → ranges[j].start(+0.3) → segments[i]
    三级可回溯（插件级接力；引擎级见下方四节点链）。"""
    segments = _three_group_segments()
    ranges = ks_mod.run({"transcript": segments}, {}, _FakeCtx(tmp_path / "ks"))["ranges"]
    calls = _mock_ffmpeg(monkeypatch)
    images = fg_mod.run(
        {"ranges": ranges, "media_path": str(_media(tmp_path))}, {}, _FakeCtx(tmp_path / "fg")
    )["images"]

    assert images and len(calls) == len(ranges)
    for img in images:
        # 二级：ts 可在 ranges 中定位来源（偏移 0.3 或短区间中点）
        src = next((r for r in ranges
                    if abs(r["start"] + 0.3 - img["ts"]) < 1e-9
                    or (r["end"] - r["start"] <= 0.3
                        and abs((r["start"] + r["end"]) / 2 - img["ts"]) < 1e-9)), None)
        assert src is not None, f"ts={img['ts']} 无法回溯到任何 range"
        assert img["source_range"]["start"] == src["start"]
        # 一级：ts 落在某个转写段落的时间窗内
        cover = next((s for s in segments
                      if s["start"] <= img["ts"] <= s["end"]), None)
        assert cover is not None, f"ts={img['ts']} 不在任何 segment 时间窗内"


# ── f) 引擎四节点链（全 mock）─────────────────────────────────────


class _FakeSegment:
    def __init__(self, start: float, end: float, text: str,
                 avg_logprob: float, no_speech_prob: float) -> None:
        self.start, self.end, self.text = start, end, text
        self.avg_logprob, self.no_speech_prob = avg_logprob, no_speech_prob


class _FakeInfo:
    duration = 36.0


class _FakeWhisperModel:
    def __init__(self, *a: Any, **kw: Any) -> None: ...
    def transcribe(self, audio: str, **kw: Any) -> Any:
        return iter([_FakeSegment(0.0, 2.0, " 深入浅出 ", -0.4, 0.05)]), _FakeInfo()


def _mock_media_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffprobe（时长）/ ffmpeg（写产物壳）统一路由假件。"""
    def fake_run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess:
        if argv[0] == "ffprobe":
            return subprocess.CompletedProcess(argv, 0, stdout=b"36.0\n", stderr=b"")
        Path(argv[-1]).write_bytes(b"fake")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeWhisperModel)
    )


def test_four_node_static_contract() -> None:
    """正典 §9.1 首链四节点静态契约：local-file→transcribe→key-segment→frame-grab 绿。"""
    manifests = [_manifest_of(d) for d in
                 ("local-file", "transcribe", "key-segment", "frame-grab")]
    nodes = [{"id": "src", "plugin": manifests[0]}, {"id": "tr", "plugin": manifests[1]},
             {"id": "ks", "plugin": manifests[2]}, {"id": "fg", "plugin": manifests[3]}]
    assert validate_pipeline_contract(nodes) == []


def test_engine_four_node_builtin_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引擎全链：media_path 跨两节点自动接线（local-file.file → frame-grab）、
    AI 切片经治理层（mock 在 gateway 模块对象上，引擎加载的插件副本同生效）、
    帧产物入 workspace 且 ts 三级可回溯。"""
    _mock_media_toolchain(monkeypatch)
    monkeypatch.setattr(ai_profiles, "ai_configured", lambda task=None: True)
    ai_tasks: list[str] = []

    def fake_chat_policed(messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        ai_tasks.append(kwargs["task"])
        return {"text": json.dumps([
            {"start": 0.0, "end": 2.0, "label": "开场要点", "confidence": 0.9,
             "text_excerpt": "深入浅出"}
        ], ensure_ascii=False)}

    monkeypatch.setattr(ai_gateway, "chat_policed", fake_chat_policed)

    media = _media(tmp_path)
    model_dir = tmp_path / "m"
    model_dir.mkdir()  # 存在的本地目录 → transcribe 模型就位探测直认（零网络）
    result = psp.run_pipeline(
        [
            {"id": "src", "plugin": "local-file", "inputs": {"path": str(media)}},
            {"id": "tr", "plugin": "transcribe", "config": {"model": str(model_dir)}},
            {"id": "ks", "plugin": "key-segment"},
            {"id": "fg", "plugin": "frame-grab"},
        ],
        plugins_root=BUILTIN_DIR,
        run_root=tmp_path / "runs",
        run_id="pl-builtin-0002",
    )
    assert result["status"] == "done", result
    assert [s["status"] for s in result["steps"]] == ["ok", "ok", "ok", "ok"]
    assert ai_tasks == ["plugin_key_segment"]  # 引擎加载副本也走治理层任务键

    def _outputs(node: str) -> dict[str, Any]:
        return json.loads(
            (Path(result["run_dir"]) / "workspace" / node / "outputs.json")
            .read_text(encoding="utf-8")
        )["inline"]

    images = _outputs("fg")["images"]
    ranges = _outputs("ks")["ranges"]
    segments = _outputs("tr")["transcript"]
    assert len(images) == 1
    assert images[0]["ts"] == pytest.approx(0.3)  # range.start(0.0)+0.3
    assert images[0]["source_range"]["label"] == "开场要点"
    # 三级锚：images.ts ← ranges.start(+0.3)；ts 落在 segment 时间窗内
    assert images[0]["ts"] == pytest.approx(ranges[0]["start"] + 0.3)
    assert any(s["start"] <= images[0]["ts"] <= s["end"] for s in segments)
    assert Path(images[0]["path"]).is_file()
