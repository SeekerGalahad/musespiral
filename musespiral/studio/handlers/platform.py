"""platform 域动作：/api/action/* + /api/jobs/* + /api/settings/* + 其余（22 条）。

R-2 自 actions.py 按域迁入（行为零变更）；行数门拆分（≤400）后三类成员：
  - 执行体函数（_action_adapt/_action_batch_adapt/_action_pack/
    _action_publish/_action_status/_action_job_cancel）原样迁移；
    discover 三动作（sync_stats/discover_draft/star 家族）与 AI 辅助三动作
    （cover/refine/tags_suggest）分出 handlers/discover.py、handlers/assist.py；
  - 共享管道（_RUNNER/_runner/_reset_runner/_submit/_assert_asset_bounds/
    _assert_fetch_url）——兄弟动作模块既有 `from .actions import …` 惰性
    导入面经 actions re-export 保持不变（discover/assist 同款 `from
    .platform import _submit` 取用，materials 判例）；
  - 委托路由（fetch/media/pipeline/tts/booksource/book/settings/agent）：
    执行体在各自既有模块（Phase A 起拆出，测试按文件路径/属性锚定），此处
    仅登记进 ROUTES。
publish_flow 补丁锚点契约（studio/__init__ §七：patch 请打在
musespiral.studio.actions.publish_flow 上）：本模块不自持 import，执行体经
actions 命名空间晚绑定读取（_action_publish 函数体内 `from .. import
actions`），补丁语义与拆分前完全一致。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..agent_actions import _action_agent_adopt, _action_agent_run
from ..book_actions import _action_book_search, _action_bookengine_demo
from ..booksource_actions import (  # R0 书源功能（契约 §3.9）：五动作
    _action_import,
    _action_import_apply,
    _action_refresh,
    _action_subs_add,
    _action_subs_del,
)
from ...core.article import MANUAL_SETTABLE
from ...core.article import set_status as _article_set_status
from ...core.content.parser import ParsedArticle, parse_article
from ...core.quality.checks import has_failures
from ...core.quality.render_checks import _collect_image_srcs
from ..fetch_actions import _action_fetch  # §十七 F21：微信文章 URL 抓取草稿
from ...infra.sources import url_fetch
from ...jobs.runner import JobFailure, JobRunner
from ..media_actions import (  # G1 文生图（2026-11-17 调研 §四/§七）：三动作
    _action_adopt_generated,
    _action_generate_image,
    _action_media_verify,
)
from ..pipeline_actions import _action_pipeline  # E1 金路径（组合批 2026-11-17，契约 §3.7）
from ...services import adapt_service, pack_service, publish_service
from ..settings import _action_settings_ai, _action_settings_kb  # 第十八期 F46/F47
from ..tts_actions import _action_tts_generate  # G-T5 朗读（2026-11-19，契约 §3.16）
from ..security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _rel,
    _require_str,
    _resolve_file,
    _sanitize_error,
)


def _assert_asset_bounds(root: Path, article_path: Path, article: ParsedArticle) -> None:
    """动作前校验：cover 与正文本地图片引用必须落在 root 内。

    pack 复制封面、publish 读盘上传——越界路径会把 root 外文件带入物料包/上传链路。
    """
    refs: list[str] = []
    cover = str(article.metadata.get("cover") or "").strip()
    if cover:
        refs.append(cover)
    refs.extend(_collect_image_srcs(article.blocks))
    for raw in refs:
        parsed = urlparse(raw)
        if parsed.scheme or parsed.netloc or raw.startswith("//"):
            continue  # 网络图不读本地盘（外域问题由渲染级检查覆盖）
        if Path(raw).is_absolute():
            raise _HttpError(400, f"封面/正文图片必须是工作目录内相对路径（禁止绝对路径）: {raw}")
        try:
            resolved = (article_path.parent / raw).resolve()
        except (OSError, ValueError):
            raise _HttpError(400, f"封面/正文图片路径非法: {raw}") from None
        if not resolved.is_relative_to(root):
            raise _HttpError(400, f"封面/正文图片路径越界（必须仍在工作目录内）: {raw}")


# ---- 进程内共享 JobRunner（惰性创建 + 一次性孤儿恢复）----

_RUNNER: JobRunner | None = None
_RUNNER_LOCK = threading.Lock()


def _runner() -> JobRunner:
    """Studio 进程共享 runner：首次取用建池并做孤儿恢复（running→failed）。

    server.py 不感知任务层（R2 边界）；/api/jobs 与动作提交都先到这里，
    恢复先于任何列举/轮询。
    """
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            runner = JobRunner(max_workers=2)
            runner.startup()
            _RUNNER = runner
        return _RUNNER


def _reset_runner() -> JobRunner | None:
    """关闭并清空共享 runner（测试隔离用；生产路径不调用）。"""
    global _RUNNER
    with _RUNNER_LOCK:
        runner = _RUNNER
        _RUNNER = None
        if runner is not None:
            runner.shutdown(wait=True)
        return runner


def _submit(
    ctx: _StudioContext,
    kind: str,
    params: dict[str, Any],
    body: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """长动作统一提交件：body(job_id) 异常 → JobFailure（root 脱敏），返 {ok, job_id}。

    原六处同型 `_run` 闭包（adapt/pack/fetch/annotate_rewrite/compose_generate/
    sync_stats）的 try/except→JobFailure 收编于此（行为零变更）；body 内已抛
    JobFailure 的（自带 result=partial 等富载荷）原样透传不二次包装。
    _action_publish/_action_batch_adapt/_action_ingest 各有自定义闭包逻辑，
    不走本件。
    """
    def _run(job_id: str) -> dict[str, Any]:
        try:
            return body(job_id)
        except JobFailure:
            raise
        except Exception as exc:
            raise JobFailure(_sanitize_error(exc, ctx.root)) from None

    return {"ok": True, "job_id": _runner().submit(kind, params, _run)}


# ---- POST 动作（瞬时同步 / 长动作建 job；均须 confirm:true）----


def _adapt_warnings(output: Path) -> list[str]:
    """变体 frontmatter 的 adapt_warnings 本地校验提示（坏文件读作空）。"""
    try:
        return list(parse_article(output).metadata.get("adapt_warnings") or [])
    except Exception:
        return []


def _action_adapt(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """生成平台文字变体（adapt_service.generate_variant 进 job；AI 是长动作）。"""
    article_rel = _require_str(data, "path", alias="article")
    platform = _require_str(data, "platform")
    article_path = _resolve_file(ctx.root, article_rel)

    def _body(_job_id: str) -> dict[str, Any]:
        output = adapt_service.generate_variant(article_path, platform)
        return {"output": _rel(ctx.root, output), "warnings": _adapt_warnings(output)}

    return _submit(ctx, "adapt", {"article": article_rel, "platform": platform}, _body)


def _action_batch_adapt(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """批量生成平台变体：一 job 顺序执行 N 个平台，进度 phase: "2/4"。

    幂等续跑/部分失败清单在 adapt_service.generate_batch（BatchAdaptError），
    本层转 JobFailure(result=partial) 入库。
    """
    article_rel = _require_str(data, "path", alias="article")
    platforms_raw = data.get("platforms")
    if (
        not isinstance(platforms_raw, list)
        or not platforms_raw
        or not all(isinstance(p, str) and p.strip() for p in platforms_raw)
    ):
        raise _HttpError(400, "platforms 必须是非空的平台码字符串数组")
    platforms = [p.strip() for p in platforms_raw]
    article_path = _resolve_file(ctx.root, article_rel)
    runner = _runner()

    def _run(job_id: str) -> dict[str, Any]:
        def _phase(i: int, total: int) -> None:
            runner.set_phase(job_id, f"{i}/{total}")

        skipped: list[Path] = []
        try:
            outputs = adapt_service.generate_batch(
                article_path, platforms, on_progress=_phase, skipped=skipped
            )
        except adapt_service.BatchAdaptError as exc:
            partial = {
                "outputs": [_rel(ctx.root, p) for p in exc.outputs],
                "skipped": [_rel(ctx.root, p) for p in exc.skipped],
            }
            raise JobFailure(_sanitize_error(exc, ctx.root), result=partial) from None
        result: dict[str, Any] = {"outputs": [_rel(ctx.root, p) for p in outputs]}
        if skipped:
            result["skipped"] = [_rel(ctx.root, p) for p in skipped]
        return result

    job_id = runner.submit("batch_adapt", {"article": article_rel, "platforms": platforms}, _run)
    return {"ok": True, "job_id": job_id}


def _action_pack(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """生成物料包（pack_service.build_pack_for 进 job；limits 由服务层注入）。"""
    article_rel = _require_str(data, "path", alias="article")
    platform = _require_str(data, "platform")
    variant_rel = data.get("variant")
    if variant_rel is not None and not isinstance(variant_rel, str):
        raise _HttpError(400, "variant 必须是 root 内相对路径或 null")
    article_path = _resolve_file(ctx.root, article_rel)
    variant_path = str(_resolve_file(ctx.root, variant_rel)) if variant_rel else None
    try:
        article = parse_article(article_path)
    except Exception as exc:  # 坏 frontmatter/IO → 可读错误
        return _error_payload(exc, ctx.root)
    _assert_asset_bounds(ctx.root, article_path, article)  # 越界 → 400（提交期同步）

    def _body(_job_id: str) -> dict[str, Any]:
        pack_dir = pack_service.build_pack_for(article_path, platform, variant=variant_path)
        return {"dir": _rel(ctx.root, pack_dir)}

    params: dict[str, Any] = {"article": article_rel, "platform": platform}
    if variant_rel:
        params["variant"] = variant_rel
    return _submit(ctx, "pack", params, _body)


def _action_publish(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """仅创建公众号草稿（检查门=publish_service.run_publish_gate）；无 submit 接口。

    与 CLI publish 同门：门有 FAIL 提交期同步拒绝；网络调用进 job；成功置
    published（失败降级 job 内告警）。job 执行体直调 publish_flow——经
    actions 命名空间晚绑定读取（R-2 迁入 handlers 后保持补丁锚点契约：
    studio/__init__ §七「patch 请打在 musespiral.studio.actions.publish_flow 上」，
    拆分前为 actions 模块全局名的同一份调用期查值）。
    """
    # 延迟导入：payload/scan 反向依赖 actions._runner（re-export 面），顶部
    # 互导成环；actions 同理（actions 顶部导入本模块），均在调用期取用
    from .. import actions
    from ..payload import _render
    from ..scan import _log_path

    article_rel = _require_str(data, "path", alias="article")
    article_path = _resolve_file(ctx.root, article_rel)
    try:
        article = parse_article(article_path)
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    _assert_asset_bounds(ctx.root, article_path, article)  # 越界 → 400
    try:
        results = publish_service.run_publish_gate(article_path)
        html = _render(article)  # 上传链路复用（与门内渲染同口径；门已过，不再失败）
    except Exception as exc:  # 渲染失败 → 可读错误（与 check --render 同口径）
        return _error_payload(exc, ctx.root)
    if has_failures(results):
        failures = [message for level, message in results if level == "FAIL"]
        error = f"审核未通过（{len(failures)} 项 FAIL），已拒绝创建草稿: " + "；".join(failures)
        return {"ok": False, "error": _sanitize_error(RuntimeError(error), ctx.root)}
    runner = _runner()

    def _run(job_id: str) -> dict[str, Any]:
        try:
            media_id = actions.publish_flow(article, article_path, html, log_path=_log_path(ctx.root))
        except Exception as exc:  # 无凭证/无封面/网络/API 错误 → job 失败（已脱敏）
            raise JobFailure(_sanitize_error(exc, ctx.root)) from None
        result: dict[str, Any] = {"media_id": media_id}
        try:
            # C1（第十四期）：publish 语义=建草稿——draft→reviewed（原 mark_published
            # 置 published 与状态机冲突恒 WARN，对齐 CLI publish 同一口径）
            publish_service.auto_mark_reviewed(article_path)
        except (ValueError, OSError) as exc:
            result["warnings"] = [f"状态置位失败（草稿已创建，不影响）: {exc}"]
        return result

    job_id = runner.submit("publish", {"article": article_rel}, _run)
    return {"ok": True, "job_id": job_id}


def _action_status(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """标记文章状态（R3 状态机 Studio 侧入口；瞬时同步，不建 job）。

    白名单=MANUAL_SETTABLE（published 只由 publish 自动回写，§三-2 红线）；
    迁移合法性由 core.article.set_status 把关。
    """
    article_rel = _require_str(data, "path", alias="article")
    status = _require_str(data, "status")
    if status not in MANUAL_SETTABLE:
        raise _HttpError(
            400,
            f"status 必须是 {'/'.join(MANUAL_SETTABLE)} 之一"
            "（published 只由 publish 成功自动置位）"
            f": {status}",
        )
    article_path = _resolve_file(ctx.root, article_rel)
    try:
        _article_set_status(article_path, status)
    except Exception as exc:  # 非法迁移/IO → 可读错误（含迁移矩阵提示）
        return _error_payload(exc, ctx.root)
    return {"ok": True, "status": status, "article": article_rel}


def _action_job_cancel(ctx: _StudioContext, data: dict[str, Any]) -> dict[str, Any]:
    """取消长任务（2026-11-18 集成轮，R3.5 正典 §2.2 取消语义落点）。

    协作式：置任务层取消旗标（jobs/runner.cancel），**已完成的产物保留入库，
    未完成的在检查点放弃**（book_fetch 检查点=取文逐章/提炼逐 pass；未设
    检查点的 kind 不受影响自然终态）。终态 job → 200 {ok, cancelled:false}
    （幂等不误报）；未知 id → 404（同 GET /api/jobs/<id> 语义）。
    """
    job_id = data.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise _HttpError(400, "缺少必填字段 job_id")
    job_id = job_id.strip()
    runner = _runner()
    if runner.get(job_id) is None:
        raise _HttpError(404, f"任务不存在: {job_id}")
    accepted = runner.cancel(job_id)
    return {"ok": True, "job_id": job_id, "cancelled": bool(accepted)}


def _assert_fetch_url(url: str) -> None:
    """提交期同步校验抓取 URL（SSRF 白名单，快、本地，400 契约）。

    门本体 = url_fetch.assert_article_url（hostname 精确等于微信文章域），
    与 job 内在线抓取共用同一道门；ValueError → 400（不建 job）。动作入口
    在 fetch_actions.py（惰性 `from .actions import _assert_fetch_url` 经
    actions re-export 取用，锚名不变）。
    """
    try:
        url_fetch.assert_article_url(url)
    except ValueError as err:
        raise _HttpError(400, str(err)) from None


# ---- 域内路由片段（actions._ACTION_ROUTES 合并面之一）----

ROUTES: dict[str, Callable[[_StudioContext, dict[str, Any]], dict[str, Any]]] = {
    "/api/action/adapt": _action_adapt,
    "/api/action/pack": _action_pack,
    "/api/action/publish": _action_publish,
    "/api/action/batch_adapt": _action_batch_adapt,
    "/api/action/status": _action_status,
    "/api/action/fetch": _action_fetch,  # §十七 F21：微信文章 URL 抓取草稿
    "/api/action/generate_image": _action_generate_image,  # G1：文生图预览（同步零落盘，A-2 临时文件）
    "/api/action/adopt_generated": _action_adopt_generated,  # G1：人工采纳（搬正+tEXt+落库，唯一写面）
    "/api/action/media_verify": _action_media_verify,  # G1 A-4：连接验证（health 三态回写）
    "/api/action/pipeline": _action_pipeline,  # E1：一键金路径 → job（kind=pipeline；服务落地前可读降级）
    "/api/action/tts_generate": _action_tts_generate,  # G-T5 2026-11-19：朗读 → job tts_generate（调研吸收 §六）
    "/api/settings/kb": _action_settings_kb,  # 第十八期 F46：KB 域写（存在性+越界 400）
    "/api/settings/ai": _action_settings_ai,  # 第十八期 F47：AI 域写（key write-only/守门）
    "/api/jobs/cancel": _action_job_cancel,  # 集成轮 2026-11-18：长任务协作式取消（R3.5 正典 §2.2）
    "/api/agent/run": _action_agent_run,  # Phase D F75.4：多步编排提交（job agent_run）
    "/api/agent/adopt": _action_agent_adopt,  # Phase D F76.4：采纳分发（material/compose）
    "/api/booksource/import": _action_import,  # R0：导入预览（三态清单，零落库）
    "/api/booksource/import_apply": _action_import_apply,  # R0：按 selections 落库（唯一写面）
    "/api/booksource/refresh": _action_refresh,  # R0：手动刷新订阅（闸门旁路=显式）
    "/api/booksource/subs/add": _action_subs_add,  # R0：订阅新增/更新（同 url upsert）
    "/api/booksource/subs/del": _action_subs_del,  # R0：订阅删除
    "/api/book/search": _action_book_search,  # R3：聚合搜索 → job book_search（引擎代理）
    "/api/action/bookengine_demo": _action_bookengine_demo,  # 2026-11-23：体验模式开关
}
