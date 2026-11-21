"""Studio TTS 朗读动作（G-T5，契约 §3.16；正典=《musespiral-调研吸收功能设计-2026-11-18》§六）。

  - POST /api/action/tts_generate `{confirm:true, article, voice?, rate?}`：
    正稿 → 朗读脚本 → edge-tts 合成 MP3（落文章目录）→ **job（kind=`tts_generate`）**
    → `{ok:true, job_id}`。网络长动作进 job 保 UI 响应（同 compose_generate 判例）。
  - GET /api/tts/file?id=<root 内相对路径>：朗读产物字节流（内嵌 `<audio>` 的
    播放面——与 /api/materials/file 同构不同域，白名单只放 .mp3）。

**出口性质**：合成走 `edge_tts` 包的微软免费 TTS 端点（外部服务，非 LLM）——
不经 `infra/ai/gateway`、不进 TASKS/routing/ai.json、零 API key。详见
services/tts_service 模块头。

**400 vs 200{ok:false} 的分界**（契约 §1.3，同 media_actions）：
  - 400 = 请求形状不对（article 缺失/越界/不存在 / voice 不在白名单 /
    rate 形态非法——提交期校验，零 job）；
  - job 内的业务失败（远端不可达、产物异常）落 `job.error`（脱敏），
    前端 trackJob 报错。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.tts.voice import looks_like_mp3, normalize_rate, validate_voice
from .security import (
    _HttpError,
    _StudioContext,
    _query_str,
    _rel,
    _resolve_file,
    _sanitize_error,
)

_MP3_CTYPE = "audio/mpeg"


def _optional_voice(data: dict[str, Any]) -> str | None:
    """voice 选填：白名单外 → 400（提交期零网络拒）。"""
    voice = data.get("voice")
    if voice is None:
        return None
    try:
        return validate_voice(voice)
    except ValueError as err:
        raise _HttpError(400, str(err)) from None


def _optional_rate(data: dict[str, Any]) -> str | None:
    """rate 选填：形态/范围非法 → 400（提交期零网络拒）。"""
    rate = data.get("rate")
    if rate is None:
        return None
    try:
        return normalize_rate(rate)
    except ValueError as err:
        raise _HttpError(400, str(err)) from None


def _action_tts_generate(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/tts_generate：文章 → 朗读 MP3 → job（kind=tts_generate）。"""
    from .actions import _runner, _submit
    from ..services import tts_service

    article = data.get("article")
    if not isinstance(article, str) or not article.strip():
        raise _HttpError(400, "缺少必填字段 article（root 内相对路径）")
    article_rel = article.strip()
    _resolve_file(ctx.root, article_rel)  # 缺文件 404 / 越界 400（形状面）
    voice = _optional_voice(data)
    rate = _optional_rate(data)

    def _body(_job_id: str) -> dict[str, Any]:
        result = tts_service.synthesize_article(
            ctx.root / article_rel, voice or None, rate or None
        )
        # 产物在文章目录内（工作目录边界内）——响应给 root 相对路径供
        # 前端拼 `/api/tts/file?id=`；`bytes/chars` 供 toast 摘要。
        return {
            "output": Path(result["output"]).name,
            "path": _rel(ctx.root, Path(result["output"])),
            "voice": result["voice"],
            "rate": result["rate"],
            "bytes": result["bytes"],
            "chars": result["chars"],
        }

    return _submit(
        ctx,
        "tts_generate",
        {"article": article_rel, "voice": voice, "rate": rate},
        _body,
    )


def _tts_file_response(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET /api/tts/file?id=<rel>：朗读产物的字节流（内嵌 `<audio>` 播放面）。

    三道门：路径边界（_resolve_file——绝对路径/穿越 400、缺失 404）+ 后缀
    白名单（仅 .mp3）+ 魔数复核（防把别的文件改名念）。不打 immutable 缓存头
    ——同文重朗读会覆盖同名产物（重新合成后前端应听到新版本）。
    """
    rel = _query_str(query, "id")
    if rel is None:
        raise _HttpError(400, "缺少必填参数 id（root 内相对路径）")
    path = _resolve_file(ctx.root, rel)
    if path.suffix.lower() != ".mp3":
        raise _HttpError(404, f"不是可播放的朗读产物（仅 .mp3）: {rel}")
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise _HttpError(404, _sanitize_error(exc, ctx.root)) from None
    if not looks_like_mp3(body):
        raise _HttpError(404, f"文件不是 MP3（内容与后缀不符）: {rel}")
    return 200, _MP3_CTYPE, body


__all__ = ["_action_tts_generate", "_tts_file_response"]
