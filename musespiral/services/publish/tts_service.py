"""TTS 朗读服务（G-T5，L3）——edge-tts 合成 → MP3 落文章目录。

**出口性质（与 LLM 出网的分别，读代码前先分清）**：`edge_tts` 打的是
Microsoft Edge 的**语音合成服务端点**（`speech.platform.bing.com` 一族，
由 edge-tts 包内部持有，musespiral 侧无可配 base_url）。它是**外部第三方服务、
不是 LLM**——因此：
  - **不经 `infra/ai/gateway.chat_policed`**（那条链路是 chat 任务的缓存→
    预算→调用→记账→缓存五段序，TTS 无 token、无 chat TASKS 键、无路由）；
  - **不进 `TASKS`/`routing`/`ai.json` 档案**（评审判例同 G1 media 域：
    非 chat 键混进白名单会被 `ai check` 用 chat 端点误探测）；
  - 无需任何 API key（用户已授权的免费档），失败只有一种可读形态：网络/
    服务端不可达或文本被拒 → TTSSynthesisError（脱敏由上层做）。

**产物纪律**：`<文章 stem>.mp3` 落在**文章所在目录**（设计 §6.2 原文），
临时文件 + `os.replace` 原子落盘——中断不留半截 MP3（半截文件会被
`<audio>` 当成完好音频播到末尾，比报错更难察觉）。

**依赖与出口单一性**：`edge-tts` 已在 pyproject 主 dependencies（设计 §6.2 的
`pip install edge-tts` 收编进依赖表），但**本模块是全仓唯一 `import edge_tts`
的地方**（tests/test_tts.py 的 `test_edge_tts_single_egress` 断言面），且导入
发生在**调用期**——源码目录未 pip install -e 时抛 TTSUnavailableError 给可读
引导，不让整个包因缺一个能力而 import 失败。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...core.content.parser import parse_article
from ...core.tts.script import speech_text
from ...core.tts.voice import looks_like_mp3, normalize_rate, validate_voice

# 合成超时（秒）：TTS 是逐字流式返回，长文（万字级）实测分钟级；给 300s
# 足够覆盖且不让 UI 无限等待。edge_tts.Communicate 的 receive_timeout 是
# **单次分片**的接收超时，与整体时长无关，故本常量只作文档性预算说明。
SYNTHESIS_BUDGET_SECONDS = 300


class TTSServiceError(ValueError):
    """业务校验错误（可读中文；Studio 转 400 / CLI 退 1）。"""


class TTSUnavailableError(RuntimeError):
    """edge-tts 未安装（可选依赖缺席；给可读安装引导）。"""


class TTSSynthesisError(RuntimeError):
    """合成失败（网络/远端拒绝/产物异常——文本可读，敏感值由上层脱敏）。"""


def _import_edge_tts() -> Any:
    """调用期导入 edge-tts（本模块是全仓唯一导入点；缺席给安装引导）。"""
    try:
        import edge_tts  # noqa: PLC0415 - 调用期导入：缺席时给可读引导而非 import 失败
    except ImportError as exc:  # pragma: no cover - 依赖已入 pyproject；此路兜源码目录
        raise TTSUnavailableError(
            "edge-tts 未安装：pip install edge-tts（微软免费 TTS，零 API key）"
        ) from exc
    return edge_tts


def mp3_path_for(article_path: str | Path) -> Path:
    """朗读产物路径：与文章同目录的 `<stem>.mp3`（设计 §6.2）。"""
    path = Path(article_path)
    return path.with_suffix(".mp3")


def synthesize(
    article_text: str,
    voice: str | None = None,
    rate: str | None = None,
    *,
    out_path: str | Path,
) -> dict[str, Any]:
    """朗读一段纯文本 → MP3 落 `out_path`（原子替换），返回产物摘要。

    - voice/rate 经 core.tts.voice 白名单与形态校验（越界零网络即拒）；
    - 空白文本 → TTSServiceError（远端对空文本会给零字节音频，那比报错难查）；
    - 合成异常 → TTSSynthesisError（保留原文以便定位，脱敏归上层）；
    - 产物不以 MP3 帧同步字开头 → TTSSynthesisError（防错误页存成 .mp3）。
    """
    text = (article_text or "").strip()
    if not text:
        raise TTSServiceError("朗读文本为空（整篇只有代码块/表格/图片时无话可读）")
    voice_name = validate_voice(voice)
    rate_str = normalize_rate(rate)

    edge_tts = _import_edge_tts()
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    try:
        communicate = edge_tts.Communicate(text, voice_name, rate=rate_str)
        communicate.save_sync(str(tmp))
        _assert_mp3(tmp)
        os.replace(tmp, target)
    except TTSSynthesisError:
        tmp.unlink(missing_ok=True)
        raise
    except BaseException as exc:
        tmp.unlink(missing_ok=True)
        raise TTSSynthesisError(
            f"语音合成失败（voice={voice_name} rate={rate_str}）：{exc}"
        ) from exc
    return {
        "output": str(target),
        "voice": voice_name,
        "rate": rate_str,
        "bytes": target.stat().st_size,
        "chars": len(text),
    }


def _assert_mp3(path: Path) -> None:
    """产物魔数复核：必须是 MP3 帧同步字或 ID3 头（零字节也拒）。"""
    try:
        head = path.read_bytes()[:4]
    except OSError as exc:
        raise TTSSynthesisError(f"合成产物不可读：{exc}") from exc
    if not looks_like_mp3(head):
        if not head:
            raise TTSSynthesisError("合成产物为空（远端未返回音频数据）")
        raise TTSSynthesisError(
            f"合成产物不是 MP3（头部字节 {head.hex()}）——远端可能返回了错误页"
        )


def synthesize_article(
    article_path: str | Path,
    voice: str | None = None,
    rate: str | None = None,
) -> dict[str, Any]:
    """文章 → 朗读脚本 → MP3（CLI `musespiral tts` 与 Studio job 共用入口）。

    解析走 `core.content.parser.parse_article`（与发布链同一解析器），脚本
    抽取走 `core.tts.script.speech_text`（代码/表格/图片不读）。
    """
    path = Path(article_path)
    if not path.is_file():
        raise TTSServiceError(f"文章不存在: {path}")
    script = speech_text(parse_article(path))
    return synthesize(script, voice, rate, out_path=mp3_path_for(path))


__all__ = [
    "SYNTHESIS_BUDGET_SECONDS",
    "TTSServiceError",
    "TTSSynthesisError",
    "TTSUnavailableError",
    "mp3_path_for",
    "synthesize",
    "synthesize_article",
]
