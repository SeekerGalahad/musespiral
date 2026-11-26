"""TXT 编码探测（L2 纯，零依赖）——BOM → UTF-8 严格 → GB18030 → Big5。

设计正典=《musespiral-三线扩展设计-本地TXT书籍-视觉编辑2-多媒体-2026-11-26》
§1.2 表格首行：`BOM → UTF-8 严格试解 → GB18030 → Big5，带置信度`；同节
「不引入 chardet 依赖（零依赖红线）；GB18030 覆盖 GBK/GB2312」。

为什么这个顺序（实测判例，不是先验）：
  - BOM 是声明式的，有就用——UTF-8/UTF-16 的 BOM 三/两字节前缀不会出现在
    合法的 GB18030/Big5 正文开头，先判零误伤；
  - UTF-8 严格试解：Python 的 utf-8 解码器对非法字节序列抛错，中文 UTF-8
    文本（3 字节序列）几乎不可能被 GB18030 混淆通过反向验证，反之 GB18030
    文本（2 字节序列）在 utf-8 严格模式下**必失败**（实测：「第一章」GB18030
    编码 6 字节 → utf-8 判 invalid start byte）——所以顺序放在 GB18030 之前
    是安全的，且 UTF-8 命中时置信度可以给满；
  - GB18030 → Big5：两者都是双字节族，存在互相可解的字节串（实测：Big5 编码
    的「第一章」被 GB18030 解成乱码+私用区字符而不抛错）。所以不能只看「解码
    不抛错」，必须加**可读性打分**——见下。

置信度口径（0.0~1.0，如实给，不粉饰）：
  - 0.99：BOM 命中（声明式证据）；
  - 0.9：UTF-8 严格解码成功（GB18030 文本过不了 utf-8 严格模式，反向证据强）；
  - 0.6~0.85：双字节候选按打分取胜（GB18030/Big5 互相可解时**只能靠打分**，
    所以封顶 0.85 而不谎称确定）；两候选原文一致（纯 ASCII）→ 0.9；
  - 0.3：全部候选都解不了（回退 utf-8 + errors=replace 的调用方兜底责任，
    本层只报告事实）。
"""
from __future__ import annotations

import unicodedata
from typing import Any

# 候选编码顺序（探测优先级；GB18030 必须在 Big5 之前——它是 GBK/GB2312 的
# 超集，覆盖面最大，同分时优先更通用的那一族）
_CANDIDATES: tuple[str, ...] = ("gb18030", "big5")

# BOM 表（前缀 → (编码, BOM 字节长度)）。UTF-32 的 BOM 是 UTF-16 的前缀超集，
# 必须排在 UTF-16 之前（否则 UTF-32LE 会被当成 UTF-16LE + 两个 NUL）。
_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

# 探测只读文件头（编码是文本属性，不需要整本读完；1MB 足够判定，且大文件
# 场景下避免整书读入内存的假象——services 层读文件时同口径）
SAMPLE_BYTES = 1_048_576


def _sample(raw: bytes) -> bytes:
    """取探测样本：文件头 SAMPLE_BYTES 字节（超长文件不整体参与打分）。"""
    return raw if len(raw) <= SAMPLE_BYTES else raw[:SAMPLE_BYTES]


def _decode(raw: bytes, codec: str) -> str | None:
    """严格解码一次；失败返回 None（探测期不允许 errors=replace 掩盖真相）。"""
    try:
        return _sample(raw).decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None


def readability_score(text: str) -> float:
    """文本可读性打分（0.0~1.0）——双字节候选互相可解时的唯一裁决依据。

    打分口径（越像「给人读的中文正文」越高）：
      - 汉字（CJK 统一表意）与常见中日韩标点、ASCII 可打印字符记满分；
      - 私用区（U+E000..U+F8FF，Big5/GB18030 错解的典型产物，实测如实出现
        `\\ue5e6` 这类字符）、控制字符、替换符 U+FFFD、未分配码位记 0 分；
      - 其余（拉丁扩展、其他脚本）记 0.5——不常见但不判死。

    为什么不用「抛不抛错」：实测 Big5 文本被 GB18030 解码**不抛错**（产出私用
    区乱码），只靠异常判定会把 Big5 书标成 GB18030。打分是把「解得出」升级为
    「解得对」的最小手段，不引第三方库。
    """
    if not text:
        return 0.0
    total = 0.0
    for ch in text:
        code = ord(ch)
        if ch in "\n\r\t":
            total += 1.0
        elif code < 0x20:  # 其他控制字符
            continue
        elif 0x4E00 <= code <= 0x9FFF:  # CJK 统一表意（正文主体）
            total += 1.0
        elif 0x3000 <= code <= 0x303F:  # CJK 标点
            total += 1.0
        elif 0xFF00 <= code <= 0xFFEF:  # 全角符号（含全角标点、替换符 U+FFFD）
            total += 0.0 if ch == "\ufffd" else 1.0
        elif 0xE000 <= code <= 0xF8FF:  # 私用区——错解铁证
            continue
        elif code < 0x7F:  # ASCII 可打印
            total += 1.0
        elif unicodedata.category(ch) in ("Cc", "Cn", "Co", "Cs"):  # 控制/未分配/私用/代理
            continue
        else:
            total += 0.5
    return round(total / len(text), 4)


def detect_bytes(raw: bytes) -> dict[str, Any]:
    """探测字节流的文本编码 → `{charset, confidence, bom, sample_bytes}`。

    返回的 `charset` 是**可直接喂给 `bytes.decode()` 的编码名**：
      - BOM 命中 → `utf-8-sig`（其自带剥 BOM 语义，调用方无需二次处理）；
      - 双字节命中 → `gb18030` / `big5`（原始编码名，不换成 gbk/big5hkscs 别名）；
      - 全部失败 → `utf-8`（配 confidence≈0.3，调用方须 errors="replace" 兜底）。

    `bom` 是 `True/False`（是否以 BOM 开头）；空文件 → `{charset:"utf-8",
    confidence:1.0, bom:False, sample_bytes:0}`——空文本用任何编码读都是空串，
    这是唯一一个可以给满置信度的「无证据」情形，如实注明。
    """
    sample = _sample(raw)
    if not sample:
        return {"charset": "utf-8", "confidence": 1.0, "bom": False, "sample_bytes": 0}
    for bom, codec in _BOMS:
        if sample.startswith(bom):
            # BOM 命中即声明式证据：即便后续字节解不全（截断样本），编码判定
            # 本身是 BOM 说的，调用方按该编码解码即可
            return {
                "charset": codec,
                "confidence": 0.99,
                "bom": True,
                "sample_bytes": len(sample),
            }
    text = _decode(sample, "utf-8")
    if text is not None:
        return {
            "charset": "utf-8",
            "confidence": 0.9,
            "bom": False,
            "sample_bytes": len(sample),
        }
    best: tuple[float, str, str] | None = None  # (score, codec, text)
    texts: dict[str, str] = {}
    for codec in _CANDIDATES:
        decoded = _decode(sample, codec)
        if decoded is None:
            continue
        texts[codec] = decoded
        score = readability_score(decoded)
        # 同分优先更通用的那一族（_CANDIDATES 顺序=gb18030 先；严格大于才换）
        if best is None or score > best[0]:
            best = (score, codec, decoded)
    if best is None:
        return {
            "charset": "utf-8",
            "confidence": 0.3,
            "bom": False,
            "sample_bytes": len(sample),
        }
    score, codec, _ = best
    # 双候选解出同一文本（纯 ASCII 字节串）→ 编码不可区分，但不影响正确解码，
    # 置信度按「文本一致」给 0.9；否则按打分封顶 0.85（互相可解的族只能靠打分）
    if len(texts) > 1 and len(set(texts.values())) == 1:
        return {
            "charset": codec,
            "confidence": 0.9,
            "bom": False,
            "sample_bytes": len(sample),
        }
    confidence = round(min(0.85, 0.5 + score * 0.4), 2)
    return {
        "charset": codec,
        "confidence": confidence,
        "bom": False,
        "sample_bytes": len(sample),
    }


__all__ = ["SAMPLE_BYTES", "detect_bytes", "readability_score"]
