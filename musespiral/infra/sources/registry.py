"""信息源注册表 sources.json 读写（第二十期 §1.1，infra/storage 纪律）。

单一持有者面：本模块是 `~/.musespiral/sources.json` 的唯一读写实现，
services/sources_service 是它的唯一编排方（正典 §1.1「单一持有者：
services/sources_service.py」——服务层编排、本层落盘，与 kb_sources.json
/config 的分工同构）。

读序级联（F46 回退逻辑泛化，正典 §1.1 迁移行）：
  1. sources.json 存在 → 权威（"sources" 数组全量）；
  2. 缺席 → 合成：kb_sources.json（→ kb_root.txt → 内置默认源表）级联
     成一条 type=kb 条目（config={root, sources}——继承 F46 双持有语义），
     origin 标注 "synthesized" 供 UI/CLI 展示。

回环钉死（SSRF 纪律延伸，正典 §三）：radar_hub 源的 config.base 经
validate_base_url 校验——host 非 127.0.0.1 一律 ValueError（localhost/
::1/内网域名均拒；字面钉死不留白名单）。
"""
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ... import config

# 注册表文件名（config.home_dir() 下）
SOURCES_FILE = "sources.json"

# 源类型枚举（B 采集扩展 §1.1：rss 本期开闸——feedparser 投产；
# N-2 2026-11-21 增 internal——musespiral 自产文章库，scan_root 指文章根，
# 刷新经 materials_service.refresh_internal_articles 扫目录产素材）
SOURCE_TYPES: tuple[str, ...] = ("radar_hub", "kb", "dir", "rss", "internal")

# rss 源 config 缺省（B 正典 §二 F65-C-1：interval 360 分钟/max_items 50）
RSS_DEFAULT_INTERVAL_MINUTES = 360
RSS_DEFAULT_MAX_ITEMS = 50
RSS_INTERVAL_BOUNDS = (15, 10000)
RSS_MAX_ITEMS_BOUNDS = (1, 500)

# 健康三态（正典 §1.1 health.status；unknown=从未探测）
HEALTH_STATUSES: tuple[str, ...] = ("ok", "down", "unknown")

# ── N-3 源级 AI 配置（读侧覆盖，2026-11-20）────────────────────────────
# sources.json 每源可选 ai_config（四键全可选）；语义=「这个源的内容特性需要
# 特定处理」——只在素材加工（digest/synthesis/tag_suggest）时覆盖全局 AI
# 配置，**绝不影响文章生成**（写作是创作者意志，正典《总体方案》N-3 裁决）。
# 校验归本层（写侧窄门），读侧归一归 clean_ai_config（宽容：坏值丢弃回落全局）。
AI_CONFIG_KEYS: tuple[str, ...] = ("profile", "model", "temperature", "prompt_override")
AI_TEMPERATURE_BOUNDS = (0.0, 2.0)

# radar-hub 连接预置（F53：安装时预置一条，enabled 默认 true；回环钉死）
DEFAULT_RADARHUB_BASE = "http://127.0.0.1:8787"


def sources_path() -> Path:
    """sources.json 路径（随 MUSESPIRAL_HOME 解析——测试 monkeypatch env 即隔离）。"""
    return config.home_dir() / SOURCES_FILE


def default_radarhub_source() -> dict[str, Any]:
    """radar-hub 预置源（F53 安装时写入；每次新 dict 防调用方污染常量）。"""
    return {
        "id": "radarhub",
        "type": "radar_hub",
        "label": "雷达枢纽",
        "enabled": True,
        "config": {"base": DEFAULT_RADARHUB_BASE},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": [],
    }


def synthesize_kb_source() -> dict[str, Any]:
    """kb_sources.json 级联 → 一条 type=kb 源条目（读侧回退合成）。

    复用 config.load_kb_sources()（kb_sources.json → kb_root.txt → 内置
    默认源表三级级联，F46 语义零变更）：root 与子源表（chips 面板数据）
    一并入 config——kb 源刷新（F54）与 Studio chips 继续从同一语义取数。
    root 未配置落 None（health 探测据此判 down，不猜路径）。
    """
    kb = config.load_kb_sources()
    return {
        "id": "kb",
        "type": "kb",
        "label": "知识库",
        "enabled": True,
        "config": {"root": kb.get("root"), "sources": kb.get("sources") or []},
        "health": {"status": "unknown", "last_ok": None},
        "subscriptions": [],
    }


def clean_ai_config(raw: Any) -> dict[str, Any]:
    """ai_config 读侧归一（宽容）：非 dict → {}；逐键类型校验，坏值静默丢弃。

    与 writes 侧 validate_ai_config 的分工：这里是**读**（脏文件不崩、坏值
    回落全局），那里是**写**（违规 raise ValueError、UI 立刻得到 400）。
    四键口径：profile/model/prompt_override 非空 str 才保留；temperature 为
    int/float（非 bool）且落在 [0.0,2.0] 才保留——越界/字符串数字都丢弃。
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("profile", "model", "prompt_override"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value
    temp = raw.get("temperature")
    if (not isinstance(temp, bool)) and isinstance(temp, (int, float)):
        low, high = AI_TEMPERATURE_BOUNDS
        if low <= float(temp) <= high:
            out["temperature"] = float(temp)
    return out

def validate_ai_config(raw: Any) -> dict[str, Any]:
    """ai_config 写侧校验（违规 → ValueError 可读错误）：返回归一后的 dict。

    缺省/空 dict → {}（合法：清空源级配置）；四键全可选。类型违规 vs 边界
    违规都 raise——写侧不接受「静默丢弃」，用户填错要立刻看到原因。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("ai_config 必须是对象（四键 profile/model/temperature/prompt_override）")
    unknown = sorted(set(raw) - set(AI_CONFIG_KEYS))
    if unknown:
        raise ValueError(f"ai_config 未知键: {unknown!r}（允许 {AI_CONFIG_KEYS}）")
    out: dict[str, Any] = {}
    for key in ("profile", "model", "prompt_override"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError(f"ai_config.{key} 必须是字符串，实际: {type(value).__name__}")
        if value.strip():
            out[key] = value.strip()
    temp = raw.get("temperature")
    if temp is not None:
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            raise ValueError(f"ai_config.temperature 必须是数值，实际: {temp!r}")
        low, high = AI_TEMPERATURE_BOUNDS
        if not low <= float(temp) <= high:
            raise ValueError(f"ai_config.temperature 必须是 {low}-{high} 之间，实际: {temp!r}")
        out["temperature"] = float(temp)
    return out

def normalize_source(row: Any) -> dict[str, Any] | None:
    """源条目形状归一：非 dict / id 非非空 str → None 丢弃（宽容读）。

    合法骨架补默认（enabled/health/subscriptions 缺键不崩读侧）；config
    保持原样（类型相关，本层不猜形状——类型校验归 validate_source）。
    ai_config 走 clean_ai_config 读侧归一（缺失 → {}，坏值静默丢弃）。
    """
    if not isinstance(row, dict):
        return None
    sid = row.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    out = dict(row)
    out["id"] = sid.strip()
    out.setdefault("label", sid.strip())
    if not isinstance(out.get("enabled"), bool):
        out["enabled"] = True
    health = out.get("health")
    health = dict(health) if isinstance(health, dict) else {}
    status = health.get("status")
    health["status"] = status if status in HEALTH_STATUSES else "unknown"
    health.setdefault("last_ok", None)
    # B 采集扩展 §1.1 增键：last_refresh=最近一次刷新尝试（含失败，F68 due
    # 判定数据面）；crawl_delay=最近一次 robots crawl-delay（秒，抬升下限）
    health.setdefault("last_refresh", None)
    health.setdefault("crawl_delay", None)
    out["health"] = health
    if out.get("type") == "rss":
        cfg = out.get("config")
        if isinstance(cfg, dict):
            cfg.setdefault("interval_minutes", RSS_DEFAULT_INTERVAL_MINUTES)
            cfg.setdefault("max_items", RSS_DEFAULT_MAX_ITEMS)
    subs = out.get("subscriptions")
    out["subscriptions"] = list(subs) if isinstance(subs, list) else []
    cfg = out.get("config")
    out["config"] = dict(cfg) if isinstance(cfg, dict) else {}
    # N-3：源级 AI 配置读侧归一（缺失/坏值 → {} 回落全局）
    out["ai_config"] = clean_ai_config(out.get("ai_config"))
    return out


def validate_base_url(base: str) -> str:
    """radar_hub base 回环钉死：host 非 127.0.0.1 一律 ValueError。

    urlparse 解析后 hostname 严格等于 "127.0.0.1"（大小写归一由
    urlparse 的 hostname 属性保证）；localhost / [::1] / 内网域名 / 缺
    scheme / 空 host 全拒——SSRF 纪律延伸（正典 §三依赖规则），不留
    白名单。合法原样返回。
    """
    if not isinstance(base, str) or not base.strip():
        raise ValueError("radar_hub base 不能为空")
    parsed = urlparse(base.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"radar_hub base 必须是 http(s) URL: {base!r}")
    if parsed.hostname != "127.0.0.1":
        raise ValueError(f"radar_hub base 必须钉死 127.0.0.1 回环（SSRF 纪律）: {base!r}")
    return base.strip()


def validate_rss_url(url: str) -> str:
    """rss 源订阅地址形状门：http(s)/无 userinfo/非 IP 字面量。

    B 正典 §二 F65-C-1：IP 字面量（私网/回环/公网一律）注册期即拒；域名
    解析期 egress 复检归 infra/fetcher 权威。合法原样返回。
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("rss 源必须配置 config.url（http(s) 订阅地址）")
    raw = url.strip()
    try:
        parsed = urlparse(raw)
        parsed.port  # 端口合法性（ValueError 向上）
    except ValueError as exc:
        raise ValueError(f"rss url 非法: {exc}") from None
    if parsed.scheme not in ("http", "https"):
        raise ValueError("rss url 必须是 http(s) URL 且不含用户凭据")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("rss url 必须是 http(s) URL 且不含用户凭据")
    host = parsed.hostname
    if not host:
        raise ValueError("rss url 必须是 http(s) URL 且不含用户凭据")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("rss url 不能使用 IP 字面量（请用域名）")
    return raw


def validate_source(source: dict[str, Any]) -> None:
    """源条目写入校验：违规 raise ValueError（注册/更新共用）。

      - id：kebab-case（小写字母/数字/连字符，字母开头）；
      - type ∈ SOURCE_TYPES（rss 本期开闸，B 正典 §1.1）；
      - radar_hub：config.base 必填且过 validate_base_url（回环钉死）；
      - rss：config.url 必填且过 validate_rss_url；interval_minutes ∈
        [15,10000] 整数、max_items ∈ [1,500] 整数（缺省 normalize 补）；
      - kb / dir / internal：base 键不得出现（回环钉死只属 radar_hub）；
        dir 的 config.path 必填（2026-11-24 dir 采集落地批，只校验必填、
        不猜存在性——存在性归健康探测/刷新期）；internal 的 config.scan_root
        必填校验归 studio 注册动作层；
      - subscriptions 每项为非空 str（表达式语法解析归服务层）。
    """
    sid = source.get("id")
    if not isinstance(sid, str) or not sid.strip():
        raise ValueError("源 id 必须是非空字符串")
    sid = sid.strip()
    if not all(part.isalnum() and part.isascii() for part in sid.split("-")) or not sid[0].isalpha():
        raise ValueError(f"源 id 必须是 kebab-case（小写字母/数字/连字符，字母开头）: {sid!r}")
    if sid != sid.lower():
        raise ValueError(f"源 id 必须小写: {sid!r}")
    stype = source.get("type")
    if stype not in SOURCE_TYPES:
        raise ValueError(f"源 type 必须是 {SOURCE_TYPES} 之一: {stype!r}")
    cfg = source.get("config") or {}
    if stype == "radar_hub":
        base = cfg.get("base")
        if not isinstance(base, str) or not base.strip():
            raise ValueError("radar_hub 源必须配置 config.base")
        validate_base_url(base)
    elif stype == "rss":
        validate_rss_url(str(cfg.get("url") or ""))
        for key, (low, high) in (
            ("interval_minutes", RSS_INTERVAL_BOUNDS),
            ("max_items", RSS_MAX_ITEMS_BOUNDS),
        ):
            value = cfg.get(key)
            if value is None:
                continue  # 缺省 normalize_source 补
            if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                raise ValueError(f"{key} 必须是 {low}-{high} 的整数")
    elif "base" in cfg:
        raise ValueError(f"type={stype} 源不接受 config.base（回环钉死只属 radar_hub）")
    elif stype == "dir":
        path = cfg.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("dir 源必须配置 config.path（本地目录绝对路径）")
    subs = source.get("subscriptions")
    if subs is not None:
        if not isinstance(subs, list):
            raise ValueError("subscriptions 必须是列表")
        for i, sub in enumerate(subs):
            if not isinstance(sub, str) or not sub.strip():
                raise ValueError(f"subscriptions[{i}] 必须是非空字符串")
    # N-3：ai_config 写侧窄门（四键类型/边界；未知键拒收防拼写静默失效）
    if "ai_config" in source:
        validate_ai_config(source.get("ai_config"))


def load_sources() -> dict[str, Any]:
    """读源注册表全口径（级联回退）。

    返回 {"sources": [...], "origin": "sources.json" | "synthesized"}；
    sources.json 坏 JSON → 视同缺席走合成（人工可编辑文件，宽容——与
    _read_kb_sources_file 同纪律）；合成只含 kb 条目（radar-hub 预置走
    安装动作显式写入，不掺进读侧合成，正典 §1.1 迁移行边界）。
    """
    path = sources_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None
    if isinstance(raw, dict):
        rows = raw.get("sources")
        if isinstance(rows, list):
            sources = [s for s in (normalize_source(r) for r in rows) if s is not None]
            return {"sources": sources, "origin": SOURCES_FILE}
    return {"sources": [synthesize_kb_source()], "origin": "synthesized"}


def save_sources(sources: list[dict[str, Any]]) -> Path:
    """写 sources.json（每条先过 validate_source；父目录自建）。

    调用方负责合并语义（upsert/去重）；本函数是纯落盘面。返回落盘路径。
    """
    for source in sources:
        validate_source(source)
    data = {"sources": list(sources)}
    path = sources_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
