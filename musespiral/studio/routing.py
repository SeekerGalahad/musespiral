"""Studio GET 路由分发（Phase A 拆分：载荷构造迁 payload、健康面板迁 health、
扫描迁 scan、批注/正文文件操作迁 annotations；本文件只留路由表与分派，
行为零变更）。

  - _GET_ROUTES：精确路由集合 + /api/jobs/<id> 动态段（R4，_RouteSet 承载）
  - _get_response：路径 → handler 分派（载荷来自 payload/health/assets），
    未知路由 404
  - 405（方法不符）由 server.py 的 Handler 方法表负责。
"""
from __future__ import annotations

from typing import Iterator

from .actions import _discover_star_payload
from .agent_payload import _agent_runs_payload  # Phase D F75.4：/api/agent/runs 读面
from .ai_payload import _ai_budget_payload, _ai_usage_payload  # G1 批 F1：AI 治理两读面
from .assets import _asset_response, _meta_payload
from .booksource_actions import (  # R0 书源功能（契约 §3.9）：四读面
    _booksource_engine_payload,
    _booksource_one_payload,
    _booksource_payload,
    _booksource_subs_payload,
)
from .book_actions import (  # R3 书源接驳+R3.5 提炼（契约 §3.10）：三读面
    _book_info_payload,
    _book_original_payload,
    _book_toc_payload,
)
from .health import _health_payload
from .ingest_actions import _ingest_status_payload  # B 采集扩展：采集面状态（只读）
from .plugin_actions import (  # PL-3 接驳批（2026-11-20 复核修复）：插件/流水线读面
    _pipeline_detail_payload,
    _pipeline_run_payload,
    _pipeline_run_audio_response,  # E-4（PL-6 批）：裁段音频 wav 字节流
    _pipeline_run_segments_payload,  # E-4（PL-6 批）：转写段落+ranges（编辑版优先）
    _pipeline_run_versions_payload,  # AI-3 复核 fix-4：run 域版本链清单+两版 diff
    _pipelines_payload,
    _plugin_check_payload,
    _plugins_payload,
)
from .media_actions import _media_preview_response  # G1 文生图：预览字节流（A-2）
from .tts_actions import _tts_file_response  # G-T5 朗读：MP3 字节流（内嵌 <audio> 播放面）
from .topic_radar_payload import _topic_radar_payload  # Phase D F78.4：/api/topic_radar 读面
from .version_actions import _article_diff_payload  # E-6 §3.7：版本池清单 + 两版 diff（纯读）
from .visual_payload import (  # Phase C F70-F74：/api/visual 族 GET 载荷
    _visual_design_payload,
    _visual_presets_payload,
    _visual_templates_payload,
)
from .payload import (
    _annotations_payload,
    _article_body_payload,
    _article_html,
    _article_payload,
    _article_review,
    _articles_payload,
    _compose_session_payload,
    _compose_sessions_payload,
    _discover_payload,
    _job_payload,
    _jobs_payload,
    _material_collections_payload,
    _material_detail_payload,
    _material_file_response,
    _material_iterations_diff_payload,
    _material_iterations_payload,
    _material_tags_payload,
    _materials_payload,
    _plan_payload,
    _platforms_payload,
    _stats_payload,
)
from .settings import _settings_payload
from .settings import _sources_domain as _sources_list
from .security import (
    _HTML_CTYPE,
    _HttpError,
    _JSON_CTYPE,
    _StudioContext,
    _json_bytes,
)


class _RouteSet:
    """GET 路由集合：精确路由 + 动态前缀段（R4 /api/jobs/<id> 先例；
    PL-3 接驳批 2026-11-20 增 /api/plugins/<id>/check、/api/pipelines/<id>、
    /api/pipelines/runs/<run_id> 三族——多前缀化）。

    server.py 的分派只做 `path in _GET_ROUTES`（R2 拆分边界不动 server.py），
    动态段以本含 __contains__ 的集合对象承载；__iter__ 仅暴露精确路由
    （既有消费方只按精确路由迭代——契约锁不锁动态段形状，动态段形状由
    端点层白名单回验防穿越）。
    """

    __slots__ = ("_exact", "_prefixes")

    def __init__(
        self,
        exact: frozenset[str],
        prefixes: tuple[str, ...] = ("/api/jobs/",),
    ) -> None:
        self._exact = exact
        self._prefixes = prefixes

    def __contains__(self, path: object) -> bool:
        if not isinstance(path, str):
            return False
        if path in self._exact:
            return True
        return any(
            path.startswith(prefix) and len(path) > len(prefix)
            for prefix in self._prefixes
        )

    def __iter__(self) -> Iterator[str]:
        return iter(self._exact)


# 动态 GET 前缀族（_RouteSet prefixes 的唯一权威；对拍锁
# test_frontend_api_literals_covered_by_backend_surface 消费——改这里须同步门）
GET_DYNAMIC_PREFIXES: tuple[str, ...] = (
    "/api/jobs/",       # R4：单任务读面
    "/api/plugins/",    # §3.19：/api/plugins/<id>/check
    "/api/pipelines/",  # §3.19：/api/pipelines/<id> 与 /api/pipelines/runs/<run_id>
)

_GET_ROUTES = _RouteSet(
    frozenset(
        {
            "/api/articles",
            "/api/article",
            "/api/article/html",
            "/api/article/review",
            "/api/article/body",  # R7：编辑 tab 载入正文（frontmatter 不含）
            # §3.17 新增（E-6 版本 diff 2026-11-19，正典=《musespiral-专业创作者视角
            # 深度评审与改进-2026-11-18》§3.7）：版本池清单 + 任意两版对比
            # （纯读——无 v1/v2 只回清单，有则附 hunks 与红删绿增 HTML）
            "/api/article/diff",
            "/api/annotations",  # R7：批注 sidecar 读取（增删走 POST 动作）
            "/api/platforms",
            "/api/stats",
            "/api/meta",
            "/api/health",  # §十七 F20：环境健康面板（只读零新安全面）
            "/api/jobs",  # R4：任务列表（顶栏任务面板数据面）
            "/api/discover",  # 第十二期 F30：知识库选题候选（KB 根内只读扫描；收编过渡期形状不变）
            "/api/discover/star",  # 档A M0：收藏星标 triage.starred（GET 读集合+最近记录）
            "/api/materials",  # 第二十期 F56：素材查询+评分现算（发现页收编后端，M2；UI 归 M3）
            "/api/materials/detail",  # M3：素材详情（血统引用状态）+ 删除连坐预览
            "/api/materials/file",  # 第二十一期 F60：素材文件字节流（三道门；与 /asset 同构不同域）
            "/api/materials/tags",  # 第二十一期 F63：标签频次表（全库现算 count 降序）
            "/api/materials/collections",  # 第二十一期 F62：手工集合清单（GET/POST 双态路径）
            # §3.20 新增（P2 素材再加工循环 2026-11-20，正典=《musespiral-格式生态
            # 与素材流转能力分析-2026-11-20》§3.3）：derived 素材打磨版本列表
            # + 同链两版对比（纯读；diff 复用 E-6 版本 diff 引擎，红删绿增
            # html 同款产物面）。GET 44→46。
            "/api/materials/iterations",
            "/api/materials/iterations/diff",
            "/api/settings",  # 第十八期 F46/F47：设置面只读（kb+ai 两域，key 恒布尔）
            "/api/sources",  # 第二十期 F53（M4）：源清单+素材计数（设置「源连接」域同源）
            "/api/compose",  # 第十八期 F48：创作台会话清单（M2；UI 归 M3）
            "/api/compose/session",  # 第十八期 F48：单会话详情（素材/当前稿/轮次）
            "/api/plan",  # P4 排期周历（契约 §3.2-2）：plan_service 只读文字周历
            "/api/ingest/status",  # B 采集扩展（第二十一期）：源健康/pending 待决区/锁占用
            "/api/visual/design",  # Phase C F70-F74：设计稿详情（SVG 载入重过白名单）
            "/api/visual/templates",  # Phase C F74：模板注册表元数据
            "/api/visual/presets",  # Phase C F74：预设清单
            "/api/agent/runs",  # Phase D F75.4：编排运行清单/详情（只读，?id= 双模式）
            "/api/topic_radar",  # Phase D F78.4：选题雷达快照读面（只读 latest；空态 snapshot:null）
            "/api/ai/usage",  # G1 批 F1：AI 用量日汇总（ai_usage.jsonl 现算；与 CLI ai usage 同数）
            "/api/ai/budget",  # G1 批 F1：三层预算读（全局两键+per_task 覆盖）
            "/api/media/preview",  # G1 文生图：预览字节流（A-2 服务端临时文件的消费面）
            # §3.9 新增（R0 书源功能 2026-11-18：四读面——索引/单源详情/订阅清单/引擎状态）
            "/api/booksource", "/api/booksource/one", "/api/booksource/subs",
            "/api/booksource/engine",
            # §3.10 新增（R3 书源接驳+R3.5 提炼 2026-11-18：三读面——详情/目录/
            # 章节原文（素材详情「查看原始章节」按需加载面））
            "/api/book/info", "/api/book/toc", "/api/book/original",
            # §3.16 新增（G-T5 TTS 朗读 2026-11-19，调研吸收 §六）：朗读产物
            # MP3 字节流（内嵌 <audio src> 直指，仅 .mp3 + 魔数复核）
            "/api/tts/file",
            # §3.19 新增（PL-3 插件系统接驳批 2026-11-20 复核修复，插件系统设计
            # §十二 as-built）：插件清单 + 流水线清单（精确面）；动态段
            # /api/plugins/<id>/check、/api/pipelines/<id>、/api/pipelines/runs/
            # <run_id> 由 _RouteSet 前缀承载 + _get_response 白名单分派。
            # GET 42→44（动态三族另计）。
            "/api/plugins",
            "/api/pipelines",
            "/asset",  # 预览图片（§15.1 预览所需；仅图片白名单 + root 内）
        }
    ),
    prefixes=GET_DYNAMIC_PREFIXES,
)


def _get_response(
    ctx: _StudioContext, path: str, query: dict[str, list[str]]
) -> tuple[int, str, bytes]:
    """GET 路由分派 → (状态码, Content-Type, body)。"""
    if path == "/api/articles":
        return 200, _JSON_CTYPE, _json_bytes(_articles_payload(ctx, query))
    if path == "/api/article":
        return 200, _JSON_CTYPE, _json_bytes(_article_payload(ctx, query))
    if path == "/api/article/html":
        return 200, _HTML_CTYPE, _article_html(ctx, query).encode("utf-8")
    if path == "/api/article/review":
        return 200, _HTML_CTYPE, _article_review(ctx, query).encode("utf-8")
    if path == "/api/article/body":
        return 200, _JSON_CTYPE, _json_bytes(_article_body_payload(ctx, query))
    if path == "/api/article/diff":  # E-6 §3.7：版本清单（+ 可选两版 diff，纯读）
        return 200, _JSON_CTYPE, _json_bytes(_article_diff_payload(ctx, query))
    if path == "/api/annotations":
        return 200, _JSON_CTYPE, _json_bytes(_annotations_payload(ctx, query))
    if path == "/api/platforms":
        return 200, _JSON_CTYPE, _json_bytes(_platforms_payload())
    if path == "/api/stats":
        return 200, _JSON_CTYPE, _json_bytes(_stats_payload(ctx))
    if path == "/api/meta":
        return 200, _JSON_CTYPE, _json_bytes(_meta_payload(ctx))
    if path == "/api/health":
        return 200, _JSON_CTYPE, _json_bytes(_health_payload(ctx))
    if path == "/api/jobs":
        return 200, _JSON_CTYPE, _json_bytes(_jobs_payload())
    if path == "/api/discover":
        return 200, _JSON_CTYPE, _json_bytes(_discover_payload(ctx, query))
    if path == "/api/materials":
        return 200, _JSON_CTYPE, _json_bytes(_materials_payload(ctx, query))
    if path == "/api/materials/detail":
        return 200, _JSON_CTYPE, _json_bytes(_material_detail_payload(ctx, query))
    if path == "/api/materials/file":
        return _material_file_response(ctx, query)
    if path == "/api/materials/tags":
        return 200, _JSON_CTYPE, _json_bytes(_material_tags_payload(ctx))
    if path == "/api/materials/collections":
        return 200, _JSON_CTYPE, _json_bytes(_material_collections_payload(ctx))
    # P2 素材再加工循环（契约 §3.20）：版本列表 + 同链两版 diff（纯读）
    if path == "/api/materials/iterations":
        return 200, _JSON_CTYPE, _json_bytes(_material_iterations_payload(ctx, query))
    if path == "/api/materials/iterations/diff":
        return 200, _JSON_CTYPE, _json_bytes(_material_iterations_diff_payload(ctx, query))
    if path == "/api/discover/star":
        return 200, _JSON_CTYPE, _json_bytes(_discover_star_payload(ctx, query))
    if path == "/api/settings":
        return 200, _JSON_CTYPE, _json_bytes(_settings_payload(ctx))
    if path == "/api/sources":
        return 200, _JSON_CTYPE, _json_bytes(_sources_list())
    if path == "/api/compose":
        return 200, _JSON_CTYPE, _json_bytes(_compose_sessions_payload(ctx))
    if path == "/api/compose/session":
        return 200, _JSON_CTYPE, _json_bytes(_compose_session_payload(ctx, query))
    if path == "/api/plan":
        return 200, _JSON_CTYPE, _json_bytes(_plan_payload(ctx, query))
    if path == "/api/ingest/status":
        return 200, _JSON_CTYPE, _json_bytes(_ingest_status_payload(ctx, query))
    if path == "/api/visual/design":
        return 200, _JSON_CTYPE, _json_bytes(_visual_design_payload(ctx, query))
    if path == "/api/visual/templates":
        return 200, _JSON_CTYPE, _json_bytes(_visual_templates_payload())
    if path == "/api/visual/presets":
        return 200, _JSON_CTYPE, _json_bytes(_visual_presets_payload())
    if path == "/api/agent/runs":
        return 200, _JSON_CTYPE, _json_bytes(_agent_runs_payload(ctx, query))
    if path == "/api/topic_radar":
        return 200, _JSON_CTYPE, _json_bytes(_topic_radar_payload(ctx, query))
    if path == "/api/ai/usage":
        return 200, _JSON_CTYPE, _json_bytes(_ai_usage_payload(ctx, query))
    if path == "/api/ai/budget":
        return 200, _JSON_CTYPE, _json_bytes(_ai_budget_payload(ctx))
    if path == "/api/media/preview":
        return _media_preview_response(ctx, query)
    # R0 书源功能（契约 §3.9）：四读面
    if path == "/api/booksource":
        return 200, _JSON_CTYPE, _json_bytes(_booksource_payload(ctx, query))
    if path == "/api/booksource/one":
        return 200, _JSON_CTYPE, _json_bytes(_booksource_one_payload(ctx, query))
    if path == "/api/booksource/subs":
        return 200, _JSON_CTYPE, _json_bytes(_booksource_subs_payload(ctx, query))
    if path == "/api/booksource/engine":
        return 200, _JSON_CTYPE, _json_bytes(_booksource_engine_payload(ctx, query))
    # R3 书源接驳+R3.5 提炼（契约 §3.10）：三读面
    if path == "/api/book/info":
        return 200, _JSON_CTYPE, _json_bytes(_book_info_payload(ctx, query))
    if path == "/api/book/toc":
        return 200, _JSON_CTYPE, _json_bytes(_book_toc_payload(ctx, query))
    if path == "/api/book/original":
        return 200, _JSON_CTYPE, _json_bytes(_book_original_payload(ctx, query))
    # G-T5 TTS 朗读（契约 §3.16）：MP3 字节流（三道门在 tts_actions）
    if path == "/api/tts/file":
        return _tts_file_response(ctx, query)
    # PL-3 插件系统接驳批（契约 §3.18）：清单两精确面 + 三族动态段
    # （顺序敏感：runs/ 前缀先于通用 /api/pipelines/<id> 分支）
    if path == "/api/plugins":
        return 200, _JSON_CTYPE, _json_bytes(_plugins_payload())
    if path.startswith("/api/plugins/") and path.endswith("/check"):
        plugin_id = path[len("/api/plugins/"):-len("/check")]
        if not plugin_id or "/" in plugin_id:
            raise _HttpError(404, f"未知路由: {path}")
        return 200, _JSON_CTYPE, _json_bytes(_plugin_check_payload(ctx, plugin_id))
    if path == "/api/pipelines":
        return 200, _JSON_CTYPE, _json_bytes(_pipelines_payload())
    # E-4 转写编辑接驳（PL-6 批，契约 §3.19）：runs/<run_id> 下两动态读面
    # ——segments（编辑版优先）/ audio（裁段 wav 字节流，tts 文件流同先例）。
    # 须先于通用 runs/<run_id> 分支；run_id 拒斜杠（白名单正则在载荷侧复检）。
    if path.startswith("/api/pipelines/runs/") and path.endswith("/segments"):
        run_id = path[len("/api/pipelines/runs/"):-len("/segments")]
        if not run_id or "/" in run_id:
            raise _HttpError(404, f"未知路由: {path}")
        return 200, _JSON_CTYPE, _json_bytes(_pipeline_run_segments_payload(ctx, run_id))
    if path.startswith("/api/pipelines/runs/") and path.endswith("/audio"):
        run_id = path[len("/api/pipelines/runs/"):-len("/audio")]
        if not run_id or "/" in run_id:
            raise _HttpError(404, f"未知路由: {path}")
        return _pipeline_run_audio_response(ctx, run_id, query)
    # AI-3 复核 fix-4：run 域版本链读面（归档 outputs.vN 的清单+两版 diff）——
    # 动态段 runs/<id>/versions，须先于通用 runs/<id> 分支（同 segments/audio）。
    if path.startswith("/api/pipelines/runs/") and path.endswith("/versions"):
        run_id = path[len("/api/pipelines/runs/"):-len("/versions")]
        if not run_id or "/" in run_id:
            raise _HttpError(404, f"未知路由: {path}")
        return 200, _JSON_CTYPE, _json_bytes(
            _pipeline_run_versions_payload(ctx, run_id, query)
        )
    if path.startswith("/api/pipelines/runs/"):
        run_id = path[len("/api/pipelines/runs/"):]
        return 200, _JSON_CTYPE, _json_bytes(_pipeline_run_payload(ctx, run_id))
    if path.startswith("/api/pipelines/"):
        pid = path[len("/api/pipelines/"):]
        if not pid or "/" in pid:
            raise _HttpError(404, f"未知路由: {path}")
        return 200, _JSON_CTYPE, _json_bytes(_pipeline_detail_payload(ctx, pid))
    if path.startswith("/api/jobs/"):
        return 200, _JSON_CTYPE, _json_bytes(_job_payload(path[len("/api/jobs/") :]))
    if path == "/asset":
        return _asset_response(ctx, query)
    raise _HttpError(404, f"未知路由: {path}")  # pragma: no cover - 上层已拦
