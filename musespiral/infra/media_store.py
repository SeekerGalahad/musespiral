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


# ── 视频媒体（B3 多媒体与视频发布，2026-11-26）─────────────────────────
# 视频不进 8MiB 门：那是 HTTP 上传（Content-Length 预检）的防线，本地登记
# 直接读盘无请求体；大文件落盘用「硬链接优先、跨卷回退 copy2」——同字节
# 零拷贝（build_pack 复制 video.mp4 的同款判例），跨卷才真复制。
VIDEO_EXTS: tuple[str, ...] = ("mp4", "webm", "mov")
# poster 落盘名约定：<material_id>.poster.jpg（与缩略 <id>.thumb.webp 同目录同
# 命名族——都是「缓存不是素材」位，但 poster 要进 payload.meta 供前端 /media/
# 引用，故给确定性路径而非临时文件）
_POSTER_JPEG_QUALITY = 2  # ffmpeg mjpeg 质量档（frame-grab 同档，best 附近）
_PROBE_TIMEOUT_S = 60


def store_media_file(material_id: str, ext: str, src_path: str | Path) -> Path:
    """大媒体（视频）落仓：files/<id>.<ext>——硬链接优先，跨卷回退 copy2。

    与 store_original 的差异：字节不走内存（视频 GB 级不能 read_bytes）、
    不做 8MiB 门（本地登记非 HTTP 上传）、幂等以「目标已存在先删」保证
    （同 id 内容寻址，重登记同文件——链接/复制前清掉旧链接防
    FileExistsError，与 _atomic_write 的「重写同字节」语义对齐）。"""
    import shutil

    path = files_dir() / f"{material_id}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    try:
        os.link(src_path, path)  # 同卷零拷贝
    except OSError:  # EXDEV 跨卷 / 权限等：真复制（元数据保 mtime 便于排查）
        shutil.copy2(src_path, path)
    return path


def poster_rel_path(material_id: str) -> str:
    """payload.meta.poster 形态：素材仓内相对 posix 路径（/media/ 消费面）。"""
    return f"materials/files/{material_id}.poster.jpg"


def probe_video_meta(material_id: str, src_path: str | Path) -> dict[str, Any]:
    """视频元数据探测：ffprobe 时长 + ffmpeg 首帧封面——**缺席/失败如实置空**。

    设计纪律（B3 任务书）：ffmpeg/ffprobe 缺失时不报错（登记本地视频是
    主链路，封面只是增强信息——「宁可空值不可假值」）；探测失败（损坏
    文件/超时/非零退出）同样置空，meta 里不写键（前端按缺省处理）。返回
    {"duration": 秒(float)|None, "poster": 仓内相对路径|None}。"""
    out: dict[str, Any] = {"duration": None, "poster": None}
    src = str(src_path)

    def _bin(name: str) -> str | None:
        """D7 修复（2026-11-26）：launchd 服务 PATH 无 /opt/homebrew/bin——
        裸名 ffprobe/ffmpeg 在 Studio（launchd 常驻）里 OSError 被如实置空。
        解析序：shutil.which（继承 PATH）→ 常见安装路径显式探测。"""
        import shutil

        found = shutil.which(name)
        if found:
            return found
        for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
            cand = f"{d}/{name}"
            if os.path.exists(cand):
                return cand
        return None

    ffprobe_bin = _bin("ffprobe")
    ffmpeg_bin = _bin("ffmpeg")
    try:
        import subprocess

        proc = subprocess.run(
            [ffprobe_bin or "ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", src],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        )
        if proc.returncode == 0:
            try:
                out["duration"] = round(float(proc.stdout.strip().splitlines()[0]), 3)
            except (ValueError, IndexError):
                pass  # 非数字时长（流信息缺失）→ 置空
    except (OSError, subprocess.SubprocessError):
        pass  # ffprobe 缺席/超时——如实置空（主流程继续）
    try:
        import subprocess

        poster = files_dir() / f"{material_id}.poster.jpg"
        poster.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [ffmpeg_bin or "ffmpeg", "-y", "-i", src, "-vframes", "1",
             "-q:v", str(_POSTER_JPEG_QUALITY), str(poster)],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S,
        )
        if proc.returncode == 0 and poster.is_file() and poster.stat().st_size > 0:
            out["poster"] = poster_rel_path(material_id)
        else:
            poster.unlink(missing_ok=True)  # 失败不留半帧文件
    except (OSError, subprocess.SubprocessError):
        pass  # ffmpeg 缺席/超时——如实置空（主流程继续）
    return out
