"""AI 文生图服务（G1）——preview 零落盘 / adopt 人工采纳 / verify 健康三态。

飞轮红线（调研 §4.4-3「AI 只标记不否决」延续，refine F57 同构）：

  - preview(prompt, …)：media_gen.generate_image（唯一出口）→ **服务端临时
    文件**（`~/.musespiral/media_preview/`，A-2 裁决——内存 b64 直传前端会在多张
    1MB+ 图时撑爆前端内存）+ 预览 URL。**零落 materials**（不进
    materials.jsonl / materials/files）；「拒绝即消失」——无拒绝动作面，
    陈旧预览由下次 preview 顺带 GC（PREVIEW_TTL_HOURS）。
  - adopt(preview_id)：人工「存为素材」唯一写面——临时文件**搬正**进
    materials/files（采纳即从 preview 目录消失）+ PNG tEXt 隐式标识
    （GB 45438-2025：prompt/provider/model/generated_at，A-5）+ 落库形状
    `license=AI-GENERATED / meta.ai_generated / chain generate /
    triage.enabled=false`（裁决 1/4：生成物默认不进发现页推荐）。
  - verify()：A-4 连接验证（media_gen.verify_connection 探活）→ health
    三态回写 `~/.musespiral/media_health.json`（源中枢同款语义）。

幂等锚 `generate://sha256:<指纹>`：同图（含 tEXt 后字节）重复采纳 →
dedup=true 返回既有行零追加（upload:// 同纪律）。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import secrets
import time
from typing import Any

from ..core.materials import model
from ..infra import media_store
from ..infra.media_gen import (
    MediaGenError,
    generate_image as _generate_image,
    verify_connection as _verify_connection,
)
from ..jobs.store import now_iso as _now_iso
from . import materials_service

# 虚拟血统源（AI 生成物无上游素材；from_id 非素材 id 的显式标记）
GEN_FROM_ID = "ai"
# 生成素材虚拟源 id（洞察「源贡献」聚合面：AI 生成独立成源，与 upload/clip 同粒度）
GEN_SOURCE_ID = "ai_gen"
# 预览目录（服务端临时文件，A-2；素材文件不进 articles root 纪律同款——
# 预览不是素材，不进 materials/files）
PREVIEW_TTL_HOURS = 24.0
# preview_id 形状（16 位小写 hex；URL 面的穿越/形状门共用）
_PREVIEW_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# tEXt 标识键集（A-5；测试断言面——四键恒在）
TEXT_KEYS: tuple[str, ...] = ("prompt", "provider", "model", "generated_at")


class MediaGenServiceError(ValueError):
    """业务校验错误（可读消息；Studio 转 400）。"""


def preview_dir() -> Any:
    """预览目录 `~/.musespiral/media_preview/`（随 MUSESPIRAL_HOME 隔离解析）。"""
    from .. import config

    return config.home_dir() / "media_preview"


def _gc_stale_previews() -> int:
    """顺带 GC：删超过 TTL 的预览文件（图像+sidecar），返回删除数。

    「拒绝时删」（A-2）的落地形态——单人本地无拒绝端点（refine「放弃不
    调 API」同款），陈旧文件由下一次生成顺带清理；失败静默（GC 不阻断主流程）。
    """
    removed = 0
    cutoff = time.time() - PREVIEW_TTL_HOURS * 3600
    try:
        entries = list(preview_dir().iterdir())
    except OSError:
        return 0
    for path in entries:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


# ── 预览（生成→服务端临时文件+预览 URL；零落 materials）──────────────


def preview(
    prompt: str,
    *,
    size: str = "1024x1024",
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    profile: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """生成一张预览图 → `{preview_id, url, width, height, bytes, format,
    model, provider}`。

    产物只落 `~/.musespiral/media_preview/`（`<id>.<ext>` + `<id>.json` sidecar
    ——采纳时 tEXt/title 的数据面）；materials.jsonl 零追加（红线，测试
    字节断言锚）。MediaGenError 原样上抛（未配置/白名单拒/网络错——
    Studio 降级面 {ok:false}）。
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise MediaGenServiceError("prompt 必须是非空字符串")
    result = _generate_image(
        prompt=prompt, size=size, seed=seed, params=params,
        profile=profile, opener=opener,
    )
    data = result["data"]
    info = media_store.inspect_image(data)  # 8MiB 门/魔数/Pillow verify 复检
    preview_id = secrets.token_hex(8)
    ts = _now_iso()
    sidecar = {
        "prompt": prompt.strip(),
        "provider": result["provider"],
        "model": result["model"],
        "generated_at": ts,
        "size": size,
        "format": info["ext"],
        "width": info["width"],
        "height": info["height"],
        "seed": seed,
    }
    directory = preview_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{preview_id}.{info['ext']}").write_bytes(data)
    (directory / f"{preview_id}.json").write_text(
        json.dumps(sidecar, ensure_ascii=False), encoding="utf-8"
    )
    _gc_stale_previews()
    return {
        "preview_id": preview_id,
        "url": f"/api/media/preview?id={preview_id}",
        "width": info["width"],
        "height": info["height"],
        "bytes": info["bytes"],
        "format": info["ext"],
        "model": result["model"],
        "provider": result["provider"],
        "generated_at": ts,
    }


def read_preview_image(preview_id: str) -> tuple[bytes, str]:
    """预览字节流读取（GET /api/media/preview 服务面）→ (bytes, content_type)。

    形状门（16 hex）+ 魔数复核（不信任扩展名，防 sidecar 目录夹带非图文件）；
    不存在/已 GC/形状非法 → ValueError（400）/FileNotFoundError（404）。
    """
    if not isinstance(preview_id, str) or not _PREVIEW_ID_RE.match(preview_id):
        raise ValueError(f"preview_id 形状非法（16 位 hex）: {preview_id!r}")
    directory = preview_dir()
    for ext in ("png", "jpeg", "gif", "webp"):
        path = directory / f"{preview_id}.{ext}"
        if path.is_file():
            data = path.read_bytes()
            fmt = media_store.sniff_format(data)
            if fmt != ext:
                raise ValueError(f"预览文件魔数与扩展名不符: {preview_id}.{ext}")
            return data, media_store.ALLOWED_FORMATS[ext]
    raise FileNotFoundError(f"预览不存在或已过期: {preview_id}")


# ── 采纳（人工「存为素材」——唯一写面）─────────────────────────────


def _png_bytes_with_text(data: bytes, meta: dict[str, str]) -> bytes:
    """字节 → 带 tEXt 的 PNG（A-5 隐式标识；GB 45438-2025）。

    png 原样重编码携带四键；非 png（jpeg/webp/gif）统一转码 PNG——保证
    **每张**采纳图都带 tEXt（合规标识不因上游格式而缺席）。
    """
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    png_info = PngInfo()
    for key in TEXT_KEYS:
        png_info.add_text(key, str(meta.get(key) or ""))
    try:
        with Image.open(io.BytesIO(data)) as img:
            frame = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img.copy()
            buf = io.BytesIO()
            frame.save(buf, "PNG", pnginfo=png_info)
    except Exception as err:  # noqa: BLE001 - Pillow 异常族统一转可读
        raise MediaGenServiceError(f"生成图标识写入失败: {err}") from None
    return buf.getvalue()


def adopt(preview_id: str, *, title: str = "", fetched_at: str | None = None) -> dict[str, Any]:
    """采纳预览为正式素材：tEXt 标注 → 搬正 materials/files → 落库。

    落库形状（G1 契约）：type=image、license=AI-GENERATED、
    payload.meta.ai_generated=true、provenance.chain=[{from_id:"ai",
    op:"generate"}]、triage.enabled=false（裁决 4：默认不进发现页推荐）。
    预览临时文件随采纳搬正删除（拒绝语义 = 不调本函数，文件由 GC 收）。
    """
    if not isinstance(preview_id, str) or not _PREVIEW_ID_RE.match(preview_id):
        raise MediaGenServiceError(f"preview_id 形状非法（16 位 hex）: {preview_id!r}")
    directory = preview_dir()
    sidecar_path = directory / f"{preview_id}.json"
    if not sidecar_path.is_file():
        raise MediaGenServiceError(f"预览不存在或已过期: {preview_id}")
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise MediaGenServiceError(f"预览元数据损坏: {preview_id}") from None
    if not isinstance(sidecar, dict):
        raise MediaGenServiceError(f"预览元数据损坏: {preview_id}")
    raw = None
    for ext in ("png", "jpeg", "gif", "webp"):
        path = directory / f"{preview_id}.{ext}"
        if path.is_file():
            raw = path.read_bytes()
            break
    if raw is None:
        raise MediaGenServiceError(f"预览图缺失（元数据孤立）: {preview_id}")

    info = media_store.inspect_image(raw)  # 8MiB/魔数/verify（与上传同链）
    png = _png_bytes_with_text(raw, {k: str(sidecar.get(k) or "") for k in TEXT_KEYS})
    if len(png) > media_store.MAX_UPLOAD_BYTES:
        raise MediaGenServiceError(
            f"标注后超过 {media_store.MAX_UPLOAD_BYTES // (1024 * 1024)}MiB 上限，拒绝采纳"
        )
    sha256 = hashlib.sha256(png).hexdigest()
    uri = f"generate://sha256:{sha256}"
    mid = model.make_material_id(GEN_SOURCE_ID, "image", uri)
    # 幂等前置判定（add_uploaded_image 同款）：同图重复采纳命中既有行，
    # 不构造新行——fetched_at 等时间键差异不会触发 immutable 冲突
    existing = materials_service.get_material(mid)
    if existing is not None:
        media_store.store_original(mid, "png", png)  # 内容寻址重写同字节（文件缺失自愈）
        media_store.make_thumbnail(mid, png)
        _drop_preview_files(preview_id)
        return {"ok": True, "id": existing["id"], "material": existing, "dedup": True}
    prompt_text = str(sidecar.get("prompt") or "").strip()
    ts = fetched_at or _now_iso()
    material = model.make_material(
        mtype="image",
        source_id=GEN_SOURCE_ID,
        uri=uri,
        title=(title.strip() if isinstance(title, str) else "")
        or (prompt_text[:40] + ("…" if len(prompt_text) > 40 else ""))
        or f"ai-gen-{sha256[:8]}",
        license="AI-GENERATED",
        payload={
            "local_path": media_store.local_rel_path(mid, "png"),
            "meta": {
                "width": info["width"],
                "height": info["height"],
                "bytes": len(png),
                "format": "PNG",
                "sha256": sha256,
                "ai_generated": True,
                "prompt": prompt_text,
                "provider": str(sidecar.get("provider") or ""),
                "model": str(sidecar.get("model") or ""),
                "generated_at": str(sidecar.get("generated_at") or ts),
            },
        },
        provenance={"chain": [{"from_id": GEN_FROM_ID, "op": "generate"}], "derived_from": []},
        triage={"read": False, "starred": False, "enabled": False, "tags": []},
        fetched_at=ts,
    )
    media_store.store_original(mid, "png", png)
    media_store.make_thumbnail(mid, png)
    try:
        saved = materials_service.add_material(material)
    finally:
        # 搬正收尾：采纳后预览即从临时目录消失（拒绝语义=不调本函数）
        _drop_preview_files(preview_id)
    return {"ok": True, "id": saved["id"], "material": saved, "dedup": False}


def _drop_preview_files(preview_id: str) -> None:
    """删除一组预览临时文件（sidecar + 四扩展位图像；missing_ok 幂等）。"""
    directory = preview_dir()
    (directory / f"{preview_id}.json").unlink(missing_ok=True)
    for ext in ("png", "jpeg", "gif", "webp"):
        (directory / f"{preview_id}.{ext}").unlink(missing_ok=True)


# ── 连接验证（A-4：health 三态回写）─────────────────────────────────


def _health_path() -> Any:
    from .. import config

    return config.home_dir() / "media_health.json"


def verify(*, profile: str | None = None, opener: Any = None) -> dict[str, Any]:
    """探活 media 档案 → `{ok, health:{status, detail, profile, checked_at}}`。

    status ∈ ok/down/unconfigured（三态）；结果回写 `~/.musespiral/
    media_health.json`（源中枢健康灯同款「回写」语义——设置面验证按钮的
    持久数据面）。永不抛错（down 是返回值不是异常）。
    """
    health = _verify_connection(profile=profile, opener=opener)
    path = _health_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "health": health}


def read_health() -> dict[str, Any] | None:
    """上次验证快照（缺文件/坏 JSON → None，读侧宽容）。"""
    try:
        raw = json.loads(_health_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None
