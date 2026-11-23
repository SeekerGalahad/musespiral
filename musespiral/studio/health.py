"""Studio 环境健康面板数据面（Phase A 自 routing.py 拆出，行为零变更）。

§十七 F20：GET /api/health 只读面板——数据源复用现有探测（config 账号 /
ai.load_ai_config / images.base.load_host_config / browser.cookies /
jobs store / themes / shutil.disk_usage），逐数据源降级（损坏的配置行报
{error: 不可用}，其余信号照常返回）；红线：布尔与名称 only——凭证只报数量、
AI 只报模型名、图床只报主机名，任何 secret 值/凭证形态文本不进响应。

W1-B1（2026-11-23，正典=《musespiral-运维发布改良设计-2026-11-23》§二 W1-B1）：
新增轻量 GET /api/healthz —— 恰五键 {ok, version, uptime_s, jobs_pending,
materials_total}，供 ctl.sh smoke / 外部探针消费。为什么不扩 /api/health：
该端点九键形状已被前端四处消费（core.js 磁盘低警 / settings 面板 / insight /
article 状态栏），改成五键即破坏 UI 且违反「前端零字节」验收；新的轻面不碰
旧面，零新安全面（同只读、同回环）。版本取 importlib.metadata（安装态口径，
非源码常量——生产 runtime 升级后 healthz 才说真话）。
"""
from __future__ import annotations

import importlib.metadata
import shutil
import time
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

# W1-B1：server 启动时刻（模块级常量——healthz 的 uptime_s 基准）。
# 模块首次 import 即记录：与 make_server 同进程，serve 前装配耗时一并计入
# （探针要的是「这进程活了多久」，装配时间本就属进程寿命）。
_SERVER_START_TS: float = time.time()


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


# ---- W1-B1：轻量探针面（五键，ctl.sh smoke / 外部探针消费）----


def _installed_version() -> str:
    """安装态包版本（importlib.metadata）；未安装（源码直跑）回退 __version__。

    生产 runtime 是 PyPI 安装态——探针要看「实际装的是哪版」，源码常量
    在生产环境不参与判定，故 metadata 优先、回退兜底保证源码树可跑。
    """
    try:
        return importlib.metadata.version("musespiral")
    except importlib.metadata.PackageNotFoundError:  # 未安装态（源码直跑测试）
        return __version__


def _healthz_payload() -> dict[str, Any]:
    """GET /api/healthz：五键轻量探针（W1-B1）。

    jobs_pending=队列未完成数（queued/running），复用 _runner().list 与
    /api/health 的 jobs 段同源（经 runner 取数即触发孤儿恢复，与
    /api/jobs 同语义）；materials_total=素材索引活跃行数，复用
    materials_service.list_materials（与 /api/materials total 同源，
    零 jsonl 二次解析）。两处均降级宽容：探针面不得因被诊断环境坏掉而 500。
    """
    from ..services import materials_service  # 函数体内导入（导入期解耦）

    def _probe(fn, fallback):
        try:
            return fn()
        except Exception:
            return fallback

    rows = _probe(lambda: _runner().list(limit=None), [])
    pending = sum(1 for job in rows if job.get("status") in ("queued", "running"))
    total = _probe(lambda: len(materials_service.list_materials()), 0)
    return {
        "ok": True,
        "version": _installed_version(),
        "uptime_s": round(max(0.0, time.time() - _SERVER_START_TS), 1),
        "jobs_pending": pending,
        "materials_total": total,
    }
