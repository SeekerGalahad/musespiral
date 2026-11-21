"""Studio 环境健康面板数据面（Phase A 自 routing.py 拆出，行为零变更）。

§十七 F20：GET /api/health 只读面板——数据源复用现有探测（config 账号 /
ai.load_ai_config / images.base.load_host_config / browser.cookies /
jobs store / themes / shutil.disk_usage），逐数据源降级（损坏的配置行报
{error: 不可用}，其余信号照常返回）；红线：布尔与名称 only——凭证只报数量、
AI 只报模型名、图床只报主机名，任何 secret 值/凭证形态文本不进响应。
"""
from __future__ import annotations

import shutil
from typing import Any

from .. import __version__, config
from ..core.content import themes
from ..infra.ai import load_ai_config
from ..infra.images.base import load_host_config
from ..browser import cookies
from .actions import _runner
from .security import _StudioContext


# ---- §十七 F20：环境健康面板（只读，布尔与名称 only）----


# Cookie 四平台探测口径（与 upload login --platform 的可登录平台一致）
_HEALTH_COOKIE_PLATFORMS: tuple[str, ...] = ("channels", "douyin", "xhs", "kuaishou")


def _health_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/health：环境健康面板数据面（§十七 F20）。

    数据源全部复用现有探测：config.list_accounts / ai.load_ai_config /
    images.base.load_host_config / browser.cookies.has / jobs store /
    themes.list_all / shutil.disk_usage(root)。
    红线：布尔与名称 only——凭证只报数量、AI 只报模型名、图床只报主机名，
    任何 secret 值/凭证形态文本不进响应（§15.3 凭证永不进响应）。
    """
    # 逐数据源降级：诊断端点不能在被诊断环境坏掉时整体 500（第九期复核
    # low 修复）——损坏的配置行报 {error: 不可用}，其余信号照常返回
    def _probe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception:
            return {"__broken__": True}

    accounts = _probe(config.list_accounts)
    ai_cfg = _probe(load_ai_config)
    host_cfg = _probe(load_host_config)
    rows = _probe(_runner().list, limit=None)
    usage = shutil.disk_usage(str(ctx.root))

    def _broken(v) -> bool:
        return isinstance(v, dict) and v.get("__broken__")

    creds_payload = (
        {"error": "credentials.json 不可读（损坏或格式错误）"}
        if _broken(accounts)
        else {"configured": bool(accounts), "accounts": len(accounts)}
    )
    if _broken(ai_cfg):
        ai_payload = {"error": "ai.json 不可读"}
    else:  # F25：configured 口径=base_url+model（api_key 可空，本地 Ollama）；补档案清单
        from ..infra.ai import list_profiles as _ai_list
        _st = _ai_list()
        ai_payload = {
            "configured": bool(ai_cfg.get("base_url") and ai_cfg.get("model")),
            "model": str(ai_cfg.get("model") or ""),
            "default": _st.get("default"),
            "profiles": sorted(_st.get("profiles") or {}),
        }
    ih_payload = (
        {"error": "imagehost.json 不可读"}
        if _broken(host_cfg)
        else {
            # 无配置缺省回退 local（与 core.images.resolve_host 选择次序同口径）
            "default": str(host_cfg.get("default") or "local"),
            "hosts": sorted(str(name) for name in (host_cfg.get("hosts") or {})),
        }
    )
    jobs_payload = (
        {"error": "jobs.jsonl 不可读"}
        if _broken(rows)
        else {
            "queued": sum(1 for job in rows if job.get("status") == "queued"),
            "running": sum(1 for job in rows if job.get("status") == "running"),
        }
    )
    return {
        "version": __version__,
        "root": str(ctx.root),
        "disk_free_gb": round(usage.free / 2**30, 1),
        "credentials": creds_payload,
        "ai": ai_payload,
        "imagehost": ih_payload,
        "cookies": {p: cookies.has(p) for p in _HEALTH_COOKIE_PLATFORMS},
        "jobs": jobs_payload,
        "themes": {"user_count": sum(1 for _n, src in themes.list_all() if src == "用户")},
    }
