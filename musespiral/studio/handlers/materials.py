"""materials 域动作：/api/materials/* + /api/sources/* + /api/ingest/*（15 条）。

R-2 自 actions.py 按域迁入（行为零变更）：8 个执行体（triage/delete/
register/collections/sources_verify/sources_enabled/sources_register/ingest
+ _ensure_radarhub_preset 前置件）原样迁移；委托路由（from-chapters/
ingest 三动作）执行体在既有 book_actions/ingest_actions，此处仅登记。
N-2 素材闭环批（2026-11-21）增三执行体：spark / from-article /
sources/internal/refresh（本文件内实现）。
job 提交复用共享管道 `_runner`（同包 platform 域持有，actions re-export
面不变）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..book_actions import _action_from_chapters  # R3+R3.5：选章入素材库 → job book_fetch
from ..ingest_actions import (  # B 采集扩展（第二十一期）：/api/ingest/* 三动作
    _action_ingest_clip,
    _action_ingest_crawl,
    _action_ingest_refresh,
)
from ..security import (
    _error_payload,
    _http_400_on,
    _HttpError,
    _require_confirm,
    _require_str,
    _StudioContext,
)
from .platform import _runner


def _ensure_radarhub_preset(sid: str) -> None:
    """radarhub 读侧预置条目（GET /api/settings 合成面）首次动作时固化为
    权威 sources.json（install_radarhub 幂等）——合成→权威切换。"""
    from ...services import sources_service

    if sid == "radarhub" and sources_service.get_source(sid) is None:
        sources_service.install_radarhub()


def _action_materials_triage(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/triage {id, starred?/read?/enabled?/tags?}：triage 窄门。

    第二十一期 §1.2 修订：人工动作只改 triage 四键（三布尔 + tags 列表，
    tags 为 list[str] 全量替换语义）。键集钉死在 HTTP 面即生效——越键
    （如 payload/provenance）→ 400 可读错误（窄门不宽；D 档 §二-F77.4/
    §五-F77 锚「越键写入 4xx」，此前请求体白名单过滤会静默丢弃越键）；
    四键值校验由 services.triage.triage_patch 拒（ValueError → 400）；
    至少携带一键，否则 400（空补丁无意义）。响应 triage 四键全回。
    """
    mid = _require_str(data, "id")
    overkey = [k for k in data if k not in ("id", "confirm", "starred", "read", "enabled", "tags")]
    if overkey:
        raise _HttpError(
            400,
            f"triage 只允许改 ('read', 'starred', 'enabled', 'tags') 四键，拒绝: {sorted(overkey)!r}",
        )
    changes = {k: data[k] for k in ("starred", "read", "enabled", "tags") if k in data}
    if not changes:
        raise _HttpError(400, "缺少分诊键（starred/read/enabled/tags 至少一项）")
    from ...services import materials_service  # 函数体内导入（导入期解耦）

    with _http_400_on(ValueError):
        row = materials_service.update_triage(mid, changes)
    return {"ok": True, "id": mid, "triage": row.get("triage") or {}}


def _action_materials_delete(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/delete {id}：追加墓碑行，返回连坐清单（非级联删除）。

    两步 confirm 的第二步后的执行面；连坐预览（第一步声明数据）走 GET
    /api/materials/detail 的 cascade 字段——本动作返回的是删除时点清单。
    """
    mid = _require_str(data, "id")
    from ...services import materials_service

    with _http_400_on(ValueError):
        result = materials_service.delete_material(mid)
    return {"ok": True, "deleted": mid, "cascade": result["cascade"]}


def _action_materials_verify(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/verify {id, verified?}：人工核验标记（N-4）。

    标记/取消同端点（body verified=false 即取消；缺省 true=标记）——写
    triage.verified（人工核验布尔，与 AI 自评 confidence 正交不混写：
    「AI 自评和人工核验必须可区分」）。同步瞬时（triage 窄门追加一行），
    走 services.update_triage（triage_patch 五键窄门；confirmed 幂等零追加）。
    素材不存在/已删、verified 非布尔 → 400 可读。响应 triage 五键全回。
    """
    mid = _require_str(data, "id")
    verified = data.get("verified", True)
    if not isinstance(verified, bool):
        raise _HttpError(400, f"verified 必须是布尔值: {verified!r}")
    from ...services import materials_service  # 函数体内导入（导入期解耦）

    with _http_400_on(ValueError):
        row = materials_service.update_triage(mid, {"verified": verified})
    return {
        "ok": True,
        "id": mid,
        "verified": row.get("triage", {}).get("verified") is True,
        "triage": row.get("triage") or {},
    }


def _action_materials_register(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/register {url, title?, note?, duration_text?}（F61）。

    视频登记制（URL+元信息）：不下载、不解码、无预览帧、**零出网**——
    登记纯本地落库。URL 门（scheme/userinfo/回环内网）与选填字段 ≤200
    字符在服务层提交期校验（ValueError → 400 可读）；同 URL（规范化后）
    再登记 → dedup=true 返回既有行（幂等，零追加）。
    """
    url = _require_str(data, "url")
    # 选填三键：缺省空串（显式给非 str 值由服务层拒绝——400）
    optional = {
        key: (data[key] if key in data else "") for key in ("title", "note", "duration_text")
    }
    from ...services import materials_service

    with _http_400_on(ValueError):
        result = materials_service.register_video(url, **optional)
    return {"ok": True, **result}


def _action_materials_collections(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/collections {action, …}（F62 合并动作端点，五 action）。

    create {name, color} / add {id, member_ids 全量替换} / remove {id,
    member_ids 差集} / rename {id, name, color?} / delete {id 仅解除组织
    关系，成员素材不动}。校验（name ≤24、color 五色枚举、member_ids 元素
    存在性等）在 collections_service（ValueError → 400 可读）。
    """
    action = data.get("action")
    from ...services import collections_service

    if action == "create":
        if "name" not in data or "color" not in data:
            raise _HttpError(400, "create 需要必填字段 name 与 color（五色枚举）")
        with _http_400_on(ValueError):
            row = collections_service.create_collection(data["name"], data["color"])
        return {"ok": True, "collection": row}
    if action in ("add", "remove"):
        cid = _require_str(data, "id")
        if "member_ids" not in data:
            raise _HttpError(400, f"{action} 需要必填字段 member_ids（素材 id 列表）")
        with _http_400_on(ValueError):
            if action == "add":
                row = collections_service.add_members(cid, data["member_ids"])
            else:
                row = collections_service.remove_members(cid, data["member_ids"])
        return {"ok": True, "collection": row}
    if action == "rename":
        cid = _require_str(data, "id")
        if "name" not in data:
            raise _HttpError(400, "rename 需要必填字段 name")
        with _http_400_on(ValueError):
            row = collections_service.rename_collection(cid, data["name"], data.get("color"))
        return {"ok": True, "collection": row}
    if action == "delete":
        cid = _require_str(data, "id")
        with _http_400_on(ValueError):
            result = collections_service.delete_collection(cid)
        return {"ok": True, **result}
    raise _HttpError(400, "action 必须是 create/add/remove/rename/delete 之一")


def _action_sources_probe(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/sources/probe {type, config}：登记前「测试连接」（零落盘）。

    探测失败返回 state=down + 人话原因（不 400/不 500）——对话框要显示它。
    """
    stype = _require_str(data, "type")
    config = data.get("config")
    if config is not None and not isinstance(config, dict):
        raise _HttpError(400, "config 必须是对象")
    from ...services import sources_service

    return sources_service.probe_source(stype, config or {})


def _action_sources_verify(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/sources/verify {id}：手动验证一条源（健康探测回写，无自动轮询）。"""
    sid = _require_str(data, "id")
    from ...services import sources_service

    _ensure_radarhub_preset(sid)
    with _http_400_on(ValueError):
        health = sources_service.check_health(sid)
    return {"ok": True, "id": sid, "health": health}


def _action_sources_enabled(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/sources/enabled {id, enabled}：源启停（禁用源不进采集循环）。"""
    sid = _require_str(data, "id")
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        raise _HttpError(400, "enabled 必须是布尔值")
    from ...services import sources_service

    _ensure_radarhub_preset(sid)
    with _http_400_on(ValueError):
        row = sources_service.set_enabled(sid, enabled)
    return {"ok": True, "id": sid, "enabled": row.get("enabled") is True}


def _action_ingest(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/action/ingest {source_id | all:true}：源刷新进 job 层（kind=ingest）。

    单步刷新此源 / 刷新全部（正典 §1.3/F54）；articles_root=ctx.root（与
    /api/materials 的 drafted 推导同根约定）。radar-hub 宕机是 job 内的
    正常返回值（status=down，非 failed）。
    """
    from ...services import ingest_service

    runner = _runner()
    if data.get("all") is True:

        def _run_all(_job_id: str) -> dict[str, Any]:
            return {"results": ingest_service.refresh_all(articles_root=ctx.root)}

        job_id = runner.submit("ingest", {"all": True}, _run_all)
        return {"ok": True, "job_id": job_id}
    sid = _require_str(data, "source_id")
    _ensure_radarhub_preset(sid)

    def _run_one(_job_id: str) -> dict[str, Any]:
        return ingest_service.refresh_source(sid, articles_root=ctx.root)

    job_id = runner.submit("ingest", {"source_id": sid}, _run_one)
    return {"ok": True, "job_id": job_id}


def _action_materials_spark(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/spark {text, title?}：灵感速记（N-2 P-1 §五）。

    只需 text（一句话极简创建）：type=text + meta.sub_type=spark + origin=
    manual + triage.read=false 全部自动填（裁决：不新增 MATERIAL_TYPES）。
    同一句话重发 → dedup=true 返回既有行（幂等，零追加）。title 选填
    （缺省取全文前 24 字）。text 缺失/空白 → 400。
    """
    text = _require_str(data, "text")
    title = data.get("title") if isinstance(data.get("title"), str) else ""
    from ...services import materials_service

    with _http_400_on(ValueError):
        result = materials_service.add_spark(text, title=title)
    return {"ok": True, **result}


def _action_materials_from_article(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/from-article {slug, mode, start_line?, end_line?}：文章反哺。

    mode 三路径（N-2 §3.2）：full=整篇归档 / excerpt=行区间摘取
    （start_line/end_line 1-based 双闭，对剥除 frontmatter 后的正文行计）/
    distill=AI 抽 3-5 条核心论点 → 多条 spark。参数/文章缺失类错误 → 400
    （ValueError 窄门）；AI 未配置或调用失败 → 200 {ok:false} 降级面
    （compose 动作同款——环境态不是请求错误）。articles_root=ctx.root
    （Studio 工作目录即文章根，与 compose 会话同约定）。
    """
    slug = _require_str(data, "slug")
    mode = data.get("mode")
    if not isinstance(mode, str) or mode.strip() not in ("full", "excerpt", "distill"):
        raise _HttpError(400, "mode 必须是 full/excerpt/distill 之一")
    from ...services import materials_service

    try:
        result = materials_service.add_from_article(
            slug,
            mode.strip(),
            articles_root=ctx.root,
            start_line=data.get("start_line"),
            end_line=data.get("end_line"),
        )
    except ValueError as exc:
        raise _HttpError(400, str(exc)) from None
    except Exception as exc:  # AIError 等：可读降级面，不 500
        return _error_payload(exc, ctx.root)
    return {"ok": True, **result}


def _action_sources_internal_refresh(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/sources/internal/refresh {id, include_drafts?}：内部源刷新。

    扫 config.scan_root 文章目录产素材（幂等按 slug 去重——连跑两次零新
    增）。include_drafts：请求体布尔优先，缺省读源 config 同名键，再缺省
    true。非 internal 源 / scan_root 缺失或非目录 → 400 可读。成功回写
    health.last_refresh（F68 刷新收尾既有惯例）。
    """
    sid = _require_str(data, "id")
    from ...services import materials_service, sources_service

    source = sources_service.get_source(sid)
    if source is None:
        raise _HttpError(400, f"源不存在: {sid!r}")
    if source.get("type") != "internal":
        raise _HttpError(
            400, f"源 type 必须是 internal: {sid!r}（实际 type={source.get('type')!r}）"
        )
    cfg = source.get("config") or {}
    scan_root = cfg.get("scan_root")
    if not isinstance(scan_root, str) or not scan_root.strip():
        raise _HttpError(400, f"internal 源缺 config.scan_root（文章根目录）: {sid!r}")
    include_drafts = data.get("include_drafts")
    if not isinstance(include_drafts, bool):
        include_drafts = cfg.get("include_drafts")
    if not isinstance(include_drafts, bool):
        include_drafts = True
    with _http_400_on(ValueError):
        result = materials_service.refresh_internal_articles(
            sid, scan_root, include_drafts=include_drafts
        )
    sources_service.mark_refresh(sid)
    return {"ok": True, **result}


def _action_sources_register(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/sources {id,type,label,config,enabled?,subscriptions?,ai_config?}：注册/更新源。

    F53「源连接」区块两段式接入的写面：registry.validate_source 把关（kebab
    id/type 枚举/radar_hub 回环钉死——SSRF 纪律），本层补类型相关最小完备
    （dir 必带 config.path、kb 必带 config.root、rss 必带 config.url、
    internal 必带 config.scan_root——N-2 2026-11-21）。同 id upsert（更新语义）。

    `ai_config`（N-3 源级 AI 配置）：请求体带该键 → 整体替换（{} 即清空）；
    不带 → 保留在册源的既有配置（注册表单/启停路径不必回传，防误清空）。
    类型/边界违规经 registry 校验 → 400 可读错误。
    """
    from ...services import sources_service

    # 2026-11-24 A5（源中枢重设计批）：id 缺省时由 service 层自动生成
    # （auto_source_id——前端新表单不再要求手填 kebab id）；显式传入仍走原校验。
    sid = data.get("id") if isinstance(data.get("id"), str) and data.get("id").strip() else None
    if sid is not None:
        data = dict(data)
        data["id"] = sid
    stype = data.get("type")
    if not isinstance(stype, str) or not stype.strip():
        raise _HttpError(400, "缺少必填字段 type（radar_hub/kb/dir/rss）")
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        raise _HttpError(400, "缺少必填字段 config（对象）")
    if stype == "dir" and not (isinstance(cfg.get("path"), str) and cfg["path"].strip()):
        raise _HttpError(400, "dir 源必须配置 config.path（目录绝对路径）")
    if stype == "kb" and not (isinstance(cfg.get("root"), str) and cfg["root"].strip()):
        raise _HttpError(400, "kb 源必须配置 config.root（知识库根绝对路径）")
    if stype == "rss" and not (isinstance(cfg.get("url"), str) and cfg["url"].strip()):
        raise _HttpError(400, "rss 源必须配置 config.url（http(s) 订阅地址）")
    if stype == "internal" and not (
        isinstance(cfg.get("scan_root"), str) and cfg["scan_root"].strip()
    ):
        raise _HttpError(400, "internal 源必须配置 config.scan_root（文章根目录绝对路径）")
    label = data.get("label")
    subscriptions = data.get("subscriptions")
    row = {
        "id": sid,  # None → service 层 auto_source_id 生成（A5）
        "type": stype.strip(),
        "label": label.strip() if isinstance(label, str) and label.strip() else None,
        "enabled": data["enabled"] if isinstance(data.get("enabled"), bool) else True,
        "config": cfg,
        "subscriptions": subscriptions if isinstance(subscriptions, list) else [],
    }
    if "ai_config" in data:
        row["ai_config"] = data.get("ai_config")
    elif sid:  # 自动 id 是新源，无从保留旧 AI 配置
        existing = sources_service.get_source(sid)
        if existing is not None and existing.get("ai_config"):
            row["ai_config"] = existing["ai_config"]
    with _http_400_on(ValueError):
        saved = sources_service.register_source(row)
    return {"ok": True, "source": saved}


def _action_materials_reindex(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/materials/reindex {confirm:true}（ARCH-5）：全量重建素材向量索引。

    同步执行（ThreadingHTTPServer 下仅本请求阻塞；素材 ≤10000 的设计规模
    秒级~分钟级）。幂等：已新鲜（同模型+同文本哈希）的条目零重嵌；顺带
    清理非活跃 id 残留行。embedding 缺位（Ollama 未运行/未拉模型）是环境
    态——200 ok:false 带安装引导（from-article 的 AI 降级面先例），不 5xx。
    """
    _require_confirm(data)
    from ...infra.embedding import EmbeddingError
    from ...services import search_service  # 函数体内导入（导入期解耦）

    try:
        result = search_service.rebuild_index()
    except EmbeddingError as exc:
        return _error_payload(exc, ctx.root)
    return {"ok": True, **result}


# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/materials/triage": _action_materials_triage,  # 第二十期 M3：素材分诊窄门（本期扩 tags 四键）
    "/api/materials/delete": _action_materials_delete,  # M3：删除=墓碑+连坐清单（两步 confirm 第二步后）
    "/api/materials/register": _action_materials_register,  # A 扩展 F61：视频登记（零出网幂等）
    "/api/materials/collections": _action_materials_collections,  # A 扩展 F62：手工集合五动作（GET/POST 双态）
    "/api/materials/from-chapters": _action_from_chapters,  # R3+R3.5：选章入素材库 → job book_fetch
    "/api/sources/probe": _action_sources_probe,  # 2026-11-24：登记前测试连接（零落盘）
    "/api/sources/verify": _action_sources_verify,  # M3：源健康手动验证回写
    "/api/sources/enabled": _action_sources_enabled,  # M3：源启停
    "/api/sources": _action_sources_register,  # M4：源注册/更新（F53 两段式接入写面）
    "/api/action/ingest": _action_ingest,  # M3：源刷新 job（单源/全部）
    "/api/ingest/refresh": _action_ingest_refresh,  # B 扩展 F65/F68：采集面刷新（rss 分派）
    "/api/ingest/clip": _action_ingest_clip,  # B 扩展 F66：剪藏 preview/adopt 两形态
    "/api/ingest/crawl": _action_ingest_crawl,  # B 扩展 F67：手动粘贴 → pending 区（无自动抓取）
    # N-2 素材闭环三端点（2026-11-21，正典=《musespiral-素材闭环与专业编辑器设计-
    # 2026-11-18.md》§五 P-1）：灵感速记 / 文章反哺三路径 / 内部源刷新。
    # 契约锁 POST 66→69（同步瞬时——扫本地目录与 AI 单次提炼均不走 job）。
    "/api/materials/spark": _action_materials_spark,
    "/api/materials/from-article": _action_materials_from_article,
    "/api/sources/internal/refresh": _action_sources_internal_refresh,
    # N-4 人工核验（2026-11-21，正典=《总体方案》§二 N-4）：标记/取消同端点
    # （body verified=false 即取消）。契约锁 POST 72→73。
    "/api/materials/verify": _action_materials_verify,
    # ARCH-5 语义基础设施（2026-11-22，正典=《极限架构与功能扩展总规划
    # -2026-11-21》§ARCH-5 第 5 件）：素材向量索引全量重建（confirm 闸门；
    # 幂等重嵌 + 清理残留行；embedding 缺位 200 ok:false 安装引导）。
    # 契约锁 POST 73→74。
    "/api/materials/reindex": _action_materials_reindex,
}
