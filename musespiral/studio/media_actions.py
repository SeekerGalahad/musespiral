"""Studio G1 媒体动作（文生图三动作 + 预览字节流）+ F1 无（本文件只挂 G1）。

  - POST /api/action/generate_image：同步生成（refine preview 同判例——
    图像生成秒级~十秒级、**不建 job**（调研 §7.1「图像同步不建 job（正确）」；
    job result 持久化=预览留痕，违反「拒绝即消失不留痕」）。MediaGenError
    → 200 {ok:false} 降级面（AI 未配置/白名单拒/网络错，同 refine AIError 面）。
  - POST /api/action/adopt_generated：同步瞬时本地搬正（零网络）。
  - POST /api/action/media_verify：探活三态，永不 4xx/5xx 业务失败。
  - GET /api/media/preview?id=：预览字节流（A-2 服务端临时文件的消费面——
    前端 <img src> 直指，不过 b64；与 /api/materials/file 同构不同域）。

confirm 闸门由 server.py 全局收口（§1.2）；参数校验 400 在本层，业务
失败 200 {ok:false} 在服务层（错误双层语义 §1.3）。
"""
from __future__ import annotations

import re
from typing import Any

from ..infra.media_gen import MediaGenError
from ..services import media_gen_service
from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _query_str,
    _sanitize_error,
)

# 尺寸形状（WxH，边 2-5 位数字——SiliconFlow/OpenAI 风格 size 串）
_SIZE_RE = re.compile(r"^\d{2,5}x\d{2,5}$")
# preview_id 形状（与 media_gen_service._PREVIEW_ID_RE 同款；HTTP 面独立声明）
_PREVIEW_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _optional_size(data: dict[str, Any]) -> str:
    size = data.get("size", "1024x1024")
    if not isinstance(size, str) or not _SIZE_RE.match(size.strip()):
        raise _HttpError(400, f"size 必须是 WxH 形态（如 1024x1024）: {size!r}")
    return size.strip()


def _optional_seed(data: dict[str, Any]) -> int | None:
    seed = data.get("seed")
    if seed is None:
        return None
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise _HttpError(400, "seed 必须是整数（可省略）")
    return seed


def _optional_params(data: dict[str, Any]) -> dict[str, Any] | None:
    params = data.get("params")
    if params is None:
        return None
    if not isinstance(params, dict) or not all(
        isinstance(k, str) and k.strip() for k in params
    ):
        raise _HttpError(400, "params 必须是对象（键为非空字符串，值透传生成 API）")
    return params


def _action_generate_image(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/generate_image {prompt, size?, seed?, params?}。

    prompt 必填（400）；size/seed/params 形状校验（400）；生成失败
    （未配置/白名单/网络/响应异常）→ 200 {ok:false}（脱敏降级面）。
    """
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise _HttpError(400, "缺少必填字段 prompt（生成提示词）")
    size = _optional_size(data)
    seed = _optional_seed(data)
    params = _optional_params(data)
    try:
        result = media_gen_service.preview(
            prompt, size=size, seed=seed, params=params
        )
    except media_gen_service.MediaGenServiceError as err:
        raise _HttpError(400, str(err)) from None
    except MediaGenError as err:
        return _error_payload(err, ctx.root)
    return {"ok": True, **result}


def _action_adopt_generated(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/adopt_generated {preview_id, title?}。

    形状/存在性 → 400（preview_id 16hex / 预览过期可读错误）；成功返回
    {ok, id, material, dedup}（落库形状见 media_gen_service.adopt）。
    """
    preview_id = data.get("preview_id")
    if not isinstance(preview_id, str) or not _PREVIEW_ID_RE.match(preview_id):
        raise _HttpError(400, f"preview_id 必须是 16 位 hex: {preview_id!r}")
    title = data.get("title", "")
    if title is not None and not isinstance(title, str):
        raise _HttpError(400, "title 必须是字符串或省略")
    try:
        return media_gen_service.adopt(preview_id, title=title or "")
    except media_gen_service.MediaGenServiceError as err:
        raise _HttpError(400, str(err)) from None


def _action_media_verify(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/media_verify {}：连接验证（A-4，health 三态回写）。

    down/unconfigured 是返回值不是异常（源中枢 verify 同语义）；响应含
    last 快照（上次验证时间，设置面灯的持久数据面）。
    """
    result = media_gen_service.verify()
    result["last"] = media_gen_service.read_health() or None
    return result


def _media_preview_response(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET /api/media/preview?id=：预览图字节流（Content-Type 按魔数）。

    形状非法 → 400；不存在/已 GC → 404；魔数不符 → 400（三道门与
    /api/materials/file 同纪律，域=media_preview 目录）。预览是临时态，
    不发 immutable 缓存头（与素材内容寻址语义相反——同 id 不会复用）。
    """
    preview_id = _query_str(query, "id")
    if preview_id is None:
        raise _HttpError(400, "缺少必填参数 id")
    try:
        body, ctype = media_gen_service.read_preview_image(preview_id)
    except FileNotFoundError as exc:
        raise _HttpError(404, _sanitize_error(exc, ctx.root)) from None
    except ValueError as exc:
        raise _HttpError(400, _sanitize_error(exc, ctx.root)) from None
    return 200, ctype, body
