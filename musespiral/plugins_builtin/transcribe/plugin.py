"""官方插件·语音转写（首插件族 #2，kind=process，正典 §9.1，PL-4 核心）。

faster-whisper 把音轨转成带时间戳的文字。四条纪律：

**G-1 质量信号（专业底线，需求 §插件二 6）**：faster-whisper 每段原生返回
``avg_logprob`` / ``no_speech_prob``——行业通行的低质量信号（<-1.0 低置信、
>0.6 疑似非语音）。本插件**原样透传**进每个 segment 绝不丢弃：没有它们
用户无法核对转写质量，下游 key-segment 的 min_confidence 也吃它。

**防幻听/防复读（精度调优手册 §5.3/§5.4，需求 §插件二 8/9）**：
``vad_filter`` 默认 True（config 可关）——视频配乐/环境音是 whisper 幻听
（输出「谢谢观看」式幻觉）的头号来源；``condition_on_previous_text`` 恒
False——长视频复读循环必设，代价是跨句连贯性略降，值得。

**可选依赖（沿 tts_service edge-tts 先例）**：``faster_whisper`` 调用期
导入，缺席抛 TranscribeUnavailableError（PluginError 子族，给可读安装
引导）——插件加载与整包 import 不受影响，不让整包因缺一个能力崩。

**模型权重不自动下载（需求 §插件二 11）**：构造 WhisperModel 前先探测
就位（config.model 为本地目录 → 直认；模型名 → 查 huggingface 缓存），
未就位给可读指引（预下载一行命令 / config.model 指向本地目录），绝不
静默拉 1.5GB。境内加速：``export HF_ENDPOINT=https://hf-mirror.com``。

链路：ffmpeg 抽 16kHz 单声道 wav（手册 §2 万能抽轨，落 ctx.tmpdir 受管
区）→ WhisperModel.transcribe → 段循环（逐段自查 ctx.cancelled）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from musespiral.core.plugins.context import PluginCancelled, PluginError

# 转写参数（精度调优手册 §3.1 速查表 + §5）
_DEFAULT_MODEL = "large-v3-turbo"
# ffmpeg 抽出的音轨采样率（whisper 系恒 16kHz mono）
_SAMPLE_RATE = 16000
_BEAM_SIZE = 5
_MIN_SILENCE_MS = 500
_FFMPEG_TIMEOUT_S = 600
_HF_MIRROR_HINT = "export HF_ENDPOINT=https://hf-mirror.com"
_INSTALL_HINT = "pip install faster-whisper"


# 依赖声明：requires（manifest）管命令面 ffmpeg；python 包依赖在此声明，
# /api/plugins/transcribe/check 的探测面据它 find_spec（缺 faster-whisper
# 时该面返回 MISSING_DEP，不崩——探测零执行，ast 静态读取本声明）。
PYTHON_DEPS = ["faster_whisper"]


class TranscribeUnavailableError(PluginError):
    """faster-whisper 未安装（可选依赖缺席；给可读安装引导）。"""


def _import_faster_whisper() -> Any:
    """调用期导入 faster-whisper（缺席给安装引导，不让插件加载/整包 import 失败）。"""
    try:
        import faster_whisper  # noqa: PLC0415 - 调用期导入：缺席时给可读引导
    except ImportError as exc:
        raise TranscribeUnavailableError(
            f"faster-whisper 未安装：{_INSTALL_HINT}"
            "（可选依赖，不随 musespiral 主包安装；模型权重另需就位，见插件说明）"
        ) from exc
    return faster_whisper


# faster-whisper 官方与社区维护的权重仓库（同一个模型名在不同 org 下都有）
# 2026-11-20 真素材实测判例：`WhisperModel('large-v3-turbo')` 实际下载的是
# **mobiuslabsgmbh/faster-whisper-large-v3-turbo**（不是 Systran），只查 Systran
# 会让「按指引预下载成功」的用户仍被判未就位——探测必须覆盖全部候选仓库。
_WEIGHT_REPOS: tuple[str, ...] = (
    "Systran/faster-whisper-{ref}",           # 官方（large-v2 等老模型）
    "mobiuslabsgmbh/faster-whisper-{ref}",    # large-v3-turbo 实际来源
    "deepdml/faster-whisper-{ref}-ct2",       # 社区 ct2 转档
)


def _model_locally_available(model_ref: str) -> bool:
    """模型就位探测（零网络）：显式存在的路径恒 True；模型名遍历
    ``_WEIGHT_REPOS`` 查 huggingface 缓存里的权重文件（faster-whisper 装机即
    随附 huggingface_hub）。探测任何异常一律按「未就位」——收敛到可读指引
    路径，绝不因探测失败抛异常或放行下载。"""
    if Path(model_ref).exists():
        return True
    try:
        from huggingface_hub import try_to_load_from_cache  # noqa: PLC0415
    except Exception:
        return False
    for tpl in _WEIGHT_REPOS:
        try:
            hit = try_to_load_from_cache(
                repo_id=tpl.format(ref=model_ref), filename="model.bin"
            )
        except Exception:
            continue
        if isinstance(hit, str):  # 缓存路径 str=已就位；None/哨兵=未就位
            return True
    return False


_MODEL_GUIDE = (
    "模型 {ref} 未在本地就位（既非存在的本地目录，也不在 huggingface 缓存）。"
    "本插件不自动下载权重（large 系约 1.5GB）。两种就位方式："
    "① 预下载后重跑：`{mirror} python -c \"from faster_whisper import WhisperModel; "
    "WhisperModel('{ref}', device='cpu', compute_type='int8')\"`；"
    "② 流水线 config.model 直接指向已下载模型的本地目录。"
)


def _extract_audio(media: Path, wav_out: Path) -> None:
    """ffmpeg 抽音轨：16kHz 单声道 pcm_s16le wav（手册 §2 万能抽轨）。"""
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(wav_out)],
            capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=False,
        )
    except FileNotFoundError as exc:
        raise PluginError(
            "ffmpeg 未找到：请先安装（brew install ffmpeg / apt install ffmpeg）"
            "——requires 面的依赖检查（/api/plugins/transcribe/check）可见"
        ) from exc
    except subprocess.SubprocessError as exc:
        raise PluginError(f"ffmpeg 抽音轨异常: {exc}") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise PluginError(f"ffmpeg 抽音轨失败（exit {proc.returncode}）：{' | '.join(tail)}")
    if not wav_out.is_file() or wav_out.stat().st_size == 0:
        raise PluginError(f"ffmpeg 未产出音轨（空/缺失）: {wav_out}")


def _wav_to_float32(wav_path: Path) -> Any:
    """读 16kHz mono s16le PCM 为 float32 numpy 数组（[-1, 1] 归一）。

    绕开 faster-whisper 内部的 av 解码路径（见 run() 内的判例注）。
    numpy 是 faster-whisper 的既有依赖（ctranslate2 链路），无需新增。
    """
    try:
        import numpy as np  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - faster-whisper 已依赖 numpy
        raise PluginError(f"numpy 不可用（faster-whisper 的既有依赖）: {exc}") from exc
    raw = wav_path.read_bytes()
    if len(raw) < 2:
        raise PluginError(f"音轨文件过小（{len(raw)} 字节），无法转写: {wav_path}")
    pcm = np.frombuffer(raw, dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def run(inputs: dict, config: dict, ctx: Any) -> dict:
    media_raw = inputs.get("media_path")
    if media_raw is None or not str(media_raw).strip():
        raise PluginError("缺少输入 media_path（音视频文件路径）")
    media = Path(str(media_raw))
    if not media.is_file():
        raise PluginError(f"媒体文件不存在: {media}")

    model_ref = str(config.get("model") or _DEFAULT_MODEL)
    vad_filter = bool(config.get("vad_filter", True))
    language = str(inputs.get("language") or "").strip()
    hotwords = inputs.get("hotwords") or []

    fw = _import_faster_whisper()
    if not _model_locally_available(model_ref):
        raise PluginError(_MODEL_GUIDE.format(ref=model_ref, mirror=_HF_MIRROR_HINT))

    # 抽音轨进受管临时目录（运行结束引擎自动清理，§4.4 ctx.tmpdir）
    wav = ctx.tmpdir() / "audio.wav"
    ctx.log(f"transcribe: 抽音轨 {media} → {wav.name}（16kHz mono）")
    _extract_audio(media, wav)

    kwargs: dict[str, Any] = {
        "beam_size": _BEAM_SIZE,
        # 手册 §5.3：VAD 必开（config 可显式关）——防配乐幻听
        "vad_filter": vad_filter,
        "vad_parameters": {"min_silence_duration_ms": _MIN_SILENCE_MS},
        # 手册 §5.4：长视频防复读循环，恒关（不接受 config 覆盖）
        "condition_on_previous_text": False,
    }
    if language and language.lower() != "auto":
        kwargs["language"] = language  # 手册 §5.2：显式语言比 auto 稳
    if isinstance(hotwords, list) and hotwords:
        # whisper 系热词走 initial_prompt（手册 §5.5：人名/术语命中率差距巨大）
        kwargs["initial_prompt"] = "、".join(str(h) for h in hotwords if str(h).strip())

    ctx.log(
        f"transcribe: WhisperModel({model_ref}, cpu/int8) "
        f"vad_filter={vad_filter} language={language or 'auto'}"
    )
    model = fw.WhisperModel(model_ref, device="cpu", compute_type="int8")
    # **传 numpy 数组而非文件路径**（2026-11-20 真素材实测判例）：传路径时
    # faster-whisper 内部用 av.open(..., metadata_errors="ignore") 解码，而
    # PyAV 19.0.2（当前最新）的 open() 不接受该关键字参数 →
    # `TypeError: open() got an unexpected keyword argument 'metadata_errors'`。
    # 已在 ffmpeg 侧抽出 16kHz mono PCM，此处直接读成 float32 数组绕开 av 解码
    # （transcribe 的 audio 参数原生支持 np.ndarray）——同时省掉一次重复解码。
    audio = _wav_to_float32(wav)
    ctx.log(f"transcribe: 音频 {len(audio) / _SAMPLE_RATE:.1f}s，开始推理")
    seg_iter, info = model.transcribe(audio, **kwargs)

    # G-1 数据面：每段必带质量信号（avg_logprob/no_speech_prob 原样透传）
    segments: list[dict[str, Any]] = []
    for seg in seg_iter:  # 生成器——此处才真正推理
        if ctx.cancelled:  # 段间自查点（正典 §4.4）
            raise PluginCancelled("转写在段间自查点收到取消信号")
        segments.append({
            "start": round(float(seg.start), 3),
            "end": round(float(seg.end), 3),
            "text": str(seg.text).strip(),
            "avg_logprob": float(seg.avg_logprob),
            "no_speech_prob": float(seg.no_speech_prob),
        })
        if len(segments) % 10 == 0:
            ctx.progress(len(segments), max(int(getattr(info, "duration", 0) or 0), len(segments)))
    ctx.progress(len(segments), max(len(segments), 1))

    duration_s = round(float(getattr(info, "duration", 0.0) or 0.0), 3)
    if not segments:
        # VAD 过滤后零段是真实结果（纯音乐/静音素材）——不制造假失败
        ctx.log("transcribe: 0 段（VAD 过滤后无语音段；若非预期可关 vad_filter 重试）")
    else:
        ctx.log(f"transcribe: {len(segments)} 段 / {duration_s}s（每段含 avg_logprob/no_speech_prob）")
    return {"transcript": segments, "duration_s": duration_s}


__all__ = ["TranscribeUnavailableError", "run"]
