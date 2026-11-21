"""TTS 声音白名单与语速校验（G-T5，L2 纯）。

设计正典=《musespiral-调研吸收功能设计-2026-11-18》§六：`zh-CN-YunxiNeural`
（男声/自然）与 `zh-CN-XiaoxiaoNeural`（女声/温柔）为推荐档，语速可调
（`+10%` 加速校对 / `-10%` 慢速听细节）。

**白名单的理由**：edge-tts 的 `voice` 参数直通微软端点，服务端声音清单随
版本漂移（拼错时远端报的是「未找到声音」这种远离根因的错误）。白名单把
「我们承诺可用的声音」收敛成一处常量——CLI 的 `--voice` 选项与 Studio 的
下拉都从这里派生，越界在提交期就拒绝（零网络请求），不进 job。

本模块只做字符串判定，**零 I/O、零网络、零三方依赖**（架构门规则 3 的
域层纪律）。
"""
from __future__ import annotations

import re

# 可朗读声音表（名称，说明）。顺序即 UI 下拉顺序；说明给人看，不进协议。
# 名称是微软 Edge TTS 的 voice short name（大小写敏感，原样送端点）。
VOICES: tuple[tuple[str, str], ...] = (
    ("zh-CN-YunxiNeural", "云希 · 男声自然"),
    ("zh-CN-YunjianNeural", "云健 · 男声解说"),
    ("zh-CN-YunyangNeural", "云扬 · 男声新闻"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · 女声温柔"),
    ("zh-CN-XiaoyiNeural", "晓伊 · 女声活泼"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · 女声台湾"),
    ("zh-HK-HiuMaanNeural", "曉曼 · 女声粤语"),
)

# 声音名清单（CLI click.Choice / Studio 校验共用；不可变元组防调用方污染）
VOICE_NAMES: tuple[str, ...] = tuple(name for name, _label in VOICES)

# 缺省声音：设计 §6.2 推荐男声自然档
DEFAULT_VOICE = "zh-CN-YunxiNeural"

# 缺省语速（edge-tts 的「原速」字面形态）
DEFAULT_RATE = "+0%"

# 语速上限（百分数绝对值）：超过 ±100% 已不是「快/慢一点」而是失真，
# 且远端对过大值的行为不稳定——在本地就拦下。
RATE_MAX_PERCENT = 100

# 语速串形态：可选符号 + 1~3 位整数 + `%`（`10%` / `+10%` / `-10%`）
_RATE_RE = re.compile(r"^([+-]?)(\d{1,3})%$")


def voice_labels() -> dict[str, str]:
    """声音名 → 说明（每次新 dict，防调用方污染常量）。"""
    return dict(VOICES)


def validate_voice(voice: str | None) -> str:
    """校验声音名并返回规范名；缺省取 DEFAULT_VOICE。

    非字符串 / 空白串 / 白名单外一律 ValueError（可读中文，列出可选值）。
    """
    if voice is None:
        return DEFAULT_VOICE
    if not isinstance(voice, str):
        raise ValueError(f"voice 不在白名单（须为字符串；可选: {' / '.join(VOICE_NAMES)}）")
    name = voice.strip()
    if not name:
        return DEFAULT_VOICE
    if name not in VOICE_NAMES:
        raise ValueError(
            f"声音不在白名单: {name!r}（可选: {' / '.join(VOICE_NAMES)}）"
        )
    return name


def normalize_rate(rate: str | None) -> str:
    """语速串校验并规整为 edge-tts 形态 `±N%`；缺省取 DEFAULT_RATE。

    - `10%` / `+10%` → `+10%`；`-10%` → `-10%`；`0%` → `+0%`；
    - 超出 ±RATE_MAX_PERCENT 或形态不符 → ValueError（可读中文）。
    """
    if rate is None:
        return DEFAULT_RATE
    if not isinstance(rate, str):
        raise ValueError("rate 必须是字符串形态（如 +10% / -10%）")
    raw = rate.strip()
    if not raw:
        return DEFAULT_RATE
    match = _RATE_RE.match(raw)
    if match is None:
        raise ValueError(
            f"语速形态非法: {raw!r}（应形如 +10% / -10%，绝对值 ≤{RATE_MAX_PERCENT}%）"
        )
    sign, digits = match.group(1), int(match.group(2))
    if digits > RATE_MAX_PERCENT:
        raise ValueError(
            f"语速超出范围: {raw!r}（绝对值 ≤{RATE_MAX_PERCENT}%）"
        )
    return f"{'-' if sign == '-' else '+'}{digits}%"


# MP3 帧同步字 / ID3 头（落盘前与回放前的**同一份**魔数口径）。
# 六种同步字覆盖 MPEG-1/2/2.5 Layer III 的常见帧头；ID3 是带标签文件的起点。
# 放域层是为了让「合成产物验收」（services）与「回放产物验收」（studio）
# 共用一张表——两份副本各改一次是判例式的漂移源。
MP3_MAGIC: tuple[bytes, ...] = (
    b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa", b"\xff\xe3", b"ID3",
)


def looks_like_mp3(body: bytes) -> bool:
    """字节流头部是否为 MP3（帧同步字或 ID3 头）；空字节流恒 False。"""
    head = (body or b"")[:4]
    return bool(head) and any(head.startswith(magic) for magic in MP3_MAGIC)
