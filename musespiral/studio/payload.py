"""Studio GET 端点载荷构造（Phase A 自 routing.py 拆出，行为零变更）。

  - 队列/详情/渲染/对照：_articles_payload（R3 status 字段 + R9 请求级扫描
    缓存）/ _article_payload / _article_html / _article_review（note 可回放
    CLI review 产物）
  - R7 编辑载荷：_article_body_payload（正文原文，frontmatter 不含）与
    _annotations_payload（批注 sidecar 读取；文件操作见 annotations.py）
  - 平台矩阵 _platforms_payload（platform list 同源）/ 统计 _stats_payload /
    任务层 _jobs_payload + _job_payload（R4：列表 + 单条轮询）
  - 渲染口径 _render（与 WeChatAdapter._build_renderer 同源）
  - 第十二期 F30：_discover_payload（知识库选题候选；KB 根外一律拒绝）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..core.content.parser import ParsedArticle, parse_article
from ..core.quality.checks import run_checks
from ..core.quality.render_checks import render_checks
from ..platforms import PLIMITS, list_platforms
from ..core.quality.review import build_review_html
from ..services import publish_log_service, publish_service
from ..services import pack_service  # N-5 物料包并排预览读面（清单+单平台 payload+diff）
from .actions import _runner
from .annotations import _load_annotations, _read_article_text, _split_head_body
from .assets import _rewrite_local_images
from .scan import (
    _VariantScanCache,
    _article_flags,
    _front_status,
    _iter_article_files,
    _paired_note,
    _paired_review,
)
from .security import (
    _HttpError,
    _StudioContext,
    _error_payload,
    _mtime_date,
    _query_str,
    _rel,
    _resolve_dir_arg,
    _resolve_file,
    _resolve_inside,
    _sanitize_error,
)


# 平台展示名（与 cli._PLATFORM_NAMES 同源；仅展示用途，不参与路由/校验）
_PLATFORM_NAMES: dict[str, str] = {
    "mp": "微信公众号",
    "xhs": "小红书",
    "channels": "视频号",
    "douyin": "抖音",
    "zhihu": "知乎",
}


# ---- 渲染 ----


def _render(article: ParsedArticle) -> str:
    """按 frontmatter > 默认值渲染——单一权威在 services.publish_service.
    render_article（Phase A 收编两处逐字相同的私有副本），本函数保留为
    薄壳委托（studio/__init__ re-export 与既有调用点的锚点不变）。"""
    return publish_service.render_article(article)


# ---- GET 端点 ----


def _articles_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """队列视图数据：root（或其子目录）下文章卡片列表（最新在前）。"""
    base = ctx.root
    root_arg = _query_str(query, "root")
    if root_arg is not None:
        base = _resolve_dir_arg(ctx.root, root_arg)
        if not base.is_dir():
            raise _HttpError(400, f"root 参数不是目录: {root_arg}")

    articles: list[dict[str, Any]] = []
    scan = _VariantScanCache()  # R9：请求级缓存（每目录一次建索引，跨文章复用）
    for path in _iter_article_files(base):
        try:
            article = parse_article(path)
        except Exception:
            continue  # 无法解析的 md 不是文章（坏 frontmatter/IO 错误）
        metadata = article.metadata
        if not (metadata.get("title") or metadata.get("cover")):
            continue  # 无 musespiral frontmatter 的普通笔记不进队列
        try:
            results = run_checks(article, path)
        except Exception:
            continue
        # 队列徽章与详情同口径：frontmatter 级 + 渲染级（否则只有渲染级 FAIL
        # 的文章会被「可发布」筛选误选，详情却又亮红）
        try:
            html = _render(article)
        except Exception as exc:
            results.append(("FAIL", f"渲染失败: {_sanitize_error(exc, ctx.root)}"))
        else:
            try:
                results += render_checks(html, article, path)
            except Exception:
                pass  # 渲染级检查异常不吞掉已得报告（与详情同口径）
        counts = {"fail": 0, "warn": 0, "pass": 0}
        for level, _ in results:
            counts[level.lower()] += 1
        articles.append(
            {
                "path": _rel(ctx.root, path),
                "title": str(metadata.get("title") or path.stem),
                "date": str(metadata.get("date") or _mtime_date(path)),
                "chars": len(article.raw_body.strip()),
                "cover": str(metadata.get("cover") or ""),  # 队列卡片封面缩略图（正典 §4.2；空串=前端走 accent 色块回退）
                "checks": counts,
                "flags": _article_flags(ctx.root, path, cache=scan, body=article.raw_body),
                "status": _front_status(metadata),  # R3：队列卡片状态徽章数据面
            }
        )
    articles.sort(key=lambda item: (item["date"], item["path"]), reverse=True)
    return {"articles": articles}


def _article_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """详情视图数据：标题/frontmatter/逐条检查报告/可渲染性/配对笔记。"""
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    path = _resolve_file(ctx.root, rel)
    article = parse_article(path)
    checks = [{"level": level, "message": message} for level, message in run_checks(article, path)]

    renderable = True
    try:
        html = _render(article)
    except Exception as exc:
        renderable = False
        # 渲染失败本身记一条 FAIL（与 check --render / 队列徽章同口径）
        checks.append({"level": "FAIL", "message": f"渲染失败: {_sanitize_error(exc, ctx.root)}"})
    if renderable:
        try:
            checks.extend(
                {"level": level, "message": message}
                for level, message in render_checks(html, article, path)
            )
        except Exception:
            pass  # 渲染级检查异常不吞掉已得的 frontmatter 级报告

    # 对照来源优先级：配对笔记（实时 diff）> CLI review 已生成产物（回放）
    pair = _paired_note(path, ctx.root) or _paired_review(path, ctx.root)
    return {
        "title": str(article.metadata.get("title") or path.stem),
        "meta": article.metadata,
        "checks": checks,
        "renderable": renderable,
        "note": _rel(ctx.root, pair) if pair is not None else None,
        "status": _front_status(article.metadata),  # R3：详情「标记已审」按钮数据面
    }


def _article_html(ctx: _StudioContext, query: dict[str, list[str]]) -> str:
    """渲染后完整 HTML（iframe 预览用；相对图片 src 重写为 /asset）。"""
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    article_path = _resolve_file(ctx.root, rel)
    return _rewrite_local_images(_render(parse_article(article_path)), ctx.root, article_path)


def _article_review(ctx: _StudioContext, query: dict[str, list[str]]) -> str:
    """原笔记 ↔ 文章对照 HTML；无 note 参数 → 404（契约）。

    note 指向本篇的 `<stem>.review.html`（CLI musespiral review 产物）时直接回放该页；
    其余仍是 note ↔ article 的实时 diff。只回放本篇派生名，避免任意 HTML 被服务。
    """
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    note_rel = _query_str(query, "note")
    if note_rel is None:
        raise _HttpError(404, "未提供配对笔记（?note=<root 内相对路径>）")
    article_path = _resolve_file(ctx.root, rel)
    note_path = _resolve_file(ctx.root, note_rel)
    expected = _paired_review(article_path, ctx.root)
    if expected is not None and note_path == expected:
        try:
            return note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            raise _HttpError(404, f"对照页读取失败: {note_rel}") from None
    return build_review_html(note_path, article_path)


def _platforms_payload() -> list[dict[str, Any]]:
    """平台能力矩阵（platform list 同源：registry + profiles）。"""
    return [
        {
            "code": adapter.name,
            "name": _PLATFORM_NAMES.get(adapter.name, adapter.name),
            "route": adapter.route,
            "capabilities": sorted(adapter.capabilities),
            "limits": PLIMITS.get(adapter.name, {}),
        }
        for adapter in list_platforms()
    ]


def _stats_payload(ctx: _StudioContext) -> dict[str, Any]:
    """发布记录汇总（publish_log_service.summarize 结构，含 by_platform/recent）。

    第十四期集成：读侧统一走 publish_log_service——root∪home 双日志合并
    去重 + demo_ fixture 行判除（正典 §1.2「周历与统计均排除」；原 _log_path
    单处二选一会漏 home 侧行、且把演示 fixture 计入看板，与 musespiral stats 同修）。

    recent_n=120（3-2 展示面截断热修）：仪表盘趋势图逐事件计数（renderTrend
    按 ts 分桶，一条事件一根柱），summarize 默认 recent_n=10 会截——单平台
    >10 条即少柱。120 覆盖单平台 30 天窗口内任意现实发布密度；仅本地
    127.0.0.1 载荷（最坏 ~5 平台 × 120 条 × ~250B），jsonl 落盘不截断。
    """
    return publish_log_service.summarize(ctx.root, recent_n=120)


# ---- 任务层端点（R4：/api/jobs 列表 + /api/jobs/<id> 单条轮询）----


def _jobs_payload() -> dict[str, Any]:
    """GET /api/jobs：最近 50 条任务（新在前）。

    经 actions._runner() 取数——首次访问即触发共享 runner 的孤儿恢复
    （上一进程遗留 running → failed(interrupted)），恢复先于任何列举。
    """
    return {"jobs": _runner().list(limit=50)}


def _job_payload(job_id: str) -> dict[str, Any]:
    """GET /api/jobs/<id>：单条任务轮询；未知 id → 404（JSON）。"""
    job = _runner().get(job_id)
    if job is None:
        raise _HttpError(404, f"任务不存在: {job_id}")
    return job


def _article_body_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/article/body?path= → {content}：正文原文，frontmatter 不含（R7）。"""
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    path = _resolve_file(ctx.root, rel)
    return {"content": _split_head_body(_read_article_text(path))[1]}


def _article_pack_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/article/pack → 物料包并排预览数据面（N-5，纯读零写）。

    双态（同 /api/article/diff 的「同一端点兼两态」判例）：
      - 无 `platform` → `{article, packs:[{platform,dir,available}]}`（清单：
        六个可成包平台逐个给有没有，前端下拉用）；
      - 有 `platform` → `{article, **pack_service.pack_preview(...)}`：payload
        头部字段 + 原文 + 红删绿增 diff HTML。物料包不存在/残缺**不是错误**
        （200 `available:false` + note，前端「尚未适配」提示面）；platform
        白名单外 → 400（service 的 PackPreviewError，同时挡目录名穿越）。
    """
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    path = _resolve_file(ctx.root, rel)
    platform = _query_str(query, "platform")
    if platform is None:
        return {"article": rel, "packs": pack_service.pack_inventory(path)}
    try:
        return {"article": rel, **pack_service.pack_preview(path, platform)}
    except pack_service.PackPreviewError as err:
        raise _HttpError(400, str(err)) from None


def _annotations_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/annotations?path= → {annotations:[...]}；缺 sidecar = 空列表。"""
    rel = _query_str(query, "path")
    if rel is None:
        raise _HttpError(400, "缺少 path 参数（root 内相对路径）")
    return {"annotations": _load_annotations(_resolve_file(ctx.root, rel))}


def _discover_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/discover：知识库选题候选（F30 数据面，第十二期）。

    - 知识库根来自 config.knowledge_root()（~/.musespiral/kb_root.txt）；未配置/
      不存在 → {"candidates": []}（不 500，前端走空状态引导）。
    - source 前缀与 limit 在调服务前先过 security 校验：source 必须 resolve
      后落在知识库根内（../、绝对路径、NUL 一律 400——_resolve_inside）；
      limit 钳制 [1,100]；min_score 可选（整数，钳制 [0,100]，显式值优先）——
      未显式给时：一般源走服务默认 60；模式家族源缺省 0（族卡实测封顶 ~35
      分，60 阈值下该 chip 恒空，正典 mock「[模式家族 163]」无法成立）。
    - discover_service 的 import 写在函数体内（服务模块由并行小组落地，
      缺席时 KB 根未配置的空响应路径不触发导入）。
    """
    kb = config.knowledge_root()
    if kb is None or not kb.is_dir():
        return {"candidates": []}
    source: Path | None = None
    source_raw = _query_str(query, "source")
    if source_raw is not None:
        source = _resolve_inside(kb, source_raw)  # 越界/绝对路径 → 400
    kwargs: dict[str, Any] = {"include_drafted": _query_str(query, "all") is not None}
    limit_raw = _query_str(query, "limit")
    if limit_raw is not None:
        try:
            kwargs["limit"] = max(1, min(int(limit_raw), 100))
        except ValueError:
            raise _HttpError(400, f"limit 必须是整数: {limit_raw}") from None
    min_raw = _query_str(query, "min_score")
    if min_raw is not None:
        try:
            kwargs["min_score"] = max(0, min(int(min_raw), 100))  # 钳制 [0,100]
        except ValueError:
            raise _HttpError(400, f"min_score 必须是整数: {min_raw}") from None
    elif source is not None and "模式家族" in source.relative_to(kb).as_posix():
        kwargs["min_score"] = 0  # 族卡封顶 ~35 分：未显式给阈值时族卡源缺省 0（否则恒空）
    if source_raw is None:
        # 无 source：缺省=登记根下源码分析目录（正典 F26 默认源）。不走
        # service 的 CWD 相对默认——Studio 进程 CWD 与仓库根无关，直连
        # API 不带 source 必须仍指向登记根。置于参数校验之后：limit/
        # min_score 的 400 优先；目录缺失 → 空态引导（不 500）
        source = kb / "13-source-analysis"
        if not source.is_dir():
            return {"candidates": []}
    from ..services import discover_service  # 函数体内导入（导入期解耦）

    try:
        candidates = discover_service.scan_candidates(source=source, **kwargs)
    except Exception as exc:  # 服务层可读错误 → ok:false（不 500）
        return _error_payload(exc, ctx.root)

    def _kb_rel(item: dict[str, Any]) -> dict[str, Any]:
        """候选 path 归一为 KB 根相对 posix：服务给绝对路径（scanner 输出），
        前端「生成骨架」回贴该值走 POST——绝对路径会被 _resolve_inside 拒绝；
        相对化同时避免向浏览器泄漏工作区外的绝对路径（与 _rel 同纪律）。"""
        try:
            item["path"] = Path(str(item.get("path") or "")).resolve().relative_to(kb).as_posix()
        except (OSError, ValueError):
            pass  # 根外/非路径值原样保留（draft 侧的防护仍会拒绝）
        return item

    return {"candidates": [_kb_rel(dict(c)) for c in candidates]}


# ── 第二十期 F56：素材视图查询面（发现页收编后端，M2；UI 归 M3）───────


def _query_bool(query: dict[str, list[str]], key: str) -> bool | None:
    """布尔 query 参数：true/false（大小写不敏感），缺省 None=不过滤。"""
    raw = _query_str(query, key)
    if raw is None:
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    raise _HttpError(400, f"{key} 必须是 true/false: {raw}")


def _materials_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/materials：素材查询 + 评分现算 + drafted 推导（F56 数据面）。

    过滤参数：starred/read/enabled（true/false）、type、source、limit
    （整数钳制 [1,500]）。articles_root=ctx.root（Studio 工作目录即文章根，
    与骨架生成同约定）；kb_root/词典由服务层按登记根自取。
    /api/discover 在收编过渡期保持原形状原口径（零变更，向后兼容）。
    """
    from ..services import materials_service  # 函数体内导入（导入期解耦）

    kwargs: dict[str, Any] = {
        "articles_root": ctx.root,
        "starred": _query_bool(query, "starred"),
        "read": _query_bool(query, "read"),
        "enabled": _query_bool(query, "enabled"),
        "mtype": _query_str(query, "type"),
        # N-2（2026-11-21）：spark chip 读 sub_type（payload.meta.sub_type
        # 判别，非 type——「类型枚举不动」裁决的读侧面）
        "sub_type": _query_str(query, "sub_type"),
        "source_id": _query_str(query, "source"),
        # 第二十一期正交扩展（F62/F63/F64）：tag/q/collection 全参数 AND，
        # 先过滤后搜索谓词；q 无专属错误（任何字符串合法，空串=不过滤）
        "tag": _query_str(query, "tag"),
        "q": _query_str(query, "q"),
        "collection": _query_str(query, "collection"),
    }
    # N-4（2026-11-21）：confidence=low 低置信筛选（枚举外 400——与 query
    # 服务层校验同语义双门；缺省 None=不过滤）
    conf = _query_str(query, "confidence")
    if conf is not None and conf != "low":
        raise _HttpError(400, f"confidence 只支持 'low' 筛选: {conf}")
    kwargs["confidence"] = conf
    limit_raw = _query_str(query, "limit")
    if limit_raw is not None:
        try:
            kwargs["limit"] = max(1, min(int(limit_raw), 500))
        except ValueError:
            raise _HttpError(400, f"limit 必须是整数: {limit_raw}") from None
    try:
        return materials_service.query_scored(**kwargs)
    except Exception as exc:  # 服务层可读错误 → ok:false（不 500）
        return _error_payload(exc, ctx.root)


def _material_detail_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/materials/detail?id=…：素材详情（血统引用就地状态）+ 删除连坐预览。

    M3 素材视图删除两步 confirm 的声明数据面：cascade 为引用该素材的
    derived 清单（不级联删除，删除后引用处显「素材已删」徽章——正典
    §1.2 连坐语义）。不存在/已删 → 404（可读错误，不静默空载荷）。
    """
    from ..services import materials_service  # 函数体内导入（导入期解耦）

    mid = _query_str(query, "id")
    if mid is None:
        raise _HttpError(400, "缺少必填参数 id")
    detail = materials_service.get_detail(mid)
    if detail is None:
        raise _HttpError(404, f"素材不存在或已删: {mid}")
    try:
        cascade = materials_service.cascade_preview(mid)
    except ValueError as exc:
        raise _HttpError(404, str(exc)) from None
    return {"ok": True, "material": detail, "cascade": cascade}


# ── 第二十一期 Phase A：文件服务（F60）/ 标签频次（F63）/ 集合清单（F62）──

# 内容寻址 id 永不变更 → 浏览器可永久缓存（正典 §二-F60）
_IMMUTABLE_CACHE = (("Cache-Control", "public, max-age=31536000, immutable"),)


def _material_file_response(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> tuple[int, str, bytes, tuple[tuple[str, str], ...]]:
    """GET /api/materials/file?id=&variant=thumb|full：素材文件字节流（F60）。

    三道门（路径边界/后缀白名单/魔数复核）在服务层钉死；本层只做状态码
    映射：ValueError → 400、FileNotFoundError → 404。Content-Type 按魔数
    真值；immutable 缓存头（缓存键含 id 即含内容指纹——无失效问题亦无
    陈图风险）。与 /asset（发布域，锚 articles root）同构不同域。"""
    from ..services import materials_service

    mid = _query_str(query, "id")
    if mid is None:
        raise _HttpError(400, "缺少必填参数 id")
    variant = _query_str(query, "variant") or "thumb"
    try:
        body, ctype = materials_service.read_material_file(mid, variant)
    except FileNotFoundError as exc:
        raise _HttpError(404, _sanitize_error(exc, ctx.root)) from None
    except ValueError as exc:
        raise _HttpError(400, _sanitize_error(exc, ctx.root)) from None
    return 200, ctype, body, _IMMUTABLE_CACHE


def _material_tags_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/materials/tags：全库标签频次表（F63，count 降序全量现算）。"""
    from ..services import materials_service

    return {"tags": materials_service.tag_counts()}


def _material_collections_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/materials/collections：手工集合清单（F62，双计数——墓碑
    惰性滤除后 active_member_count 与全长 member_count 并呈，非静默缩数）。"""
    from ..services import collections_service

    return {"collections": collections_service.list_collections()}


def _material_iterations_payload(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> dict[str, Any]:
    """GET /api/materials/iterations?id=：打磨版本列表（P2 素材再加工循环）。

    id 可为链上任一素材（原始根/任一版本，链根判定在服务层）；素材不
    存在/已删 → 404（不静默空列表）。载荷 {root, versions:[…]}——versions
    按 iteration 升序，空列表=该素材尚无打磨史（前端空态）。
    """
    from ..services import materials_service, refine_service

    mid = _query_str(query, "id")
    if mid is None:
        raise _HttpError(400, "缺少必填参数 id")
    if materials_service.get_material(mid) is None:
        raise _HttpError(404, f"素材不存在或已删: {mid}")
    try:
        return {"ok": True, **refine_service.list_iterations(mid)}
    except refine_service.RefineError as err:
        raise _HttpError(400, str(err)) from None


def _material_iterations_diff_payload(
    ctx: _StudioContext, query: dict[str, list[str]]
) -> dict[str, Any]:
    """GET /api/materials/iterations/diff?a=&b=：两版对比（P2；纯读）。

    a 与 b 必须同时给出（/api/article/diff 的 v1/v2 同款「只给一侧=形状
    不对」纪律）；任一侧不存在/已删 → 404；跨迭代链（链根不同）或任一
    侧无正文面 → 400 可读拒绝（业务校验，服务层 RefineError 域）。
    """
    from ..services import materials_service, refine_service

    a, b = _query_str(query, "a"), _query_str(query, "b")
    if a is None or b is None:
        raise _HttpError(400, "a 与 b 必须同时给出（同链两版素材 id）")
    for mid in (a, b):
        if materials_service.get_material(mid) is None:
            raise _HttpError(404, f"素材不存在或已删: {mid}")
    try:
        return {"ok": True, **refine_service.diff_iterations(a, b)}
    except refine_service.RefineError as err:
        raise _HttpError(400, str(err)) from None


# ── 第十八期 F48：创作台会话（M2 数据面；素材 path 同 discover 归一纪律）──


def _compose_sessions_payload(ctx: _StudioContext) -> dict[str, Any]:
    """GET /api/compose：会话清单（status/rounds/sources 计数）。"""
    from ..services import compose_service  # 函数体内导入（导入期解耦）

    return {"sessions": compose_service.list_sessions(root=ctx.root)}


def _compose_session_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/compose/session?slug=…：单会话详情（素材/当前稿块/轮次块+裁决）。"""
    from ..services import compose_service  # 函数体内导入（导入期解耦）

    slug = _query_str(query, "slug")
    if slug is None:
        raise _HttpError(400, "缺少必填参数 slug")
    try:
        detail = compose_service.session_detail(slug, root=ctx.root)
    except Exception as exc:
        return _error_payload(exc, ctx.root)
    for s in detail.get("sources") or []:
        # 素材绝对路径不透出（与 _discover_payload 的 _kb_rel 同纪律）；
        # N-2 material://<id> 素材定位符不是文件路径（无本地路径可泄），
        # 原样透出——它是前端显示锚与 remove_source 的匹配锚，basename 化
        # 会剥掉协议头（集成轮 e2e 实测缺陷：池行显示裸 id、移除锚失配）。
        p = str(s.get("path") or "")
        if not p.startswith("material://"):
            s["path"] = Path(p).name or s.get("path")
    return detail


def _plan_payload(ctx: _StudioContext, query: dict[str, list[str]]) -> dict[str, Any]:
    """GET /api/plan：排期周历只读（契约 §3.2-2；plan_service 唯一读面）。

    双形态（P4 接线期过渡——前端既有消费面是文字周历 text（ui.js showPlan
    无参调用读 d.text），契约 §3.2-2 预定形状是 items；§八-7 裁决归一前
    两者并存，?range= 显式走契约 items 形态）：
      - ?week=YYYY-Www（或无参数=本周）→ {ok, week, text}：render_week 文字
        周历，行首前缀即三态口径（计划/已交付/已发布）；非法周序 → 400；
      - ?range=30|90 → {ok, items:[{article, title, status: planned|
        delivered|published, publish_at, date}…]}：plan_service.plan_items
        契约形态；枚举外 → 400。
    宽容读：坏 frontmatter/坏日志行由服务层逐行跳过，不 500。
    """
    from ..services import plan_service  # 函数体内导入（导入期解耦）

    range_raw = _query_str(query, "range")
    if range_raw is not None:
        if range_raw not in ("30", "90"):
            raise _HttpError(400, f"range 必须是 30/90 之一: {range_raw}")
        try:
            items = plan_service.plan_items(root=ctx.root, days=int(range_raw))
        except Exception as exc:  # 服务层可读错误 → ok:false（不 500）
            return _error_payload(exc, ctx.root)
        return {"ok": True, "items": items}
    week_vals = query.get("week") or [""]
    week = week_vals[-1].strip() or None
    try:
        text = plan_service.render_week(ctx.root, week=week)
    except ValueError as err:
        raise _HttpError(400, str(err)) from None
    return {"ok": True, "week": week or "", "text": text}
