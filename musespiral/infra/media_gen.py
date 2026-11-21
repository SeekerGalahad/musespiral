"""AI 文生图唯一出口（G1，调研 §四/§五裁决/§七 A-1~A-4）——L1 基础设施。

**唯一出口纪律（调研 §4.4-2「零出网纪律延伸」）**：本模块是媒体生成的
唯一 HTTP 出口——host 白名单（https 且 ∈ PROVIDERS 域集）+ api_key 只进
Authorization 头（不进 URL/请求体/日志/异常文本）。任何新增的图像生成
出网路径必须收敛到本模块，不得出现第二个直连点（gateway 同款审查纪律）。

档案制（ai.json 双命名空间，2026-11-17 评审修正）：media 域档案落
`media_profiles` + `media_default`（profiles.py media 节）——**不进 chat
TASKS/routing**（media 走 /images/generations 不经 chat()，进 TASKS 会被
ai check 用 chat 端点误探测、被 routing 误路由）。档案三元组
base_url/api_key/model 与 chat 域同构（「供应商可转换」裁决直接适用）；
PROVIDERS 给出两家预设（SiliconFlow 先行 / DashScope 留档案位），host
白名单由预设表派生——单一事实源。

返回形态（A-1 双形态）：请求恒显式 `response_format=b64_json`；部分模型
无视该参数只回 URL——`data[0].url` 走下载兜底（`fetcher.assert_public_url`
SSRF 门 + https + 8MiB 字节门 + 魔数复核，不信任 Content-Type）。

参数面（A-3）：`seed` 可选（复现微调的关键参数）+ `params` 透传 dict
（Open WebUI 扩展位同款——未来 steps/negative_prompt 免改 schema）；
`model`/`prompt`/`response_format` 三键为安全键，透传不覆盖。

超时 120s（与 chat 同量级）。失败抛 MediaGenError（可读中文；异常文本
永不携带 api_key——canary 红线，tests/test_media_gen.py 断言零回显）。
"""
from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import urlsplit

from . import fetcher
from .ai.profiles import (
    load_media_config,
    load_media_profile,
    resolve_media_profile_name,
)
from .media_store import MAX_UPLOAD_BYTES, sniff_format

# 生成超时（秒）：图像生成比 chat 慢一档（扩散模型秒级~十秒级），与 chat
# 同取 120s（调研 §七 G1 工作流契约「超时 120s」）
TIMEOUT_SECONDS = 120

# 连接验证探测超时（秒）：/models 是轻请求，快速失败防 UI 灯长挂
VERIFY_TIMEOUT_SECONDS = 15

# media 域供应商预设（单一事实源：CLI `musespiral media config` 的 base_url 与
# host 白名单都从这里派生）。DashScope 为 OpenAI 兼容端点（compatible-mode）。
PROVIDERS: dict[str, str] = {
    "siliconflow": "https://api.siliconflow.cn/v1",
    "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}

# host 白名单 = 预设表的域集（精确匹配；scheme 恒 https 由 _assert_endpoint 另查）
ALLOWED_HOSTS: frozenset[str] = frozenset(
    urlsplit(url).hostname or "" for url in PROVIDERS.values()
)


def _now_iso() -> str:
    """ISO 秒级时间戳（与 jobs.store.now_iso 同口径；gateway 同款本地副本——
    本模块不 import jobs 避免层间耦合）。"""
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


class MediaGenError(RuntimeError):
    """媒体生成业务错误（可读消息；Studio 转 {ok:false} 降级面）。"""


def assert_media_endpoint(url: str) -> None:
    """生成端点白名单门（调研红线：https 且 host ∈ ALLOWED_HOSTS）。

    media 档案手滑填了 chat 域端点（如 deepseek）时在此拦截——白名单外
    主机/非 https 一律拒绝（零网络请求，提交期语义）。
    """
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise MediaGenError(f"生成端点 URL 非法: {exc}") from None
    host = parsed.hostname or ""
    if parsed.scheme != "https":
        raise MediaGenError(
            f"生成端点必须是 https（零出网纪律）: {parsed.scheme or '(空)'}://{host}"
        )
    if host not in ALLOWED_HOSTS:
        known = "/".join(sorted(PROVIDERS))
        raise MediaGenError(
            f"生成端点主机不在白名单: {host}（允许: {known}；"
            f"白名单外主机禁止直连——唯一出口纪律）"
        )


def _resolve(profile: str | None) -> tuple[dict[str, Any], str]:
    """取生效 media 档案（profile 显式覆盖 > media_default）。

    返回 (档案原文, 档案名)；未配置 → MediaGenError（引导 CLI 配置）。
    恒在 media 命名空间取——chat 域 profiles/routing 不参与（评审修正：
    media 不经 chat()，chat 档案（如 deepseek）从根上无机会被当生成档案）。
    """
    if profile:
        cfg = load_media_profile(profile)
        name = profile
        if not cfg:
            raise MediaGenError(f"media 档案不存在: {profile}")
    else:
        name = resolve_media_profile_name() or ""
        cfg = load_media_config()
    if not (cfg.get("base_url") and cfg.get("model")):
        raise MediaGenError(
            "媒体生成未配置。请先运行: musespiral media config --api-key <KEY>"
            " [--model FLUX.1-schnell] [--provider siliconflow|dashscope]"
        )
    return cfg, name


def _http_json(
    url: str,
    *,
    method: str,
    headers: dict[str, str],
    body: bytes | None = None,
    timeout: int = TIMEOUT_SECONDS,
    opener: Any = None,
) -> tuple[int, bytes]:
    """HTTP 收发底座（urllib，零新 pip 依赖）——测试的接缝点（opener 注入）。

    返回 (status, body_bytes)；网络层异常 → MediaGenError（文本含状态码或
    异常类摘要，**永不携带请求头/URL 查询串**——api_key 只进 Authorization）。
    """
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        resp = (opener or urllib.request).urlopen(req, timeout=timeout)
        with resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        detail = b""
        try:
            detail = e.read()[:300]
        except Exception:  # noqa: BLE001 - 读错误体失败不影响状态码呈现
            pass
        text = detail.decode("utf-8", "replace").strip()
        raise MediaGenError(f"生成服务返回 {e.code}: {text}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise MediaGenError(f"调用生成服务失败: {e.__class__.__name__}") from None


def _download_image(url: str, *, opener: Any = None) -> bytes:
    """URL 形态下载兜底（A-1）：SSRF 门 + https + 8MiB 字节门 + 魔数复核。

    不信任 Content-Type（以字节魔数为准）；超限/白名单外/非图片一律
    MediaGenError。下载域 = 任意公共 CDN（生成服务的文件域不固定），
    门 = fetcher.assert_public_url（与采集取数同一道 SSRF 纪律）+ https。
    """
    try:
        fetcher.assert_public_url(url)
    except fetcher.RefusedError as exc:
        raise MediaGenError(f"生成结果 URL 被拒（SSRF 门）: {exc}") from None
    if urlsplit(url).scheme != "https":
        raise MediaGenError("生成结果 URL 必须 https")
    status, data = _http_json(
        url, method="GET", headers={}, timeout=TIMEOUT_SECONDS, opener=opener
    )
    if status != 200:
        raise MediaGenError(f"生成结果下载失败: HTTP {status}")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaGenError(
            f"生成结果超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MiB 上限: {len(data)} 字节"
        )
    if not sniff_format(data):
        raise MediaGenError("生成结果不是受支持的图片格式（png/jpeg/gif/webp）")
    return data


def generate_image(
    *,
    prompt: str,
    size: str = "1024x1024",
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    profile: str | None = None,
    opener: Any = None,
) -> dict[str, Any]:
    """一次文生图调用 → `{data, format, provider, model}`。

    OpenAI 风格 `POST {base_url}/images/generations`；请求恒带
    `response_format: b64_json`（A-1），URL-only 模型自动走下载兜底。
    `params` 透传进请求体（A-3），但 `model`/`prompt`/`response_format`
    为安全键不被覆盖。api_key 只进 Authorization 头。
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise MediaGenError("prompt 必须是非空字符串")
    cfg, name = _resolve(profile)
    url = str(cfg["base_url"]).rstrip("/") + "/images/generations"
    assert_media_endpoint(url)  # 白名单外 → 拒（零网络请求）

    body: dict[str, Any] = {
        "model": cfg["model"],
        "prompt": prompt.strip(),
        "size": size,
        "n": 1,
        "response_format": "b64_json",
    }
    if seed is not None:
        body["seed"] = int(seed)
    if params:
        body.update(params)
    # 安全键后置强制：透传不覆盖（response_format 双形态契约的根基）
    body["model"] = cfg["model"]
    body["prompt"] = prompt.strip()
    body["response_format"] = "b64_json"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    status, raw = _http_json(
        url,
        method="POST",
        headers=headers,
        body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        timeout=TIMEOUT_SECONDS,
        opener=opener,
    )
    if status != 200:
        raise MediaGenError(f"生成服务返回 {status}")
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
        item = (payload.get("data") or [{}])[0]
    except (json.JSONDecodeError, IndexError, AttributeError) as exc:
        raise MediaGenError(f"生成响应格式异常: {exc.__class__.__name__}") from None

    b64 = item.get("b64_json") if isinstance(item, dict) else None
    link = item.get("url") if isinstance(item, dict) else None
    if isinstance(b64, str) and b64.strip():
        try:
            data = base64.b64decode(b64)
        except (ValueError, TypeError) as exc:
            raise MediaGenError(f"生成结果 b64 解码失败: {exc.__class__.__name__}") from None
    elif isinstance(link, str) and link.strip():  # URL-only 模型兜底（A-1）
        data = _download_image(link.strip(), opener=opener)
    else:
        raise MediaGenError("生成响应缺 b64_json/url（双形态都不在）")
    fmt = sniff_format(data)
    if not fmt:
        raise MediaGenError("生成结果不是受支持的图片格式（png/jpeg/gif/webp）")
    return {"data": data, "format": fmt, "provider": name, "model": str(cfg["model"])}


def verify_connection(*, profile: str | None = None, opener: Any = None) -> dict[str, Any]:
    """连接验证（A-4）：探测 `{base_url}/models` → health 三态。

    返回 `{status: "ok"|"down"|"unconfigured", detail, profile}`——
    「unconfigured」是第三态（未配置不是故障，不红灯）；探测走同一道
    白名单门（白名单外档案 → down 带理由，零网络请求）。
    """
    if profile:
        cfg = load_media_profile(profile)
        name = profile
    else:
        name = resolve_media_profile_name() or ""
        cfg = load_media_config()
    if not (cfg.get("base_url") and cfg.get("model")):
        return {
            "status": "unconfigured",
            "detail": "媒体档案未配置（musespiral media config --api-key …）",
            "profile": name,
            "checked_at": _now_iso(),
        }
    url = str(cfg["base_url"]).rstrip("/") + "/models"
    try:
        assert_media_endpoint(url)
    except MediaGenError as exc:
        return {
            "status": "down",
            "detail": str(exc),
            "profile": name,
            "checked_at": _now_iso(),
        }
    headers: dict[str, str] = {}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    try:
        status, _raw = _http_json(
            url,
            method="GET",
            headers=headers,
            timeout=VERIFY_TIMEOUT_SECONDS,
            opener=opener,
        )
    except MediaGenError as exc:
        return {
            "status": "down",
            "detail": str(exc),
            "profile": name,
            "checked_at": _now_iso(),
        }
    if status == 200:
        return {"status": "ok", "detail": "", "profile": name, "checked_at": _now_iso()}
    return {
        "status": "down",
        "detail": f"探测返回 HTTP {status}（鉴权或档案疑失效）",
        "profile": name,
        "checked_at": _now_iso(),
    }
