"""subtitle 插件测试：SRT/VTT 解析、质量信号、合并、错误路径。

核心不变量（本插件的存在意义）：
1. 输出与 transcribe **同构**——segments 每段含 start/end/text/avg_logprob/no_speech_prob，
   下游 key-segment 零改动可消费；
2. 人工字幕的质量信号是 1.0/0.0（无 ASR 误识，不该触发低置信告警）；
3. 不支持的字幕格式**可读拒绝**，不静默半解析。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_PATH = (
    Path(__file__).resolve().parent.parent
    / "musespiral" / "plugins_builtin" / "subtitle" / "plugin.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("subtitle_plugin", PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sub = _load()


class _Ctx:
    def __init__(self) -> None:
        self.cancelled = False
        self.logs: list[str] = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)


SRT_SAMPLE = """1
00:00:00,200 --> 00:00:04,700
大家好，我们从第一页开始

2
00:00:04,700 --> 00:00:06,400
<i>Attention is all you need</i>

3
00:00:06,400 --> 00:00:10,200
下面副标题写着一种用于
序列转导的革命性架构

4
01:02:03,500 --> 01:02:08,000
长时间戳测试（1小时后）
"""

VTT_SAMPLE = """WEBVTT

NOTE 这是注释块，必须跳过

STYLE
::cue { color: red }

00:00:01.000 --> 00:00:03.000 align:start position:10%
第一行 VTT 字幕

00:00:03.000 --> 00:00:05.500
第二行 VTT 字幕
"""


@pytest.fixture()
def srt_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.srt"
    p.write_text(SRT_SAMPLE, encoding="utf-8")
    return p


@pytest.fixture()
def vtt_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.vtt"
    p.write_text(VTT_SAMPLE, encoding="utf-8")
    return p


# ── 基本解析 ──────────────────────────────────────────────────


def test_srt_parses_all_cues(srt_file: Path) -> None:
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    segs = r["transcript"]
    assert len(segs) == 4
    assert segs[0]["text"] == "大家好，我们从第一页开始"
    assert segs[0]["start"] == pytest.approx(0.2)
    assert segs[0]["end"] == pytest.approx(4.7)
    assert r["source_kind"] == "subtitle.srt"


def test_srt_strips_tags_by_default(srt_file: Path) -> None:
    """<i> 标记默认剥除——下游看到的是纯文本。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    assert r["transcript"][1]["text"] == "Attention is all you need"
    assert "<i>" not in r["transcript"][1]["text"]


def test_srt_keeps_tags_when_disabled(srt_file: Path) -> None:
    r = sub.run({"subtitle_path": str(srt_file)}, {"strip_tags": False}, _Ctx())
    assert "<i>" in r["transcript"][1]["text"]


def test_multiline_cue_merged_into_one_line(srt_file: Path) -> None:
    """字幕换行是排版不是语义——合并成单行。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    assert r["transcript"][2]["text"] == "下面副标题写着一种用于 序列转导的革命性架构"


def test_hour_scale_timestamp(srt_file: Path) -> None:
    """01:02:03,500 = 3723.5s——小时位必须正确进位。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    assert r["transcript"][3]["start"] == pytest.approx(3723.5)
    assert r["duration_s"] == pytest.approx(3728.0)


# ── 质量信号（本插件的关键语义） ──────────────────────────────


def test_quality_signals_are_full_marks(srt_file: Path) -> None:
    """人工字幕 = ASR 层面天然满分（1.0 / 0.0），不该触发低置信告警。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    for seg in r["transcript"]:
        assert seg["avg_logprob"] == 1.0
        assert seg["no_speech_prob"] == 0.0


def test_output_shape_matches_transcribe(srt_file: Path) -> None:
    """与 transcribe 同构：下游 key-segment 零改动即可消费。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    required = {"start", "end", "text", "avg_logprob", "no_speech_prob"}
    for seg in r["transcript"]:
        assert required <= set(seg.keys())
        assert isinstance(seg["start"], float)
        assert isinstance(seg["end"], float)
        assert seg["end"] > seg["start"]


# ── VTT ───────────────────────────────────────────────────────


def test_vtt_skips_note_and_style(vtt_file: Path) -> None:
    r = sub.run({"subtitle_path": str(vtt_file)}, {}, _Ctx())
    segs = r["transcript"]
    assert len(segs) == 2
    assert segs[0]["text"] == "第一行 VTT 字幕"
    assert all("注释块" not in s["text"] for s in segs)
    assert all("color" not in s["text"] for s in segs)


def test_vtt_strips_cue_settings(vtt_file: Path) -> None:
    """`align:start position:10%` 是 cue 设置，不能进文本。"""
    r = sub.run({"subtitle_path": str(vtt_file)}, {}, _Ctx())
    assert "align" not in r["transcript"][0]["text"]
    assert r["source_kind"] == "subtitle.vtt"


# ── 合并 ──────────────────────────────────────────────────────


def test_merge_disabled_by_default(srt_file: Path) -> None:
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    assert len(r["transcript"]) == 4


def test_merge_adjacent_by_gap(srt_file: Path) -> None:
    """1000ms 阈值：前 3 段（间隔 0s）合并，第 4 段（间隔 3713s）独立。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {"merge_short_ms": 1000}, _Ctx())
    segs = r["transcript"]
    assert len(segs) == 2
    assert segs[0]["start"] == pytest.approx(0.2)
    assert segs[0]["end"] == pytest.approx(10.2)
    assert "大家好" in segs[0]["text"] and "革命性架构" in segs[0]["text"]
    assert segs[1]["start"] == pytest.approx(3723.5)


def test_merge_zero_gap_only(srt_file: Path) -> None:
    """阈值 1ms：只有真正首尾相接（gap=0）的才合并。"""
    r = sub.run({"subtitle_path": str(srt_file)}, {"merge_short_ms": 1}, _Ctx())
    assert len(r["transcript"]) == 2  # 1+2+3 相接合并，4 独立


# ── 错误路径 ──────────────────────────────────────────────────


def test_missing_input_rejected() -> None:
    with pytest.raises(sub.SubtitleError, match="缺少输入 subtitle_path"):
        sub.run({}, {}, _Ctx())


def test_nonexistent_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(sub.SubtitleError, match="不存在"):
        sub.run({"subtitle_path": str(tmp_path / "nope.srt")}, {}, _Ctx())


def test_ass_format_rejected_with_guidance(tmp_path: Path) -> None:
    """富样式字幕可读拒绝，并给 ffmpeg 转换指引（不静默半解析）。"""
    p = tmp_path / "x.ass"
    p.write_text("[Script Info]\n", encoding="utf-8")
    with pytest.raises(sub.SubtitleError) as ei:
        sub.run({"subtitle_path": str(p)}, {}, _Ctx())
    msg = str(ei.value)
    assert "不支持的字幕格式" in msg
    assert "ffmpeg" in msg  # 给了转换指引


def test_empty_subtitle_rejected(tmp_path: Path) -> None:
    p = tmp_path / "empty.srt"
    p.write_text("", encoding="utf-8")
    with pytest.raises(sub.SubtitleError, match="0 段"):
        sub.run({"subtitle_path": str(p)}, {}, _Ctx())


def test_zero_length_cue_skipped(tmp_path: Path) -> None:
    """end <= start 的坏 cue 跳过（坏字幕常见），不产生零长段。"""
    p = tmp_path / "bad.srt"
    p.write_text(
        "1\n00:00:05,000 --> 00:00:05,000\n零长段\n\n"
        "2\n00:00:01,000 --> 00:00:02,000\n正常段\n",
        encoding="utf-8",
    )
    r = sub.run({"subtitle_path": str(p)}, {}, _Ctx())
    assert len(r["transcript"]) == 1
    assert r["transcript"][0]["text"] == "正常段"


def test_cancelled_raises(tmp_path: Path, srt_file: Path) -> None:
    ctx = _Ctx()
    ctx.cancelled = True
    with pytest.raises(Exception):
        sub.run({"subtitle_path": str(srt_file)}, {}, ctx)


def test_bom_and_crlf_handled(tmp_path: Path) -> None:
    """带 BOM 与 CRLF 的 SRT（Windows 导出常见）必须正确解析。"""
    p = tmp_path / "crlf.srt"
    p.write_bytes(
        "\ufeff1\r\n00:00:01,000 --> 00:00:02,000\r\n带BOM的字幕\r\n".encode("utf-8")
    )
    r = sub.run({"subtitle_path": str(p)}, {}, _Ctx())
    assert len(r["transcript"]) == 1
    assert r["transcript"][0]["text"] == "带BOM的字幕"


# ── media_path 透传（链路契约） ────────────────────────────────


def test_media_path_passthrough_when_given(srt_file: Path, tmp_path: Path) -> None:
    """给了 media_path 就透传——下游 frame-grab 才能抽帧。

    2026-11-20 实测判例：不透传会让 subtitle→key-segment→frame-grab 链被
    validate_pipeline_contract 判「必需输入端口未被供给」而整链拒收。
    """
    media = tmp_path / "video.mp4"
    media.write_bytes(b"\x00" * 32)  # 内容不重要，只要存在
    r = sub.run({"subtitle_path": str(srt_file), "media_path": str(media)}, {}, _Ctx())
    assert r["media_path"] == str(media)


def test_media_path_none_when_absent(srt_file: Path) -> None:
    """没给 media_path 输入 → 输出**恒含该键、值为 None**（2026-11-23 扫尾裁决）。

    「声明即产出」是 manifest outputs 的设计原则：引擎按全键校验，键缺席=
    OutputContractError 判失败。旧断言「不在输出里」把缺陷锁成语义（PLUGIN-DEMO
    批发现：无 media_path 输入的字幕链在引擎级必判失败）。下游 frame-grab 对
    None 有可读守卫（「缺少输入 media_path」）——能力边界由消费侧降级表达。
    """
    r = sub.run({"subtitle_path": str(srt_file)}, {}, _Ctx())
    assert r["media_path"] is None
    # 全键契约断言：输出键集与 manifest.outputs 声明完全一致（防未来再漂移）
    assert set(r) == {"transcript", "duration_s", "source_kind", "media_path"}


def test_nonexistent_media_path_rejected(srt_file: Path, tmp_path: Path) -> None:
    with pytest.raises(sub.SubtitleError, match="media_path 指向的文件不存在"):
        sub.run(
            {"subtitle_path": str(srt_file), "media_path": str(tmp_path / "nope.mp4")},
            {},
            _Ctx(),
        )


def test_chain_with_frame_grab_contract_valid(tmp_path: Path) -> None:
    """subtitle 声明 media_path 输出后，含 frame-grab 的链契约接通。"""
    from musespiral.core.plugins.manifest import validate_pipeline_contract
    from musespiral.services import plugins_service as P

    valid, _ = P.scan_plugins()
    by_id = {v["manifest"]["id"]: v["manifest"] for v in valid}
    nodes = [
        {"id": i, "plugin": by_id[i]}
        for i in ["subtitle", "key-segment", "frame-grab", "article-writer", "material-sink"]
    ]
    assert validate_pipeline_contract(nodes) == []


def test_engine_subtitle_without_media_no_contract_fail(tmp_path: Path, srt_file: Path) -> None:
    """引擎级回归（2026-11-23 扫尾）：无 media_path 输入的字幕单节点链不再被
    OutputContractError 判失败——subtitle 恒产出 media_path=None（键在值空）。

    旧行为（键缺席）的失败路径：engine._exec 的 `declared - set(outputs)` 判
    「缺输出键 ['media_path']」→ 节点 failed——PLUGIN-DEMO 批实测发现。
    """
    from musespiral.services import pipeline as psp

    srt = tmp_path / "pure.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n纯字幕链，没有配套媒体\n", encoding="utf-8"
    )
    result = psp.run_pipeline(
        [{"id": "sub", "plugin": "subtitle", "inputs": {"subtitle_path": str(srt)}}],
        run_root=tmp_path / "runs",
        run_id="sub-nomedia-0001",
    )
    assert result["status"] == "done", str(result)
    sub_step = next(s for s in result["steps"] if s["step"] == "sub")
    assert sub_step["status"] == "ok"
