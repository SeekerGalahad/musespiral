"""素材文件仓存取（第二十一期 §1.1/F59）——L1 基础设施。

`~/.musespiral/materials/files/` 的唯一读写面（正典 §一 关键决策一）：

  files/
    <material_id>.<ext>        # 原图（不可变——内容寻址，落盘后字节不动）
    <material_id>.thumb.webp   # 缩略缓存（长边 ≤480px，WebP q80；gif 首帧）

职责：sha256 内容指纹 / 魔数嗅探（不信任扩展名）/ Pillow 校验取尺寸
真值 / 原子落盘（临时名+rename，失败清理无残留）/ 缩略同步生成 /
8MiB 单文件门。素材文件不进 articles root（正典纪律 1）；缩略是缓存
不是素材（纪律 2）；本模块不 import 上层（services/studio）——架构门
规则 4 由 test_architecture 锚死。Pillow 是 pyproject 既有依赖。
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
from typing import Any

from PIL import Image

from .. import config

# 8MiB 单文件门（正典 §二-F59：Content-Length 预检与字节真值双保险）
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# 格式白名单：魔数嗅探结果（ext）→ Content-Type（svg/bmp/heic/tiff 恒拒）
ALLOWED_FORMATS: dict[str, str] = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# Pillow format 名 → 魔数 ext（双源互证：魔数与解码器判定必须一致）
_PIL_TO_EXT = {"PNG": "png", "JPEG": "jpeg", "GIF": "gif", "WEBP": "webp"}

# 缩略缓存参数（正典 §1.1：长边 ≤480px，WebP q80）
_THUMB_EDGE = 480
_THUMB_QUALITY = 80


def files_dir() -> Path:
    """素材文件仓根：~/.musespiral/materials/files/（随 MUSESPIRAL_HOME）。"""
    return config.home_dir() / "materials" / "files"


def sniff_format(data: bytes) -> str:
    """魔数嗅探 → 白名单 ext（png/jpeg/gif/webp）；不识别返回 ""。

    不信任扩展名（正典 §二-F59 校验链第 2 道）；svg 恒拒（同源脚本
    判例，assets.py:22 同纪律）。
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def sniff_content_type(data: bytes) -> str | None:
    """魔数 → Content-Type（白名单外/不识别 → None）。"""
    ext = sniff_format(data)
    return ALLOWED_FORMATS.get(ext)


def inspect_image(data: bytes) -> dict[str, Any]:
    """上传字节体检：8MiB 门 → 魔数嗅探 → Pillow verify → 尺寸真值。

    返回 {sha256, ext, format, width, height, bytes}；任何一道不过 →
    ValueError（可读文案带真实判定结果，400 语义）。
    """
    if not data:
        raise ValueError("上传内容为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"文件超过 8MiB 单文件上限: {len(data)} 字节")
    ext = sniff_format(data)
    if not ext:
        head = data[:8].hex()
        raise ValueError(
            "不支持的图片格式（仅 PNG/JPEG/GIF/WebP，按文件头魔数判定；"
            f"svg/bmp/heic/tiff 恒拒）: 文件头 {head}"
        )
    try:
        with Image.open(io.BytesIO(data)) as img:
            pil_format = img.format
            width, height = img.size
            img.verify()  # 解码器级校验（截断/损坏字节在此炸出）
    except Exception as err:  # noqa: BLE001 - Pillow 异常族不可枚举，统一转可读
        raise ValueError(
            f"图片内容校验失败（魔数判定为 {ext.upper()} 但解码器拒绝）: {err}"
        ) from None
    if _PIL_TO_EXT.get(pil_format or "") != ext:
        raise ValueError(f"图片魔数与解码器判定不符: 魔数={ext}, 解码器={pil_format}")
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "ext": ext,
        "format": ext.upper(),
        "width": width,
        "height": height,
        "bytes": len(data),
    }


def _atomic_write(path: Path, data: bytes) -> None:
    """原子落盘：临时名写入 + rename；中途抛错清理临时文件（无 .tmp 残留）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def store_original(material_id: str, ext: str, data: bytes) -> Path:
    """原图落盘：files/<id>.<ext>（内容寻址，同图重写同字节——幂等）。"""
    path = files_dir() / f"{material_id}.{ext}"
    _atomic_write(path, data)
    return path


def make_thumbnail(material_id: str, data: bytes) -> Path:
    """缩略缓存生成：files/<id>.thumb.webp（长边 ≤480，WebP q80）。

    gif 取首帧（Image.open 惰性定位在第 0 帧）；生成失败 → ValueError
    （可读，400 语义）；缩略是缓存不是素材（不进 materials.jsonl）。
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            frame = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img.copy()
            frame.thumbnail((_THUMB_EDGE, _THUMB_EDGE))  # 保纵横比，长边 ≤480
            buf = io.BytesIO()
            frame.save(buf, "WEBP", quality=_THUMB_QUALITY)
    except Exception as err:  # noqa: BLE001 - 同上，统一转可读
        raise ValueError(f"缩略图生成失败: {err}") from None
    path = files_dir() / f"{material_id}.thumb.webp"
    _atomic_write(path, buf.getvalue())
    return path


def local_rel_path(material_id: str, ext: str) -> str:
    """payload.local_path 形态：相对 ~/.musespiral 的 posix 路径（正典 §1.1）。"""
    return f"materials/files/{material_id}.{ext}"
