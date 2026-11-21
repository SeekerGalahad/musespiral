"""TTS 朗读命令（G-T5，正典=《musespiral-调研吸收功能设计-2026-11-18》§六）。

  musespiral tts <article> [--voice zh-CN-YunxiNeural] [--rate +10%]
    → <article 同目录>/<stem>.mp3（覆盖写；原子落盘）

**出口性质**：走 `edge_tts` 包的微软免费 TTS 端点（`speech.platform.bing.com`
一族）——是**外部服务**，不是 LLM，故不经 `infra/ai/gateway`、不进 TASKS/
routing/ai.json 档案、零 API key。CLI 侧同步执行（正典 §三-3：任务层只服务
Studio 长动作），失败给可读中文并退 1。

`--voice` 的 `click.Choice` 从 core.tts.voice.VOICES 派生（单一事实源：
白名单加声音时 CLI/Studio 两侧同时生效）。
"""
from __future__ import annotations

import sys

import click

from ..core.tts.voice import DEFAULT_RATE, DEFAULT_VOICE, VOICE_NAMES, voice_labels


@click.command()
@click.argument("article", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--voice",
    type=click.Choice(VOICE_NAMES),
    default=DEFAULT_VOICE,
    show_default=True,
    help="朗读声音（中文声音白名单，见 musespiral tts --help 下方说明）",
)
@click.option(
    "--rate",
    default=DEFAULT_RATE,
    show_default=True,
    help="语速（如 +10% 加速校对 / -10% 慢速听细节；绝对值 ≤100%）",
)
def tts(article: str, voice: str, rate: str) -> None:
    """把文章正文朗读成 MP3（<stem>.mp3 落文章目录，零 API key）。"""
    from ..services.tts_service import (
        TTSServiceError,
        TTSSynthesisError,
        TTSUnavailableError,
        synthesize_article,
    )

    try:
        result = synthesize_article(article, voice, rate)
    except (TTSServiceError, TTSUnavailableError, TTSSynthesisError) as err:
        click.echo(f"❌ {err}", err=True)
        sys.exit(1)
    click.echo(
        f"✅ 朗读已生成: {result['output']}"
        f"（{result['chars']} 字 / {result['bytes']} 字节 / {result['voice']} {result['rate']}）"
    )


# 帮助里的声音清单（click 的 Choice 只列名字，说明由本段补——`--help` 可见）
tts.epilog = "声音: " + " / ".join(
    f"{name}（{label}）" for name, label in voice_labels().items()
)
