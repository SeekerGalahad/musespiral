"""Studio 本地工作台后端 — §15.2 API 契约 + §15.3 安全红线。

单入口本地 Web 工作台的服务端：只绑 127.0.0.1、零新依赖（stdlib
ThreadingHTTPServer，与 preview.py 同一路线）。前端是 M1 双层产物
`musespiral/studio/ui.html`（壳）+ `musespiral/studio/assets/core.js|core.css`（M1 前端
多文件化 2026-11-18，正典=《musespiral-前端多文件化设计-2026-11-18》），本包按
§15.2 契约提供数据面与动作编排。

端点（§15.2 唯一依据）:
  GET  /                                ui.html（importlib.resources 读 musespiral/studio/ui.html）
  GET  /assets/<rel>                    前端产物静态面（.js/.css 白名单 + ETag 304）
  GET  /api/articles?root=<dir>         队列卡片：path/title/date/chars/checks/flags
  GET  /api/article?path=<rel>          详情：title/meta/checks/renderable/note
  GET  /api/article/html?path=<rel>     渲染后完整 HTML（iframe 预览）
  GET  /api/article/review?path=&note=  对照 HTML（无 note → 404；note 可为
                                         `<stem>.review.html` 已生成对照页，直接回放）
  GET  /asset?path=<rel>               预览用本地图片（iframe 相对 src 重写到此）
  GET  /api/platforms                   registry + profiles 能力矩阵
  GET  /api/stats                       storage.jsonl.summarize 结构（含 by_platform/recent）
  GET  /api/meta                        {root, version, ai_configured}（§16 F19；
                                         root 为绝对路径供前端 root chip 显示，
                                         AI 只回布尔，不回显任何配置内容）
  POST /api/action/adapt|pack|publish   动作三件套（必须 confirm:true）

安全红线（§15.3，逐条落实）:
  - 仅绑定 127.0.0.1；GET/POST 以外方法一律 405；未知路由 404（响应均为 JSON）
  - 路径穿越防护：所有 <rel> 必须是 root 内相对路径 —— 绝对路径直接拒绝，
    resolve 后必须仍 is_relative_to(root)，否则 400（%00 等非法字符同样 400）
  - 动作白名单=adapt/pack/publish 三项；不存在 submit/发表接口
    （publish 只调 platforms.wechat.publish_flow 建草稿，绝不 submit）
  - 凭证永不进任何响应；异常文本脱敏（token/appid/secret/api_key 值打码 +
    root 外路径打码 + 截断，§16 F19）
  - POST 动作必须带 confirm:true，缺省拒绝 400（人点确认=人审闸门在 UI 的落点）。
    §15.2 的 pack 行未显式写出 confirm，但 §15.3「POST 动作必须带 confirm:true」
    覆盖全部动作 —— 三件套统一强制（设计文档内部不一致处按红线执行）

契约未展开处的实现口径（均在此文档化，供集成阶段对齐）:
  - 队列文章=root 下带 musespiral frontmatter（title/cover 至少一项）的 .md；
    衍生文件不进队列、作为源文章的 flags 标记：配对笔记 <stem>.note.md、
    平台变体 <stem>.<平台码>.md、review 产物 <stem>.review.html、
    物料包目录 <stem>.*.pack/，以及隐藏目录（含 .musespiral）
  - flags.note 为布尔（有配对笔记）；详情接口的 note 是该笔记的 root 相对路径
    （无配对为 null），可直接拼给 /api/article/review?note=
  - flags.published=发布日志中该文章路径有 draft/publish 记录（对应 UI
    「已发草稿」徽章）；日志优先取 root/publish_log.jsonl（演示 fixture 与
    工作区自带日志场景），缺省回落 storage.jsonl.default_log_path()（~/.musespiral）；
    publish 动作的日志写入与 published 判定同源，建草稿后徽章即时点亮
  - /api/article 的 checks = frontmatter 级 run_checks + 渲染级 render_checks
    （§15.1 详情视图「check + render_checks 报告逐条着色」），逐条 {level,message}
  - ?root= 接受 root 相对路径或指向 root 内的绝对目录（前端 location.search
    透传形态；越界一律 400），返回的 path 仍相对服务器 root
  - make_server 的 db 参数供集成阶段预留（§15.2 契约当前无 DB 端点），当前不消费

包结构（第八期重构 R2，原单文件 studio.py 拆分、行为零变更；Phase A 再将
routing 拆为 scan/payload/health/annotations、actions 编排下沉 services 并拆
出 edit_actions/fetch_actions，行为零变更）:
  server.py       _make_handler/ThreadingHTTPServer 装配（make_server 在此）
  routing.py      GET 路由表与分派 + 404（载荷构造在 payload/health）
  scan.py         文章扫描与状态探测（发现/产物标记/发布日志定位）
  payload.py      GET 端点载荷装配（队列/详情/渲染/平台/统计/任务/R7 编辑）
  health.py       /api/health 环境健康面板数据面（§十七 F20）
  annotations.py  批注 sidecar 与正文切分/保存（R7，供 actions 复用）
  security.py     穿越防护/脱敏/confirm 校验 + 共享底层（被依赖叶子）
  actions.py      POST 动作路由表+薄壳（Phase A 调 services；publish_flow
                  补丁锚点保留在此）
  edit_actions.py R7 编辑环节动作：save/批注增删/按批注 AI 改写（edit 数据面
                  在 annotations.py，AI 执行体在 services.annotate_service）
  fetch_actions.py §十七 F21 抓取动作（SSRF 提交期门=actions._assert_fetch_url，
                  执行体在 services.fetch_service）
  assets.py       /asset 静态路由 + /api/meta + ui.html 读取 + /assets/* 产物静态面
  ui.html         前端壳（M1 双层产物：壳内联八视图 HTML + 引用 assets/core.js|css）
  assets/         前端产物目录（core.js 全部 JS / core.css 全部 CSS；随包发布）

迁移兼容（重构正典 §七）：旧 import 路径（musespiral.studio.make_server 及测试用到
的 _sanitize_error/_render 等模块级符号）经本 __init__ re-export 保持可用，
不删；打补丁请瞄准定义子模块（如 monkeypatch musespiral.studio.actions.publish_flow）。
"""
from __future__ import annotations

# ---- 兼容 re-export：旧 musespiral.studio.* 顶层符号（§七 迁移兼容，不删）----
from .actions import (
    _ACTION_ROUTES,
    _action_adapt,
    _action_pack,
    _action_publish,
    _assert_asset_bounds,
    publish_flow,  # 旧 studio.publish_flow import 路径兼容（补丁请打在 .actions 上）
)
from .assets import (
    _IMG_SRC_RE,
    _STATIC_TYPES,
    _UI_RESOURCE,
    _asset_response,
    _asset_url,
    _meta_payload,
    _read_ui_html,
    _rewrite_local_images,
)
from .payload import (
    _PLATFORM_NAMES,
    _article_html,
    _article_payload,
    _article_review,
    _articles_payload,
    _platforms_payload,
    _render,
    _stats_payload,
)
from .routing import _GET_ROUTES, _get_response
from .scan import (
    _DERIVED_SUFFIXES,
    _PLATFORM_CODES,
    _article_flags,
    _has_pack,
    _has_publish_record,
    _iter_article_files,
    _log_path,
    _paired_note,
    _paired_review,
    _variants,
)
from .security import (
    _HTML_CTYPE,
    _HttpError,
    _JSON_CTYPE,
    _MAX_ERROR_CHARS,
    _PATH_TOKEN_RE,
    _SECRET_RE,
    _StudioContext,
    _error_payload,
    _json_bytes,
    _mask_paths,
    _mtime_date,
    _query_str,
    _rel,
    _require_confirm,
    _require_str,
    _resolve_dir_arg,
    _resolve_file,
    _resolve_inside,
    _sanitize_error,
)
from .server import ThreadingHTTPServer, _make_handler, make_server

__all__ = [
    "make_server",
    "ThreadingHTTPServer",
]
