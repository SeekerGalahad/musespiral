"""信息源注册编排 + 健康三态 + 订阅表达式解析（第二十期 §1.1/F53-F55）。

services 层单一持有者面：sources.json 的全部业务动作（注册/启停/健康
回写）经本模块编排，落盘归 infra/sources/registry，KB 级联回退复用
config 既有语义——本层不直读 sources.json 文件。

健康三态（正典 §1.1 health）：
  unknown 从未探测（注册缺省）；ok/down 由显式「验证」/源刷新回写
  （正典 §四-Q1：健康快照只在显式验证/刷新时回写，无自动轮询）。
  M1 探测面：kb=config.root 存在性、dir=config.path 存在性（本地零
  HTTP）；radar_hub 探测归 M2 只读客户端（四端点 /api/health），M1 保持
  unknown 不回写。down 是正常返回值不是异常（宕机=灰灯不阻塞，正典
  §三-Q2 语义在服务面先立）。

订阅表达式（正典 §1.1 subscriptions / F55）：保存查询表达式形如
  "line=embedded & grade=S & 未生成骨架"
——"&" 分隔；段含 "=" 为键值条件，裸词条为模糊条件；求值（对 radar-hub
实时数据）归 M2，本层先立解析与可读错误（正典 §五 sources 面）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..infra.sources import radarhub, registry
from ..jobs.store import now_iso as _now_iso


def kb_root(explicit: str | Path | None = None) -> Path | None:
    """KB 根级联解析（单一权威）：显式参数 > sources.json kb 条目 config.root
    > config.knowledge_root()（kb_root.txt/kb_sources.json 读侧兼容序）。

    materials 评分（_kb_root_for_scoring 原实现）与 refine 正文提取
    （_kb_root_for_refine 原实现）两处逐字同款级联收编于此（行为零变更）。
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    for source in list_sources():
        if source.get("type") == "kb":
            raw = (source.get("config") or {}).get("root")
            if isinstance(raw, str) and raw.strip():
                return Path(raw).expanduser()
    return config.knowledge_root()


# ── 注册编排（upsert / 查询 / 启停）──────────────────────────────────


def list_sources() -> list[dict[str, Any]]:
    """源清单（级联回退读；origin 细节走 registry.load_sources 原始返回）。"""
    return registry.load_sources()["sources"]


def get_source(source_id: str) -> dict[str, Any] | None:
    """按 id 取源；不存在（含合成面缺席）返回 None。"""
    for source in list_sources():
        if source["id"] == source_id:
            return source
    return None


def register_source(source: dict[str, Any]) -> dict[str, Any]:
    """注册/更新一条源（upsert：同 id 覆盖，id 集唯一）。

    写入校验（kebab id / type 枚举 / radar_hub 回环钉死）由 registry.
    validate_source 把关；首次落盘会把读侧合成面（kb 条目）一并固化——
    合成→权威的自然切换，无独立迁移步骤。
    """
    registry.validate_source(source)
    normalized = registry.normalize_source(source)
    assert normalized is not None  # validate 已保证 id 非空 str
    # N-3：ai_config upsert 语义「带键整体替换（{} 清空）/ 不带键保留在册」
    # （启停/验证路径只回传既有行、注册表单不重填，均不应误清空）——validate
    # 已通过后仍以源 source 的「键是否存在」为准保留。
    if "ai_config" not in source:
        existing = get_source(normalized["id"])
        if existing is not None and existing.get("ai_config"):
            normalized["ai_config"] = existing["ai_config"]
    current = [s for s in list_sources() if s["id"] != normalized["id"]]
    current.append(normalized)
    registry.save_sources(current)
    return normalized

def resolve_ai_config(source_id: str | None) -> dict[str, Any]:
    """N-3 读侧解析：某源的 ai_config 覆盖（无源/无配置 → {}）。

    **只读侧**：本函数的输出只允许流向素材加工（digest/synthesis/tag_suggest）
    的 chat_policed 调用点，**严禁**流向 compose generate（写作=创作者意志，
    不被源配置绑架——正典《总体方案》N-3 裁决）。消费方按需把返回字典解包
    成 profile/model/temperature/prompt_override 四参数，缺省回落全局。

    profile 指向已删除/未配置的档案时**丢弃该键**（回落全局路由）——读侧
    宽容纪律：坏配置不阻断加工，ai.json 侧 misconfig 由设置页可见。
    """
    if not isinstance(source_id, str) or not source_id.strip():
        return {}
    source = get_source(source_id)
    if source is None:
        return {}
    config = registry.clean_ai_config(source.get("ai_config"))
    profile = config.get("profile")
    if profile is not None:
        from ..infra.ai.profiles import load_profile

        if not load_profile(profile):
            config.pop("profile", None)
    return config

def ai_overrides(materials: list[dict[str, Any]]) -> dict[str, Any]:
    """素材集合的源级 AI 覆盖（**多素材只在同源时生效**，否则 {} 回落全局）。

    多源综合（synthesis N 篇来自不同源）没有「一个源配置」语义——取哪条都
    是武断，故只在全部素材 origin.source_id 相同（含空集）时解析。单素材
    调用方直接传 [material]。
    """
    if not materials:
        return {}
    sids = {(m.get("origin") or {}).get("source_id") for m in materials}
    if len(sids) != 1:
        return {}
    sid = next(iter(sids))
    return resolve_ai_config(sid) if isinstance(sid, str) else {}

def override_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """ai_config → chat 调用关键字（profile/model/temperature 三键，缺省不填）。

    消费方用法：`chat_fn(messages, task=..., **sources_service.override_kwargs(cfg))`
    ——缺键即回落全局口径（client/gateway 各自有缺省值）。
    """
    out: dict[str, Any] = {}
    for key in ("profile", "model", "temperature"):
        if key in config:
            out[key] = config[key]
    return out

def override_template(config: dict[str, Any], fallback: str) -> str:
    """生效模板：源级 prompt_override > fallback（prompts.d 覆盖 > 内置缺省）。

    源级最具体，优先级最高——「这个源的内容特性需要特定处理」比全局覆盖
    更贴切。调用方按自身「有材料插槽则原位注入」纪律消费该文本。
    """
    text = config.get("prompt_override")
    return text.strip() if isinstance(text, str) and text.strip() else fallback


def install_radarhub() -> dict[str, Any]:
    """radar-hub 连接安装（F53：预置一条，enabled 默认 true）。

    幂等：radarhub 已在册（任意形态）直接返回在册条目零改写。
    """
    existing = get_source("radarhub")
    if existing is not None:
        return existing
    return register_source(registry.default_radarhub_source())


def set_enabled(source_id: str, enabled: bool) -> dict[str, Any]:
    """启停一条源（triage 语义的源侧对应面；禁用源不进采集循环）。"""
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    target = get_source(source_id)
    if target is None:
        raise ValueError(f"源不存在: {source_id!r}")
    target["enabled"] = enabled
    return register_source(target)


def remove_source(source_id: str) -> dict[str, Any]:
    """移除一条源（注册表摘除；素材不连坐——origin.source_id 留档）。"""
    target = get_source(source_id)
    if target is None:
        raise ValueError(f"源不存在: {source_id!r}")
    rest = [s for s in list_sources() if s["id"] != source_id]
    registry.save_sources(rest)
    return target


# ── 健康三态（显式探测回写）─────────────────────────────────────────


def check_health(source_id: str) -> dict[str, Any]:
    """探测一条源并回写健康快照（返回回写后的 health 对象）。

    - kb：config.root 存在 → ok（回写 last_ok）；未配置/缺失 → down
      （保留 last_ok——上次正常时间不因本次失败抹掉）；
    - dir：config.path 存在性同上；
    - internal（N-2 2026-11-21）：config.scan_root 存在性同上（本地零
      HTTP——文章根目录探活，刷新面另经 refresh_internal_articles）；
    - radar_hub：四端点客户端探测 /api/health（M2 接驳）——载荷 ok 即 ok、
      down/宕机即 down；未配置 base 是注册面不可能态（validate_source 拒收），
      宽容按 down 处理不回写。
    down 是返回值不是异常——宕机=灰灯+上次快照，不阻塞其他源（正典 F55）。
    """
    source = get_source(source_id)
    if source is None:
        raise ValueError(f"源不存在: {source_id!r}")
    if source["type"] == "radar_hub":
        base = (source.get("config") or {}).get("base")
        if not isinstance(base, str) or not base.strip():
            return dict(source["health"])  # 不可能态：宽容保持快照不回写
        health = radarhub.RadarHubClient(base).health()
        return mark_health(source_id, "ok" if health.get("ok") is True else "down")
    # 本地目录型探活键（kb→root / dir→path / internal→scan_root；其余类型
    # 落 "path" 兜底——既有行为零变更）
    key = {"kb": "root", "dir": "path", "internal": "scan_root"}.get(source["type"], "path")
    raw = (source.get("config") or {}).get(key)
    path = Path(str(raw)) if isinstance(raw, str) and raw.strip() else None
    ok = path is not None and path.is_dir()
    health = dict(source["health"])
    if ok:
        health["status"] = "ok"
        health["last_ok"] = _now_iso()
    else:
        health["status"] = "down"  # last_ok 保留
    source["health"] = health
    register_source(source)
    return dict(health)


def mark_health(source_id: str, status: str) -> dict[str, Any]:
    """显式回写健康状态（M2 radarhub 客户端探测结果的落点）。

    status ∈ ok/down/unknown；ok 时 last_ok 由本函数生成，down/unknown
    保留既有 last_ok。M1 测试与 M2 客户端共用本面——服务层不感知探测
    方式，只管状态机。
    """
    if status not in registry.HEALTH_STATUSES:
        raise ValueError(f"status 必须是 {registry.HEALTH_STATUSES} 之一: {status!r}")
    source = get_source(source_id)
    if source is None:
        raise ValueError(f"源不存在: {source_id!r}")
    health = dict(source["health"])
    health["status"] = status
    if status == "ok":
        health["last_ok"] = _now_iso()
    source["health"] = health
    register_source(source)
    return dict(health)


def mark_refresh(
    source_id: str, *, status: str | None = None, crawl_delay: int | None = None
) -> dict[str, Any]:
    """刷新收尾回写（B 采集扩展 §1.1/F68）：health.last_refresh 记最近一次
    刷新尝试（ok/down 皆写——due 判定数据面）；status 给出时同写健康态
    （ok 照 mark_health 语义补 last_ok）；crawl_delay（秒）给出时抬升
    下次 due 判定。单次 register_source 写入（sources.json 单写点）。
    """
    source = get_source(source_id)
    if source is None:
        raise ValueError(f"源不存在: {source_id!r}")
    health = dict(source["health"])
    health["last_refresh"] = _now_iso()
    if status is not None:
        if status not in registry.HEALTH_STATUSES:
            raise ValueError(f"status 必须是 {registry.HEALTH_STATUSES} 之一: {status!r}")
        health["status"] = status
        if status == "ok":
            health["last_ok"] = health["last_refresh"]
    if crawl_delay is not None:
        health["crawl_delay"] = int(crawl_delay)
    source["health"] = health
    register_source(source)
    return dict(health)


# ── 订阅表达式解析（求值归 M2 收编后端）─────────────────────────────


# ── 订阅表达式求值（F55：对 radar-hub 实时数据；纯函数，M2）──────────

# 「未生成骨架」特殊词条（正典 §1.1 订阅示例原文）：不是文本模糊条件，
# 而是 drafted 过滤——命中正稿 frontmatter.source 指回的信号视为已消化。
DRAFTED_TERM = "未生成骨架"
# 求值面认识的键值条件键（line 过滤雷达线、grade 过滤信号评级；其余键
# 拒绝——不静默丢条件）
EVALUATION_KEYS: tuple[str, ...] = ("line", "grade")


def line_matches(conditions: list[dict[str, Any]], radar_id: str) -> bool:
    """line 条件对一条雷达线的判定（子串、大小写不敏感；无条件=全过）。

    多个 line 条件按 AND（同一订阅内同时命中两线名的子串）；radar_id
    空串只在无条件时通过。
    """
    ok = True
    for cond in conditions:
        if cond.get("key") == "line":
            ok = ok and cond["value"].lower() in radar_id.lower()
    return ok


def item_matches(
    conditions: list[dict[str, Any]],
    item: dict[str, Any],
    *,
    drafted: set[str] | None = None,
) -> bool:
    """条件列表对一条 radar 信号项的判定（AND 语义；纯函数零 I/O）。

    item 取 scored.json 条目形状：repo/name/description/category/grade/url。
      - grade=<G>（可逗号多值 "S,A"）：item.grade 大小写不敏感命中任一值；
      - 裸词条：repo/name/description/category 四字段子串模糊（大小写不敏感）；
      - 裸词条 == DRAFTED_TERM（「未生成骨架」）：item.url 不在 drafted 集
        才通过——drafted=None（调用方未提供）时 ValueError（不可静默放行）；
      - 其余键值条件键：ValueError（可读错误——已知键 {line, grade}，line
        在雷达线阶段已消化，进到条目层的 line 视为冗余通过）。
    """
    for cond in conditions:
        if "key" in cond:
            key = cond["key"]
            if key not in EVALUATION_KEYS:
                raise ValueError(f"订阅条件键未知: {key!r}（求值面认识 {EVALUATION_KEYS}）")
            if key == "line":
                continue  # 雷达线阶段已过滤；条目层冗余通过
            wanted = [v.strip().upper() for v in str(cond["value"]).split(",") if v.strip()]
            if str(item.get("grade") or "").upper() not in wanted:
                return False
            continue
        term = str(cond.get("term") or "")
        if term == DRAFTED_TERM:
            if drafted is None:
                raise ValueError(f"订阅词条 {DRAFTED_TERM!r} 需要 drafted 集（调用方未提供）")
            if str(item.get("url") or "") in drafted:
                return False
            continue
        hay = " ".join(
            str(item.get(k) or "") for k in ("repo", "name", "description", "category")
        ).lower()
        if term.lower() not in hay:
            return False
    return True


def parse_subscription(expr: str) -> list[dict[str, Any]]:
    """解析订阅表达式：`&` 分隔的条件列表（正典 §1.1 示例形态）。

    段含 "=" → {"key","value"}（键值条件，如 line=embedded / grade=S）；
    裸词条 → {"term"}（模糊条件，如 未生成骨架）。非法输入 raise
    ValueError（可读错误，正典 §五）：空表达式、空条件段、空键/空值。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("订阅表达式不能为空")
    conditions: list[dict[str, Any]] = []
    for i, part in enumerate(expr.split("&")):
        seg = part.strip()
        if not seg:
            raise ValueError(f"订阅表达式第 {i + 1} 段为空（连续 && 或首尾 &）: {expr!r}")
        if "=" in seg:
            key, _, value = seg.partition("=")
            key, value = key.strip(), value.strip()
            if not key:
                raise ValueError(f"订阅表达式第 {i + 1} 段条件键为空: {seg!r}")
            if not value:
                raise ValueError(f"订阅表达式第 {i + 1} 段条件值为空: {seg!r}")
            conditions.append({"key": key, "value": value})
        else:
            conditions.append({"term": seg})
    return conditions


# ── 迁移件一：kb_sources.json → sources.json 条目（幂等）────────────


def migrate_kb_sources() -> dict[str, Any]:
    """kb_sources.json 固化为 sources.json 条目（迁移三件之一，M4 执行）。

    幂等以「sources.json 落盘且含 kb 条目」为完成标志——读侧合成面
    （sources.json 缺席时的回退）不算已迁移，本函数把合成结果固化为
    权威文件；重复执行（文件已在册含 kb）零改写。固化后 kb_sources.json
    保留只读兼容一个版本期（正典 §1.2 迁移表），本函数不删旧文件。
    """
    loaded = registry.load_sources()
    authoritative = loaded["origin"] == registry.SOURCES_FILE
    existing = next((s for s in loaded["sources"] if s["type"] == "kb"), None)
    if authoritative and existing is not None:
        return {"migrated": False, "reason": "kb 源已在册（sources.json 已固化）", "source": existing}
    entry = registry.synthesize_kb_source()
    # 文件在册但缺 kb 条目 → 补一条并保留既有源；合成面 → 首次固化只落 kb
    registry.save_sources([*loaded["sources"], entry] if authoritative else [entry])
    return {"migrated": True, "source": entry}
