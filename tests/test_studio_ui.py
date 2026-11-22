"""Studio 前端结构测试（前端重构正典 Phase 1~4 + M1 前端多文件化 2026-11-18）。

读 M1 双层产物：壳 ui.html（HTML 骨架+视图内联+assets 引用）、
assets/core.js（全部 JS）、assets/core.css（全部 CSS）——断言按标记类型
分文件读取（比单文件时代的「全在一个产物里找」更准）：
  - _source() → 壳：HTML 结构标识（id/data-*/视图 section/静态文案）
  - _js()     → core.js：函数/状态机/fetch 契约字面量
  - _css()    → core.css：token/组件类选择器/响应式规则
不启动服务器、不访问网络、不读写 ~/.musespiral。改前端请改源文件
（ui.tmpl.html/ui.views/ui.css/ui.js）后重建（conftest 会话级已构建），
勿手改产物。

校验范围（2026-11-14 前端重构 Phase 1 起，Phase 3 详情视图 / Phase 4 扩展）：
  - 零外联硬门（M1 升级）：壳+assets 全扫——无 http(s) 引用、无协议相对
    引用、无 @import/url()、所有 <script src>/<link href> 必须 assets/
    相对路径（防 CDN 化）；体积三锚（壳≤48KiB/总量≤400KiB/单文件≤208KiB
    +触发条款）在 tests/test_build_ui.py
  - 设计系统 token 全量（色彩/状态色/字体/六级字号/间距/圆角/阴影）
  - 暗色模式变量覆写（prefers-color-scheme: dark）
  - 38 个 monoline SVG 图标集（Phase 1 24 个 + Phase 2 docs + P1 shell 5 +
    D2 素材类型 6 + PL-3 插件 2）
  - 全局框架：顶栏 48px / 底栏 32px / 三视图结构与导航
  - 基础组件类：btn 四变体 / badge 三色 / card / modal / toast / input / tabs / chips
  - Phase 2 队列视图：卡片网格（2-4 列响应式）/ 封面区 / 印章徽章 / 产物行 /
    检查摘要 / 筛选 chips 下移（全部/草稿/待审/已审/已发布/有问题）/ 骨架屏 /
    拖放 .md 建稿 / 空状态（插画+说明+行动引导）
  - Phase 3 详情视图：编辑 Tab（默认）分屏（textarea+高亮 overlay+行号 | 677px
    实时预览）/ 双侧同步滚动 / 底部状态栏（字数/保存时间/AI 档案）/ ⌘S 保存 /
    划词批注气泡（替代行号点击，POST /api/annotations/add）/ 批注侧栏（时间/
    按批注修改（全文）→annotate_rewrite/删除）/ 变体 Tab 画廊（平台/标题/字数/本地校验/
    预览全文/重新生成/生成物料包/四平台一键）/ 检查 Tab 三分组（格式/渲染/事实
    自检；FAIL 默认展开；WARN/FAIL 附 CLI 修复命令）
  - Phase 4 仪表盘数据故事（§4.4）：统计瓷砖（已发布/待审/总阅读/平台数）+
    纯 CSS 近 30 天趋势柱图（div height %）+ 按平台水平条形（div width %）+
    环境健康独立卡片（未就绪附 CLI 修复命令）；任务面板升级（§5.4：状态图标
    SVG/进度条 phase i-n/相对时间/查看/重试/清空已完成——清空仅收起 done 行，
    failed 常驻保留重试；面板级过滤，jobs.jsonl 留档）；命令面板（⌘K：文章
    搜索/生成变体/创建草稿/检查/跳转；↑↓/Enter/Esc 键盘导航）；⌘⏎ 主操作 /
    ⌘1-4 切 Tab / 队列 ↑↓/Enter 卡片导航（§6.1）
  - fetch 路径集合与 §15.2 契约逐端点一致，动作请求体含 confirm:true
  - 自定义 confirm 弹层（非 window.confirm）与 toast 结构
  - 第十二期 F30「发现」视图：第三导航 Tab / 候选卡片（分数三档 dc-bar +
    四维分解 pbar）/ 来源筛选 chips / 生成骨架按钮与 drafted 状态 / ⌘K 条目
  - 第十八期 F48-F52「创作」视图（M3）：第四导航 Tab / 三栏工作区（素材池+
    章节勾选树+预算仪表 | 分块三态裁决 | 轮次时间线+N-1↔N 按块对照）/
    再生成走 job 面板 / 采纳入稿 confirm / R2 共享渲染层锚滤除 / SVG 图标
    TAG 公共前缀（M3 体积回收：monoline 属性随包装函数单点持有）
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
UI = STUDIO / "ui.html"
CORE_JS = STUDIO / "assets" / "core.js"
CORE_CSS = STUDIO / "assets" / "core.css"
VIEWS_JS = STUDIO / "assets" / "views"
VIEWS_HTML = STUDIO / "ui.views"

# §15.2 API 契约逐端点（路径不含 query string）；/api/meta 为 §16 F19-2 新增；
# /api/action/batch_adapt 为第八期轮次四新增（单引号字面量走本契约，重发路径为模板串不计入）；
# /api/health 与 /api/action/fetch 为第九期 F20/F21 新增（fetch 含重发路径字面量，集合去重）；
# Phase 3 零新增端点（变体取数复用 /api/article/body 模板字面量）；
# /api/discover 与 /api/action/discover_draft 为第十二期 F30 新增（发现视图）；
# /api/action/sync_stats 为第十四期 F39 新增（仪表盘健康卡「数据」行回采按钮）；
# /api/settings 与 /api/settings/kb、/api/settings/ai 为第十八期 F46/F47 新增
# （设置面：GET 两域只读 + kb/ai 域写，M1 Group A）
CONTRACT_PATHS = {
    "/api/articles",
    "/api/article",
    "/api/article/html",
    "/api/article/review",
    # N-5 平台适配并排预览（2026-11-21，契约 §3.22）：views/article.js 消费
    # 的物料包清单/单平台 payload+diff 读面（@view:article 块，进 _js() 语料）
    "/api/article/pack",
    "/api/platforms",
    "/api/stats",
    "/api/meta",
    "/api/action/adapt",
    "/api/action/pack",
    "/api/action/publish",
    "/api/action/batch_adapt",
    "/api/health",
    "/api/action/fetch",
    # FEAT-P1B 写作节奏（2026-11-23）：洞察「写作节奏」卡数据面（纯读 GET）
    "/api/writing_stats",
    "/api/discover",
    "/api/action/discover_draft",
    "/api/action/sync_stats",
    "/api/settings",
    "/api/settings/kb",
    "/api/settings/ai",
    # /api/compose* 为第十八期 F48-F52 新增（创作台：两 GET + 五 POST 动作，M3 UI）
    "/api/compose",
    "/api/compose/session",
    "/api/compose/new",
    "/api/compose/add",
    "/api/compose/remove_source",  # F-C 2026-11-18：素材撤池
    "/api/compose/generate",
    "/api/compose/decide",
    "/api/compose/adopt",
    # N-1 小说前端（2026-11-20）：前情提要/人物卡六 action 统一端点（E-5 既有端点，
    # 前端首次消费；N-1 零新增——契约锁数不变，见 test_compose_novel）
    "/api/compose/novel-summary",
    # FEAT-2 智能组稿（2026-11-22）：三步向导统一端点（探测=语义搜候选 /
    # 确认=建会话+素材入池+AI 三段式大纲）+ 向导「进入工坊」前的大纲整表覆写
    # （action=replace——G-T2 既有端点的新 action 形态）
    "/api/compose/smart-start",
    "/api/compose/outline",
    # P20 M3 素材视图收编（发现→素材）：分诊/删除连坐/源连接/ingest 端点；
    # /api/discover/star 后端零触碰保留（收编过渡期），UI 不再调用故出契约
    "/api/materials",
    "/api/materials/detail",
    "/api/materials/search",  # FEAT-1 语义搜索（关键词/语义/混合三模式统一读面）
    "/api/materials/triage",
    "/api/materials/delete",
    "/api/sources",
    "/api/sources/verify",
    "/api/sources/enabled",
    "/api/action/ingest",
    # P3/P4 前端补完（2026-11-16）：refine 两形态（preview/adopt）；/api/plan 排期周历只读
    "/api/action/refine",
    "/api/plan",
    # M3 多模态+B4a 前端接线批（2026-11-16）：上传二进制旁路/视频登记/集合五动作合并端点/
    # 文件字节流/标签频次表（file GET 与 tags GET 均为新增消费面）；
    # /api/ingest/{clip,crawl}=B4a 剪藏预览→采纳与手动爬取入口
    "/api/materials/upload",
    "/api/materials/register",
    "/api/materials/collections",
    "/api/materials/file",
    "/api/materials/tags",
    # N-2 素材闭环前端批（2026-11-21）：spark ⌘⇧I 浮层保存 + 文章反哺三路径提交
    "/api/materials/spark",
    "/api/materials/from-article",
    "/api/ingest/clip",
    "/api/ingest/crawl",
    # Phase C F71 封面工坊（2026-11-17）：视觉设计面板换调 /api/visual 族真预览
    # （render 两段式/save→export 导出链/preset 保存 + presets 拉取）；
    # /api/action/cover 保留为 CLI 对齐面（后端零触碰，前端无调用方）。
    # Phase C F72 社媒图编辑器（2026-11-17）：+GET /api/visual/design（design.json
    # +白名单复验 SVG 载入面；无稿 404→前端缺省稿走 render）
    "/api/visual/design",
    "/api/visual/templates",  # F-B 2026-11-18：色板 slot_hexes 数据面
    "/api/visual/render",
    "/api/visual/save",
    "/api/visual/export",
    "/api/visual/preset",
    "/api/visual/presets",
    # Phase C/D 入口 C（2026-11-17）：洞察选题雷达卡消费 GET /api/topic_radar
    # （Phase D F78.4 读面，test_architecture.CONTRACT_GET_EXACT 已登记；零新增端点）
    "/api/topic_radar",
    # Phase C/D 入口 B（2026-11-17）：建议标签消费 POST /api/action/tags_suggest
    # （Phase D F77 既有端点，test_architecture.CONTRACT_POST_EXACT 已登记；零新增端点）
    "/api/action/tags_suggest",
    # Phase C/D 入口 A（2026-11-17）：AI 综合消费 agent 族三端点（Phase D F75.4/F76.4
    # 既有，test_architecture.CONTRACT_POST_EXACT + CONTRACT_GET_EXACT 均已登记）
    "/api/agent/run",
    "/api/agent/runs",
    "/api/agent/adopt",
    # G1 组合批（2026-11-17）：文生图三动作 + 预览字节流（A-2 临时文件消费面，
    # 前端 <img src> 直指）+ E1 金路径触发 + F1 两读面
    "/api/action/generate_image",
    "/api/action/adopt_generated",
    "/api/action/media_verify",
    # 注：GET /api/media/preview 走 <img src>（不进 fetch 字面量集合）——
    # test_architecture.CONTRACT_GET_EXACT 已锁该 GET 面
    "/api/action/pipeline",
    "/api/ai/usage",
    "/api/ai/budget",
    # R4 #books 书籍视图 + R3.5 提炼触点批（2026-11-18）：引擎灯读面（§3.9
    # 既有 GET）+ §3.10 三 GET（info/toc 代理 / original 原文按需加载）+
    # 选章入素材库 POST（book_fetch job 提交面）；步级明细复用既有
    # /api/agent/runs，job 轮询走 `/api/jobs/${id}` 模板串（不入单引号集合）。
    # POST /api/book/search 本批未接（搜索态占位，结果渲染 R4 批）——不入前端契约。
    "/api/booksource/engine",
    # R4 导入弹窗批（2026-11-18，书源实弹修复与导入弹窗设计 §三）：解析预览
    # POST /api/booksource/import（零落库）+ 落库 POST /api/booksource/import_apply
    # （selections 按 URL + importKeep 三开关）——订阅态「导入书源」入口两步链。
    "/api/booksource/import",
    "/api/booksource/import_apply",
    "/api/book/info",
    "/api/book/toc",
    "/api/book/original",
    "/api/materials/from-chapters",
    # 集成接线轮（2026-11-18）：入库进度条「取消」=POST /api/jobs/cancel
    # （协作式取消，R3.5 正典 §2.2——已完成章保留/未完成章放弃+终态 M/N toast）
    "/api/jobs/cancel",
    # PL-3 插件系统前端批（2026-11-20，插件系统设计 §十二）：插件清单 GET /api/plugins、
    # 重扫 POST /api/plugins/refresh、流水线列表/保存 GET|POST /api/pipelines、运行
    # POST /api/pipelines/run（PL-3 复核修复批接驳：动态段 /{id}/run 改精确路径+
    # body id——见契约 §3.18；后端已接驳，前端消费面与路由面的对拍锁在
    # test_architecture.py::test_frontend_api_literals_covered_by_backend_surface）。
    # PL-6 批 E-4 接驳（2026-11-20）：转写编辑保存 POST /api/pipelines/runs/segments
    # （body run_id——PL-3 先例）；仍走模板串的动态 GET：/api/plugins/${id}/check、
    # /api/pipelines/${id}、/api/pipelines/runs/${...}、/runs/${...}/segments、
    # /runs/${...}/audio（后端动态前缀族承载）。
    "/api/plugins",
    "/api/plugins/refresh",
    "/api/pipelines",
    "/api/pipelines/run",
    "/api/pipelines/runs/segments",
    # P1 AI 辅助创作增强批（2026-11-23，契约 §3.26）：质量循环 job 提交 + 标题
    # A/B 三风格（generate 同步 / apply 写回，mode 分流同一端点）——views/
    # article.js 操作 tab 两动作组消费面
    "/api/action/quality_loop",
    "/api/action/title_ab",
}

# 38 个 monoline 图标（Phase 1 正典 §3.4 的 24 个 + Phase 2 追加 docs 空状态插画；
# comment 为批注场景预置。清单即唯一事实源——下方清单项数须等于
# test_svg_icon_set_complete 的 38 断言，改动两边同批）
ICON_NAMES = [
    "file", "edit", "eye", "check", "warning", "error", "refresh", "download",
    "upload", "publish", "star", "clock", "tag", "user", "search", "settings",
    "bell", "close", "comment", "arrow-left", "arrow-right", "plus", "trash", "external-link",
    "docs",
    # P1 shell 集成（2026-11-16）新增 5 枚（重构设计 §1.3 图标纪律 monoline）：
    # sun/moon/monitor=主题三态切换器；command=顶栏 ⌘K 入口；panel-left=rail 折叠钮
    "sun", "moon", "monitor", "command", "panel-left",
    # D2 素材类型图标 6 枚（终评 §三 P1-D2，2026-11-16）：
    # image=矩形+山+日 / video=播放三角 / link=链两环 / radar=同心弧+点 /
    # globe=地球经纬 / derived=菱形+箭头（text 复用 docs）
    "image", "video", "link", "radar", "globe", "derived",
    # PL-3 插件系统 2 枚（2026-11-20）：play=运行按钮/日志行；flow=流水线节点链
    # （rail「工具」组导航 + 画布箭头）
    "play", "flow",
]


def _source() -> str:
    """读壳 ui.html（ARCH-1 起非首屏视图在壳内是**空占位** <section>，真骨架
    由 assets/views/<view>.html.js 进入视图时注入）；缺失即失败（不静默跳过）。

    ARCH-1 等价域（2026-11-21 壳 HTML 外移）：断言语料=「占位替换回源骨架」的
    组装 DOM——每个空占位 section 行替换为 ui.views/<view>.html 的完整源（与
    拆分前壳内联形态逐字节等价；首屏 publish 本就内联，原样保留）。三千余行
    结构断言的等价域由此保持，无需逐条改写；需要验「壳里真的没有骨架」的
    断言读 _shell_bare()。
    """
    assert UI.exists(), f"Studio 壳不存在: {UI}"
    shell = UI.read_text(encoding="utf-8")
    out_lines = []
    for line in shell.split("\n"):
        m = re.fullmatch(r'<section id="view-([a-z0-9-]+)"[^>]*></section>', line)
        if m:
            name = m.group(1)
            path = VIEWS_HTML / f"{name}.html"
            assert path.exists(), f"懒视图源缺失: {path}"
            out_lines.append(path.read_text(encoding="utf-8").rstrip("\n"))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def _shell_bare() -> str:
    """裸壳（ARCH-1 专用域）：只读 ui.html 本体——占位/无骨架断言用。"""
    assert UI.exists(), f"Studio 壳不存在: {UI}"
    return UI.read_text(encoding="utf-8")


def _js() -> str:
    """JS 全语料 = core.js + 视图 views/*.js 合并（M3 拆分）。

    M3 把原单文件 JS 拆成 core（工具/路由/状态/命令面板/toast/jobs）+ 各视图
    （各自的 render* + 专属 helper；PL-3 批起 11 视图）。绝大多数结构断言关心
    「函数/字面量在 JS 语料里存在」而非具体落在哪个文件，合并读保持这些断言的
    等价域（=拆分前单文件）。需要验证视图归属的断言另用 `_view_js(name)` / `_core_js()`。
    ARCH-1（2026-11-21）：assets/views/ 下另有 <view>.html.js（HTML 骨架注入器，
    非行为脚本）——HTML 语料经 `_source()` 的占位替换覆盖，此处排除之。
    """
    assert CORE_JS.exists(), f"core.js 产物不存在: {CORE_JS}"
    files = [CORE_JS] + sorted(
        p for p in VIEWS_JS.glob("*.js") if not p.name.endswith(".html.js"))
    # UIX-3（2026-11-22）：material 三拆 → 13 行为脚本（10 单文件视图 + material 三子段）
    assert len(files) == 14, f"视图产物应为 13 个，实为 {len(files) - 1}: {files}"
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _mat_js() -> str:
    """material 三拆产物并集（UIX-3）：core/drawer/media 三子段合并读。

    多数 material 断言关心「标记在素材视图 JS 语料里存在/不存在」而非落在哪个
    子段（与 _js() 的 M3 合并读同构）；归属断言另用 _mat_part("drawer") 形态。
    """
    parts = sorted(VIEWS_JS.glob("material-*.js"))
    assert len(parts) == 3, f"material 三拆产物应为 3 个，实为 {parts}"
    assert not (VIEWS_JS / "material.js").exists(), "单文件 material.js 应已退役"
    return "\n".join(p.read_text(encoding="utf-8") for p in parts)


def _mat_part(name: str) -> str:
    """读单个 material 子段产物（UIX-3 归属断言：core/drawer/media）。"""
    path = VIEWS_JS / f"material-{name}.js"
    assert path.exists(), f"material 子段产物不存在: {path}"
    return path.read_text(encoding="utf-8")


def _core_js() -> str:
    """只读 core.js（core 专属断言：工具/路由/状态/懒加载器/图标）。"""
    assert CORE_JS.exists(), f"core.js 产物不存在: {CORE_JS}"
    return CORE_JS.read_text(encoding="utf-8")


def _view_js(name: str) -> str:
    """读单个 assets/views/<name>.js（视图归属断言：render*/专属 helper/bind）。"""
    path = VIEWS_JS / f"{name}.js"
    assert path.exists(), f"视图产物不存在: {path}"
    return path.read_text(encoding="utf-8")


def _css() -> str:
    """CSS 全语料 = core.css + views/*.css 合并（ARCH-1 ② CSS 按视图拆分）。

    ②拆分把视图专属样式自 core.css 拆到 assets/views/<view>.css（懒加载随视图
    注入；publish 首屏样式留 core.css）。绝大多数结构断言关心「选择器/规则在
    CSS 语料里存在」而非具体落在哪个文件，合并读保持这些断言的等价域（=拆分前
    单文件，与 _js() 的 M3 先例同构）。core.css 专属断言（token/暗色覆写）继续
    直接读 CORE_CSS。
    """
    assert CORE_CSS.exists(), f"core.css 产物不存在: {CORE_CSS}"
    parts = [CORE_CSS.read_text(encoding="utf-8")]
    parts.extend(p.read_text(encoding="utf-8")
                 for p in sorted(VIEWS_JS.glob("*.css")))
    return "\n".join(parts)


def _shell_js() -> str:
    """壳+core.js 合并视图：同函数内 HTML 静态标记与 JS 标记并存的断言域。"""
    return _source() + "\n" + _js()


def _js_css() -> str:
    """core.js+core.css 合并视图：同函数内 JS 与 CSS 标记并存的断言域。"""
    return _js() + "\n" + _css()


def _shell_css() -> str:
    """壳+core.css 合并视图：同函数内 HTML 静态标记与 CSS 标记并存的断言域。"""
    return _source() + "\n" + _css()


def _shell_js_css() -> str:
    """壳+core.js+core.css 合并视图：HTML/JS/CSS 标记全混的断言域（=单文件时代
    产物语料的等价物；标记各归其产物，仅语料形状合并）。"""
    return _source() + "\n" + _js() + "\n" + _css()


def _all() -> str:
    """全产物并集——壳 + core + views/*.js + **所有懒加载 assets**（2026-11-21
    AI-3 复核 fix-6 扩域）。

    **否向断言的专用域**（2026-11-18 集成接线轮修正）：M1 分域读取把 32 条
    `"X" not in src` 的检查域从「全产物」窄化成「单一产物」（如 `'已停止跟踪'
    not in _js()`、`'.nav-tab' not in _shell_css()`）——退役文案/旧类名若以
    JS 字符串形态回流到未被读的产物就抓不到了。而「X 不得出现」这类断言的
    本意与产物归属无关，M1 之前的等价域就是整份语料。故否向断言一律用本域，
    正向断言继续用分域（`_js()`/`_css()`/`_source()`，更精确）。

    **fix-6 假门修复**：此前本域=壳+core+views，漏掉 core.js:685 懒加载的
    assets/{iterations,quickstart,versions,tts,…}.js——窗口提示这类违例落在
    iterations.js 就永远扫不到（AI-3 批真实缺陷）。现补入 assets/ 顶层全部
    手写 .js/.css（排除 editor/ 第三方 CodeMirror 压缩产物——那些非本仓代码，
    且含 `prompt` 等字样会误伤）。负向断言域扩大只会更严，不放松既有约束。
    """
    assets = STUDIO / "assets"
    lazy = sorted(
        p for p in assets.iterdir()
        if p.is_file() and p.suffix in (".js", ".css")
    )
    return _shell_js_css() + "\n" + "\n".join(
        p.read_text(encoding="utf-8") for p in lazy
    )


class _Structure(HTMLParser):
    """收集 id、data-* 属性值与起始标签，供结构断言使用。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.attrs: dict[str, set[str]] = {}
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        pairs = dict(attrs)
        self.tags.append((tag, pairs))
        if pairs.get("id"):
            self.ids.append(str(pairs["id"]))
        for key, value in pairs.items():
            if key.startswith("data-") and value is not None:
                self.attrs.setdefault(key, set()).add(value)


def _structure() -> _Structure:
    parsed = _Structure()
    parsed.feed(_source())
    return parsed


# ── 零外联硬门（M1 升级：壳+assets 全扫）与壳形态 ────────


def test_shell_entry_document_form() -> None:
    """壳形态（M1）：doctype 开头 + 无内联脚本/样式代码体（全部 JS/CSS 在 assets/）
    + 静态 HTML 无 img src（封面走 mountCovers 运行时建 img）。"""
    src = _source()
    assert src.lstrip().lower().startswith("<!doctype html>")
    # 无内联 <script>（无 src 的 script 标签）——代码体全部外置
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", src, re.IGNORECASE) is None
    assert "<style" not in src.lower()
    # 静态 HTML 不带 img src（JS 模板串在 core.js，不进壳）
    assert re.search(r"<img[^>]*\bsrc=", src, re.IGNORECASE) is None


def test_ui_has_no_external_resource_references() -> None:
    """零外联硬门（M1 升级，正典 §六）：壳+assets 全扫。

    ① 无 http(s) 引用（无 CDN/webfont/外链图片，图标全部内嵌 SVG）；
    ② 无协议相对 // 引用；③ 无 @import 与 CSS url()；
    ④ **所有 <script src>/<link href> 必须 assets/ 相对路径**（防被改成
    CDN——构建期 render 已有同款机器断言，此处产物面对拍双保险：两门共用
    同一判定口径 `_tag_refs`，引号三态 × 大小写全覆盖，见其 docstring）；
    ⑤ 壳恰好引用 core.css + core.js（首屏必需面，缺一即红），且**壳内不得
    出现任何 `<link>` 标签**（M1 前 = `"<link" not in src`；现由 `_tag_refs`
    抽出的引用 + 下方 link 标签计数两条共同锁死——只锁引用清单会漏掉无
    href 的 <link rel=preload> 形态）。
    """
    for blob in (_source(), _js(), _css()):
        assert re.search(r"https?://", blob, re.IGNORECASE) is None
        assert re.search(r"(?:src|href)\s*=\s*[\"']//", blob, re.IGNORECASE) is None
        assert "@import" not in blob
        # F72 批（2026-11-17）：FileReader.readAsDataURL( 含子串 "URL(" 被
        # 无边界大小写不敏感匹配误伤（非 CSS url() 外链、零网络）——加词边界
        # lookbehind 收窄回 CSS url() 本意；裸 url( 仍会命中（下方对拍）。
        assert re.search(r"(?i)(?<![A-Za-z0-9])url\s*\(", blob) is None
    assert re.search(r"(?i)(?<![A-Za-z0-9])url\s*\(", "background:url(x)") is not None
    shell = _source()
    refs = _tag_refs(shell)
    assert refs, "壳缺少任何资源引用（M1 壳必须引用 assets/）"
    for url in refs:
        assert url.startswith("assets/"), f"壳内引用须为 assets/ 相对路径（零外联红线）: {url}"
    assert refs == ["assets/core.css", "assets/core.js"], refs
    # 壳内 <link> 标签数恒为 1（唯一那条 stylesheet）——M1 前 `"<link" not in src`
    # 的等价锁：无 href 的 <link rel=preload/preconnect> 不会被上面的引用清单抓到。
    assert shell.lower().count("<link") == 1, (
        f"壳内 <link> 标签数为 {shell.lower().count('<link')}，应为 1（仅 core.css 那条）")


# 壳内引用提取（与 scripts/build_ui.py 的 `_tag_refs` 同口径：标签级扫描 + 属性级
# 提取，引号三态双/单/无 + 属性名大小写全覆盖）。
# 2026-11-18 集成接线轮修正缺陷：原产物面实现只认「双引号 + 小写属性名」，与
# 构建期门同病——`src='https://…'`（单引号）、`src=https://…`（无引号）、
# `SRC="https://…"`（大写）三类违规在**两道门**里都是 PASS，doc 与代码注释里的
# 「双保险」名不副实。两处现共用同一判定口径；构建期版在 build_ui.py，此处
# 内联复刻（避免测试 import 构建脚本造成的路径耦合）。
_TAG_OPEN_RE = re.compile(r"<(?:script|link)\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ATTR_REF_RE = re.compile(
    r"""(?:^|[\s/])(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)


def _tag_refs(shell: str) -> list[str]:
    """壳内全部 <script>/<link> 的 src/href 值（引号三态 + 大小写无关）。"""
    refs: list[str] = []
    for tag in _TAG_OPEN_RE.finditer(shell):
        for attr in _ATTR_REF_RE.finditer(tag.group(1)):
            refs.append(attr.group(1) or attr.group(2) or attr.group(3) or "")
    return refs


def test_ui_has_no_third_party_libraries() -> None:
    """零三方库（壳+assets 全扫）：不引 react/vue/jquery/axios/bootstrap/tailwind，
    不用 XMLHttpRequest（统一走 fetch）。"""
    for blob in (_source(), _js(), _css()):
        assert re.search(r"\b(react|vue|jquery|axios|bootstrap|tailwind)\b", blob, re.IGNORECASE) is None
        assert "XMLHttpRequest" not in blob


# ── 设计系统 token（Phase 1：纸墨视觉语言）────────────────


def test_design_tokens_light_mode_complete() -> None:
    """P1 token v2（重构设计 §1.2/§1.3 表逐值比对）：8 语义色槽+三级墨/三层纸面/
    双边线+可视化序列+7 档字号+8 档间距+圆角四档+阴影四档+动效+层级+布局常量。"""
    src = _css()
    root = re.search(r":root\{[^}]*\}", src)
    assert root is not None, "缺少 :root token 块"
    tokens = root.group(0)
    # 色彩语义槽（一色一语义，亮色值=设计 §1.2 表）
    for pair in (
        "--vermilion:#c0392b",   # 行动·朱砂
        "--indigo:#2c5f8a",      # 信息·青黛
        "--jade:#1a7038",        # 通过·竹绿
        "--amber:#9c5700",       # 注意·琥珀
        "--crimson:#8e2434",     # 危险·绛
    ):
        assert pair in tokens, pair
    # 三级墨 + 三层纸面 + 双边线
    for pair in (
        "--ink-1:#1a1a1a", "--ink-2:#6b6b6b", "--ink-3:#9a948c",
        "--paper-0:#faf9f7", "--paper-1:#ffffff", "--paper-2:#f1efec",
        "--line-1:#e8e6e3", "--line-2:#d5d2ce",
    ):
        assert pair in tokens, pair
    # 可视化序列（§1.2：viz-1..4 引用语义槽自动跟随暗色，viz-5 石板固定值）
    for pair in ("--viz-1:var(--indigo)", "--viz-2:var(--vermilion)",
                 "--viz-3:var(--jade)", "--viz-4:var(--amber)", "--viz-5:#5d6d7e"):
        assert pair in tokens, pair
    # 派生混色收编旧 --t8/--t4
    for pair in (
        "--wash:color-mix(in srgb,var(--ink-1) 8%,transparent)",
        "--tint:color-mix(in srgb,var(--ink-1) 4%,transparent)",
    ):
        assert pair in tokens, pair
    # 旧 --color-* 命名全量退役（P1 体积回收+语义迁移完成）
    for gone in ("--color-accent", "--color-text", "--color-bg", "--color-card",
                 "--color-border", "--color-muted", "--color-error", "--color-success",
                 "--color-warning", "--t8:", "--t4:", "--fs-code"):
        assert gone not in src, gone
    # 字体
    assert '--font-body:-apple-system,"SF Pro Text","PingFang SC"' in tokens
    assert '--font-mono:"SF Mono","JetBrains Mono"' in tokens
    # 7 档字号（§1.3：新增 display 34 与 micro 11）
    for pair in (
        "--fs-display:34px",
        "--fs-h1:28px",
        "--fs-h2:22px",
        "--fs-h3:17px",
        "--fs-body:15px",
        "--fs-caption:13px",
        "--fs-micro:11px",
    ):
        assert pair in tokens, pair
    # 8 档间距（8px 栅格 + 2px 微档 + 64 视图顶留白）
    for pair in (
        "--space-2xs:2px",
        "--space-xs:4px",
        "--space-sm:8px",
        "--space-md:16px",
        "--space-lg:24px",
        "--space-xl:48px",
        "--space-xxl:64px",
    ):
        assert pair in tokens, pair
    # 圆角四档 + 阴影四档（含选中内凹通道）
    for pair in (
        "--radius-btn:6px", "--radius-card:8px", "--radius-modal:12px", "--radius-pill:999px",
        "--shadow-sm:", "--shadow-md:", "--shadow-lg:",
        "--shadow-inset:inset 0 1px 2px rgba(0,0,0,.06)",
    ):
        assert pair in tokens, pair
    # 动效（§1.3：三档时长+缓动）
    for pair in (
        "--dur-fast:120ms", "--dur-base:180ms", "--dur-slow:240ms",
        "--ease-out:cubic-bezier(.2,.7,.3,1)",
    ):
        assert pair in tokens, pair
    # 层级（归一 5/20/30/50/60/70 字面量 + cmdk 最高）
    for pair in (
        "--z-pop:10", "--z-sticky:20", "--z-topbar:30", "--z-menu:50",
        "--z-modal:60", "--z-toast:70", "--z-cmdk:80",
    ):
        assert pair in tokens, pair
    # 布局常量（topbar/statusbar 保留 + rail/aside 新增）
    for pair in (
        "--topbar-h:48px", "--statusbar-h:32px",
        "--rail-w:220px", "--rail-w-min:56px", "--aside-w:320px",
    ):
        assert pair in tokens, pair


def test_dark_mode_variable_overrides() -> None:
    """P1 三态主题 CSS 面（重构设计 §1.2）：data-theme=dark 属性块 + prefers-color-scheme
    auto 块（html:not([data-theme=light])）双路径全量覆写；暗值逐对比对 §1.2 表。

    亮暗切换零依赖：手动切换只写 <html data-theme>；auto 态暗色变量仍由
    @media (prefers-color-scheme:dark) 提供（现状行为保留）。
    """
    src = _css()
    # 手动 dark 覆盖块
    dark = re.search(r'html\[data-theme="dark"\]\{[^}]*\}', src)
    assert dark is not None, "缺少 html[data-theme=dark] 手动暗色覆盖块"
    block = dark.group(0)
    # auto 跟随系统块（light 手动选择时豁免）
    auto = re.search(r'@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*'
                     r'html:not\(\[data-theme="light"\]\)\{[^}]*\}', src)
    assert auto is not None, "缺少 auto 态 prefers-color-scheme 暗色块（html:not([data-theme=light])）"
    ablock = auto.group(0)
    # 两路径暗值一致（同一组暗色 token，逐值=设计 §1.2 表暗列）
    dark_pairs = (
        "--vermilion:#e8604f", "--indigo:#7fb3d8", "--jade:#3ecf6f",
        "--amber:#f0a132", "--crimson:#ff7b89",
        "--ink-1:#e6e2dc", "--ink-2:#a09a92", "--ink-3:#6e6862",
        "--paper-0:#211f1e", "--paper-1:#2a2827", "--paper-2:#1a1918",
        "--line-1:#3a3a3a", "--line-2:#4a4845",
    )
    for pair in dark_pairs:
        assert pair in block, f"手动 dark 块缺 {pair}"
        assert pair in ablock, f"auto dark 块缺 {pair}"
    # 原生控件暗色可达：dark 态 color-scheme:dark
    assert "color-scheme:dark" in block
    # 语义分离：暗色 crimson（危险）≠ vermilion（行动）——删除确认与主按钮可区分
    vm = re.search(r"--vermilion:(#[0-9a-fA-F]{6})", block)
    cr = re.search(r"--crimson:(#[0-9a-fA-F]{6})", block)
    assert vm and cr, "暗色块缺少 vermilion/crimson 色值"
    assert vm.group(1).lower() != cr.group(1).lower(), (
        f"暗色 --crimson 与 --vermilion 同值: {vm.group(1)}"
    )


def test_native_controls_follow_color_scheme() -> None:
    """表单原生控件暗色可达：:root 声明 color-scheme + 编辑器透明底方案（token v2 迁移）。"""
    src = _css()
    assert re.search(r":root\{color-scheme:light dark;", src) is not None
    edit = re.search(r"#edit-area\{[^}]*\}", src)
    assert edit is not None
    assert "caret-color:var(--ink-1)" in edit.group(0)
    assert "color:transparent" in edit.group(0)
    overlay = re.search(r"#edit-hl\{[^}]*\}", src)
    assert overlay is not None, "缺少高亮 overlay #edit-hl"
    assert "color:var(--ink-1)" in overlay.group(0)
    assert "#edit-area::selection" in src


# ── SVG 图标集（38 个 monoline）────────────────────────────


def test_svg_icon_set_complete() -> None:
    """38 个 monoline SVG 图标：ICONS 对象存 path 数据 + TAG 共享前缀包装 + icon(name)。

    M3 体积回收（2026-11-15）：图标的 ``<svg viewBox …stroke-width="1.7">``
    公共前缀抽成 TAG 常量、由 icon() 统一包装（每图标省 ~76B）；monoline 规格属性
    随之从「每图标 ×25」变为「TAG ×1」——断言同步改为计数 1。圆角端点仍经 CSS
    （[data-icon] svg 规则）全局生效。P1 shell 集成（2026-11-16）+5：
    sun/moon/monitor/command/panel-left。
    """
    src = _js_css()
    assert len(ICON_NAMES) == 38  # Phase 1 的 24 + Phase 2 docs + P1 shell 5 + D2 素材类型 6 + PL-3 插件 2
    assert "var ICONS={" in src  # 内嵌 JS 对象 {iconName: pathData}
    # 共享前缀 + 包装函数：ICONS 值只存 path 数据，icon() 统一拼 <svg> 壳
    assert (
        "var TAG='<svg aria-hidden=\"true\" viewBox=\"0 0 24 24\" fill=\"none\" "
        "stroke=\"currentColor\" stroke-width=\"1.7\">';" in src
    )
    assert "function icon(name){return ICONS[name]?TAG+ICONS[name]+'</svg>':'';}" in src
    for name in ICON_NAMES:
        key = re.escape(name)
        # 值=path 数据（error/clock/user/search 以 <circle 开头，其余 <path）
        assert re.search(rf"(?:^|[\s{{,])(?:'{key}'|\"{key}\"|{key})\s*:\s*'<(?:path|circle)", src), name
    # monoline 规格：1.7px stroke（1.6–1.8 区间）/ currentColor / 24 网格——TAG 单点持有
    assert src.count('stroke-width="1.7"') == 1
    assert src.count('stroke="currentColor"') == 1
    assert src.count('viewBox="0 0 24 24"') == 1
    assert src.count('fill="none"') == 1
    # 圆角端点经 CSS 全局生效（不再逐图标内联）
    assert "stroke-linecap:round;stroke-linejoin:round" in src
    # 图标函数暴露到全局，功能脚本（健康面板 hrow / 队列卡片 I()）可复用
    assert "window.mdpubIcon=icon;" in src


def test_icons_mounted_via_data_icon_spans() -> None:
    """静态 HTML 用 <span data-icon> 占位，mountChrome() 经 icon() 注入 SVG。"""
    parsed = _structure()
    assert "data-icon" in parsed.attrs
    mounted = parsed.attrs["data-icon"]
    # 顶栏/导航/任务铃铛/空状态锚点均有图标占位
    for name in ("publish", "file", "star", "bell", "refresh", "arrow-left", "comment"):
        assert name in mounted, name
    src = _js()
    assert "function mountChrome(){" in src
    assert "mountChrome();" in src  # 装载入口被调用
    assert "icon(nodes[i].getAttribute('data-icon'))" in src  # 注入走 icon(name)


# ── 三视图与全局框架 ──────────────────────────────────────


def test_three_views_and_navigation() -> None:
    """P1 六视图（重构设计 §2.2 hash 表）：sources/material/compose/publish/insight
    五主视图 + #article 深链 + settings；旧名 queue/dashboard/discover 经 HALIAS 兼容。"""
    parsed = _structure()
    assert {"view-sources", "view-material", "view-compose", "view-publish",
            "view-insight", "view-article", "view-settings"} <= set(parsed.ids)
    assert {"sources", "material", "compose", "publish", "insight", "article", "settings"} \
        <= parsed.attrs["data-view"]
    # 导航可达五视图（settings 走顶栏齿轮）
    assert {"sources", "material", "compose", "publish", "insight"} <= parsed.attrs["data-nav"]
    src = _source()
    assert "musespiral Studio" in src
    assert {"root-path", "refresh-btn"} <= set(parsed.ids)
    # 旧视图容器名退役（P1 改名：queue→publish / dashboard→insight）
    assert "view-queue" not in src and "view-dashboard" not in src


def test_shell_topbar_and_statusbar() -> None:
    """全局框架（GB 中间态=顶栏 Tab 方案）：顶栏 48px（品牌/导航/任务铃铛）+
    底栏 32px（版本/服务地址/工作目录）。rail 终态见 P1 集成验收区段。"""
    parsed = _structure()
    src = _shell_css()
    assert {"jobs-wrap", "jobs-btn", "jobs-badge", "jobs-menu", "server-addr"} <= set(parsed.ids)
    # 顶栏/底栏尺寸 token 与类
    assert "--topbar-h:48px" in src and "--statusbar-h:32px" in src
    assert ".topbar{" in src and ".statusbar{" in src
    assert "musespiral v0.5.0" in src  # 底栏版本号（随 __version__ 收口同步）
    assert "root: " in src  # 底栏工作目录前缀


def test_ids_are_unique() -> None:
    parsed = _structure()
    assert len(parsed.ids) == len(set(parsed.ids)), "ui.html 存在重复 id"


# ── Phase 2 队列视图 ──────────────────────────────────────


def test_queue_view_structure() -> None:
    """P1 发布中心（重构设计 §3.4，原队列视图重组）：漏斗带+六档筛选+网格/列表双形态。

    渲染函数 renderQueue→renderPublish 改名；队列卡 id 全保留（e2e 锚点兼容）。
    """
    parsed = _structure()
    assert {"article-list", "queue-filters", "queue-empty", "queue-sub"} <= set(parsed.ids)
    # 漏斗带（§3.4）：四级计数+相邻级转化，段可点深链筛选档
    assert {"funnel-band", "pub-rows"} <= set(parsed.ids)
    assert "renderFunnel" in _js()
    # 网格/列表双形态（AList 三布局对位）：data-pview
    assert {"grid", "list"} <= parsed.attrs["data-pview"]
    # 六档筛选（正典 §4.2）：全部/草稿/待审/已审/已发布/有问题
    assert {"all", "draft", "pending", "reviewed", "published", "fail"} <= parsed.attrs["data-filter"]
    src = _shell_js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    for label in ("全部", "草稿", "待审", "已审", "已发布", "有问题", "暂无文章"):
        assert label in src, label
    # 每档筛选有独立计数
    for suffix in ("all", "draft", "pending", "reviewed", "published", "fail"):
        assert f"count-{suffix}" in src, suffix
    # 筛选语义：待审 = 无 FAIL/WARN 且 status=draft 且未发布；其余档按状态/标记
    assert "if(state.filter==='pending') return (checks.fail||0)===0&&(checks.warn||0)===0&&articleStatus(a)==='draft'&&!flags.published;" in src
    assert "if(state.filter==='draft') return articleStatus(a)==='draft';" in src
    assert "if(state.filter==='reviewed') return articleStatus(a)==='reviewed';" in src
    # 汇总行保留「共 N 篇」口径（e2e 断言锚点；pubCounts 聚合后 n.all）
    # 评审补批 P2-2：统计行与 fb-seg 同口径——草稿/待审/已审/已发布四段数字与
    # count-draft/pending/reviewed/published 芯片逐一相等（「有问题」是正交筛选项
    # 而非生命周期态，只留在 fb-seg 芯片，不再混入统计行；数字断言在 e2e）
    assert "'共 '+n.all+' 篇 · 草稿 '+n.draft+' · 待审 '+n.pending+' · 已审 '+n.reviewed+' · 已发布 '+n.published" in src
    assert "'共 '+n.all+' 篇 · 待审 '+n.pending+' · 有问题 '" not in all_src  # 旧口径退役
    # 卡片数据面：标题/日期/状态/检查/产物标记
    for field in ("item.title", "item.date", "item.status", "item.checks", "item.flags"):
        assert field in src, field
    for flag in ("f.review", "f.variants", "f.pack", "f.published"):
        assert flag in src, flag
    for tag in ("对照", "物料包", "已发草稿"):
        assert tag in src, tag


def test_queue_card_grid_layout() -> None:
    """Phase 2 卡片网格：2-4 列响应式 grid + 卡片五要素（封面/标题/徽章/产物行/检查摘要）。"""
    src = _js_css()
    # 网格：auto-fill 自适应（宽屏 4 列 → 中屏 3 → 平板 2 → 手机 1）
    assert ".card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(264px,1fr))" in src
    assert re.search(r"@media\s*\(max-width:\s*900px\)[^@]*\.card-grid\{grid-template-columns:repeat\(2,1fr\)\}", src)
    assert "@media (max-width:560px){.card-grid{grid-template-columns:1fr}}" in src
    # 卡片仍是 <a class="article-card">（e2e 选择器 #article-list a.article-card 兼容）
    assert "'<a class=\"article-card qcard\" href=\"'+href+'\">'" in src
    # ① 封面区：16:9；有 cover 走 /asset 缩略图（mountCovers 运行时建 img），无则 accent 色块+标题文字
    assert ".qcard-cover{position:relative;aspect-ratio:16/9" in src
    assert "data-cover=\"'+esc(coverRel)+'\"" in src
    assert "function mountCovers(){" in src
    assert "img.src='/asset?path='+encodeURIComponent(" in src
    assert ".qcover-block{position:absolute;inset:0" in src
    assert "qcover-text" in src  # 色块回退携带标题文字
    # ② 标题：H3 + 最多 2 行 ellipsis（-webkit-line-clamp:2）
    assert "'<h3 class=\"card-title qcard-title\">'" in src
    assert "-webkit-line-clamp:2" in src
    # ③ 状态徽章（印章风格胶囊 .badge.stamp）+ 时间
    assert "'<span class=\"badge stamp '" in src
    assert ".badge.stamp{letter-spacing:.5px" in src
    assert "qcard-time" in src
    # ④ 产物标记行：monoline SVG 图标 + 计数
    assert "function qArt(iconName,label,count){" in src
    assert "'<span class=\"qa\">'+I(iconName)" in src
    assert "function I(n){return typeof mdpubIcon==='function'?mdpubIcon(n):'';}" in src
    # ⑤ 检查状态摘要：FAIL/WARN/检查通过 三态
    for token in ("qcheck fail", "qcheck warn", "qcheck pass", "检查通过"):
        assert token in src, token
    # 变体平台代码 chips 保留 .tag.accent（e2e 断言锚点）
    assert "'<span class=\"tag accent\">'+esc(code)+Z" in src


def test_queue_skeleton_loading() -> None:
    """Phase 2 骨架屏：首载渲染 8 张灰块占位卡（div 非 a，不污染 e2e 卡片计数），数据到达整卡替换。"""
    src = _shell_js_css()
    assert "function renderSkeleton(){" in src
    assert "for(var i=0;i<8;i++){" in src  # 8 张占位卡
    # 占位卡是 div（aria-hidden），非可点击 <a>——骨架期不算文章卡
    assert "'<div class=\"article-card qcard skel\" aria-hidden=\"true\">'" in src
    assert "skel-block" in src and "skel-line" in src
    # 呼吸动画 + prefers-reduced-motion 降级
    assert "@keyframes skel-pulse{" in src
    assert "@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important" in src
    # 首次装载（含刷新重置 articlesLoaded=false）才显示骨架；后台强刷不闪骨架
    assert "if(!state.articlesLoaded) renderSkeleton();" in src


def test_queue_dragdrop_creates_article() -> None:
    """拖放建稿（正典 §6.2 保留）：dragover/drop → FileReader → POST /api/article/save。

    P1：拖放目标随视图改名挂 view-publish；完成后刷新发布中心。
    """
    src = _js()
    # 四个拖放事件都挂在发布中心视图上；dragenter/dragleave 计数防子元素闪烁
    for evt in ("dragenter", "dragover", "dragleave", "drop"):
        assert f"addEventListener('{evt}'" in src, evt
    assert "var queueView=$('view-publish');" in src
    assert "var dragDepth=0;" in src
    assert "$('drop-overlay').hidden=!on;" in src  # 拖入显示虚线朱砂覆盖层
    # FileReader 读取文本（正典 §6.2 指定 API）
    assert "var r=new FileReader();" in src
    assert "r.readAsText(file);" in src
    # 仅接受 .md/.markdown；非 md 拒收提示
    assert "/\\.(md|markdown)$/i.test(f.name)" in src
    assert "仅支持拖入 .md 文件" in src
    # POST /api/article/save {article, content, confirm:true}（模板字面量）
    assert "postJSON(`/api/article/save`,{article:f.name,content:content,confirm:true})" in src
    # 完成后强制刷新文章并重绘发布中心（A 类专项 2026-11-20：core 经 VAPI.repaint
    # 触达视图渲染函数，不再直呼 renderPublish）
    assert "await loadArticles(true);\nviewRepaint('publish');" in src


def test_queue_empty_state_with_action() -> None:
    """Phase 2 空状态（正典 §6.4）：SVG 插画 + 一行说明 + 一个行动引导，双模式动作。"""
    parsed = _structure()
    assert {"queue-empty", "queue-empty-title", "queue-empty-text", "queue-empty-action"} <= set(parsed.ids)
    src = _shell_js()
    # 视觉锚点：docs monoline 插画（非 emoji）
    assert 'data-icon="docs"' in src
    # 空根目录 → 引导刷新；筛选落空 → 引导查看全部（data-empty-mode 分派）
    assert "act.setAttribute('data-empty-mode',rootEmpty?'refresh':'reset');" in src
    assert "rootEmpty?'刷新队列':'查看全部'" in src
    assert "$('queue-empty-action').addEventListener('click'" in src
    assert "if(this.getAttribute('data-empty-mode')==='reset') setFilter('all');" in src
    # 一行说明：两态文案都在源码内
    assert "拖入 .md 文件自动创建" in src
    assert "当前筛选条件下没有文章" in src


# ── Phase 3 详情视图（编辑默认 Tab + 变体画廊 + 检查分组）──


def test_detail_view_structure() -> None:
    """Phase 3 Tab 栏：编辑/变体/检查/预览/对照/版本历史/操作，面板 id 与 data-panel 对齐。"""
    parsed = _structure()
    assert {
        "back-link",
        "detail-title",
        "detail-meta",
        "detail-tabs",
        "preview-frame",
        "preview-stage",
        "check-list",
        "check-summary",
        "review-stage",
        "review-empty",
        "adapt-buttons",
        "pack-platform",
        "pack-variant",
        "pack-btn",
        "publish-btn",
    } <= set(parsed.ids)
    tabs = {"edit", "variants", "checks", "preview", "review", "versions", "actions"}
    assert tabs <= parsed.attrs["data-tab"]
    assert tabs <= parsed.attrs["data-panel"]
    src = _shell_js_css()
    # 编辑为默认 Tab：路由缺省 tab=edit、卡片入口带 tab=edit
    assert "route.params.get('tab')||'edit'" in src
    assert "'&tab=edit'" in src
    assert "['edit','variants','checks','preview','review','versions','actions'].indexOf(tab)>=0?tab:'edit'" in src
    assert "677px" in src
    assert "无配对笔记" in src
    for label in ("编辑", "变体", "检查", "预览", "对照", "版本历史", "操作"):
        assert label in src, label
    # setTab 挂载各面板（edit/variants 为 Phase 3 新增挂载点）
    for token in ("if(tab==='edit') mountEdit();", "if(tab==='variants') renderVariants();"):
        assert token in src, token


def test_detail_split_editor_structure() -> None:
    """编辑 Tab 分屏（正典 §4.3）：左编辑器（等宽+行号+overlay）右 677px 实时预览。"""
    parsed = _structure()
    assert {"edit-gutter", "edit-hl", "edit-area", "ed-stack", "live-frame", "ed-chars", "ed-saved", "ed-ai"} <= set(parsed.ids)
    src = _shell_js_css()
    # overlay 与 textarea 度量逐项一致（font/padding/white-space），否则着色错位
    area = re.search(r"#edit-area\{[^}]*\}", src).group(0)
    hl = re.search(r"#edit-hl\{[^}]*\}", src).group(0)
    for metric in ("font:12px/20px var(--font-mono)", "padding:12px 14px", "white-space:pre"):
        assert metric in area, metric
        assert metric in hl, metric
    assert 'wrap="off"' in src  # 禁软换行（overlay 对齐前提）
    # 行号列独立渲染并在滚动时同步
    assert "EG.scrollTop=EA.scrollTop" in src
    # 预览台：灰底居中 + 677px 手机宽度 iframe（遗留清偿：改 max-width+flex:1+
    # min-width:0——预览台被压窄时 iframe 跟随收缩，不再溢出）
    assert ".pv-stage{" in src and "#live-frame{max-width:677px;flex:1;min-width:0" in src
    # 分屏三列布局：编辑列/预览台/批注侧栏（.edit-layout 直挂）
    assert ".edit-layout{display:flex" in src and ".ed-col{" in src
    # 审查修复：编辑列保底 360px（textarea≈310px 可编辑）不被压穿；预览列自 697 可收缩
    assert ".ed-col{flex:1 0 360px;" in src
    assert ".pv-stage{flex:0 1 697px;" in src


def test_detail_syntax_highlight_overlay() -> None:
    """语法高亮（任务 §3）：textarea 透明 + overlay 正则着色，不引 CodeMirror。"""
    src = _js_css()
    assert "function hlSeg(t){" in src and "function renderHl(sel){" in src
    # 关键字着色：标题整行 / 围栏整段 / 引用行 / 行内码 / 粗体 / 链接
    for token in ("tk-h", "tk-c", "tk-q", "tk-b", "tk-l"):
        assert f"'{token}'" in src or f'"{token}"' in src or f"class=\\\"{token}\"" in src or f'<span class="{token}">' in src, token
    # CSS token 色与 overlay 指针穿透
    for rule in (".tk-h,", ".tk-c,", ".tk-b", "pointer-events:none"):
        assert rule in src, rule
    # overlay 不抢输入事件；textarea 透明文字+可见光标
    assert "color:transparent;caret-color:var(--ink-1)" in src
    # 选中区间包 .tk-mark（气泡定位锚点 + 批注期间的高亮保持）
    assert "tk-mark" in src


def test_detail_live_preview_renderer() -> None:
    """实时预览（任务 §1）：300ms 防抖 → 迷你 md 渲染 → iframe srcdoc（零网络）。

    审查修复：相对路径图片重写 /asset?path=（防裂图）；URL 协议白名单 + iframe
    sandbox（防 javascript: 链接在同源 srcdoc 内执行）。
    """
    src = _shell_js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "function mdToHtml(md,dir){" in src and "function mdInl(s,dir){" in src
    assert "var LIVE_CSS=" in src  # 预览排版自包含（srcdoc 内无外部样式可达）
    assert "LF.srcdoc=" in src
    assert "liveTimer=setTimeout(renderLive,300)" in src  # 防抖
    # 块级支持：围栏/标题/引用/列表/段落
    for token in ("<pre><code>", "</blockquote>", "<ul>", "<p>"):
        assert token in src, token
    # 准确渲染走预览 Tab 的服务端 iframe（实时预览为主，主题级为准）
    assert "htmlPath(state.detailPath)" in src
    # 相对图片重写：文章目录前缀 + /asset 端点（与队列封面同一数据面）
    assert "function dirOf(p){" in src
    assert "function safeSrc(u,dir,img){" in src
    assert "return img?'/asset?path='+encodeURIComponent(dir+u):'#';" in src
    assert "mdToHtml(EA.value,dirOf(state.detailPath));}" in src
    # 协议白名单（遗留清偿收紧）：链接仅放行 http(s)/#——javascript:/data:/mailto:
    # 一律废弃为 #；图片另放行 data:（base64 内联），相对路径重写 /asset
    assert "if(img?/^(https?:|data:)/i.test(u):/^(https?:|#)/i.test(u))return u;" in src
    # iframe sandbox：保留同源（/asset 图片可载、父页可读文档做同步滚动），脚本禁用
    assert 'id="live-frame" title="实时预览" sandbox="allow-same-origin"' in src
    assert "allow-scripts" not in all_src
    # 输入事件联动五件套：dirty 标记/行号/高亮/预览/状态栏（建议3：编辑即 dirty）
    assert "EA.addEventListener('input',function(){state.saveSt='dirty';syncGutter();renderHl();scheduleLive();updateStatusbar();hideBubble();});" in src


def test_detail_bidirectional_scroll_sync() -> None:
    """双侧同步滚动（建议2/沉淀·模式 6，doocs useScrollSync 移植）：块级锚映射替代像素比例。

    编辑侧 mdBlocks 与 mdToHtml 同一分割规则（围栏/标题/引用/列表/段落，锚行归入
    后块）→ 块数天然对齐；预览侧收集块级 HTMLElement（PVS 选择器）；scrollTop→
    块索引+块内比例；双层防回环——sLock 一帧标志 + sEcho 程序赋值目标 <2px 回声
    吞掉（单向一次性判定，diff>2 时清 pending 不误吞后续真实滚动）。
    """
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "function syncPreview(){" in src
    # 块分割函数与渲染器同规则：围栏/标题/引用/列表/段落五分支 + 锚行前置归并
    assert "function mdBlocks(md){" in src
    assert "BANCHOR.test(L)" in src  # 锚行（<!--b:id-->）计入后块起点，不产元素
    for seg in ("/^```/.test(L)", "/^(#{1,4})\\s+/.test(L)", "/^>\\s?/.test(L)",
                "/^\\s*(?:[-*]|\\d+\\.)\\s+/.test(L)"):
        assert seg in src, seg
    # 段落累积与 mdToHtml fl() 同语义：特殊块到达先闭段（cp(i)）
    assert "function cp(end){if(ps>=0)o.push([ps,end]);ps=-1;}" in src
    # 预览侧块元素选择器（与 mdToHtml 输出的元素集一致）
    assert "PVS='p,h1,h2,h3,h4,pre,blockquote,ul'" in src
    # 编辑→预览：行高常量（overlay 12px/20px 度量）+ 块索引 + 块内比例 + 钳位
    assert "var EDLH=20,EDPT=12" in src
    assert "lf=Math.max(0,(EA.scrollTop-EDPT)/EDLH)" in src
    assert "els[Math.min(bi,els.length-1)]" in src  # 块数瞬时失配（防抖间隙）钳末块
    # 预览→编辑：反向块映射回写行坐标
    assert "EA.scrollTop=t;sEcho.e=t;" in src
    assert "bs[Math.min(bi,bs.length-1)]" in src
    # 双层防回环：sLock + <2px 回声吞掉（两侧各一次、一次性判定）
    assert "if(sLock)return;" in src
    assert "Math.abs(b.scrollTop-sEcho.p)<2" in src
    assert "Math.abs(EA.scrollTop-sEcho.e)<2" in src
    # 像素比例公式已退役（块级映射替代）。域=material 视图 + core（该公式的
    # 退役现场）；editor.js 是另一子系统（CodeMirror↔iframe 滚动同步），其
    # 自己的 scrollHeight 比例公式是正当实现，不属本条「material 预览退役」
    # 语义——fix-6 扩 all_src 域后不能拿别处的正当用法误伤本条（各锁各域）。
    _mv = _mat_js() if VIEWS_JS.exists() else ""  # UIX-3：material 三拆并集域
    assert "scrollHeight-b.clientHeight" not in (src + _mv)
    # 编辑器滚动同时同步行号列与 overlay（既有行为保留）
    assert "EG.scrollTop=EA.scrollTop;HL.scrollTop=EA.scrollTop;HL.scrollLeft=EA.scrollLeft" in src


def test_detail_statusbar_and_ai_profile() -> None:
    """底部状态栏（任务 §4 + 建议3/深审 H2）：字数/保存四态徽章/当前 AI 档案（/api/health 复用）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "function updateStatusbar(){" in src
    assert "$('ed-chars').textContent=EA.value.replace(/\\s+/g,'').length" in src
    # AI 档案：编辑装载时惰性拉取 /api/health（既有端点，单引号字面量已在契约集）
    assert "getJSON('/api/health').then(function(h){state.aiModel=(h&&h.ai&&h.ai.model)||''" in src
    # 保存成功后记录 HH:MM（saved 徽章消费：已保存 HH:MM）
    assert "state.savedAt=('0'+t.getHours()).slice(-2)+':'+('0'+t.getMinutes()).slice(-2)" in src
    # 二进制「保存于/未保存」文案退役，零散文案由状态表渲染替代
    assert "保存于 '" not in all_src


def test_save_status_four_state_meta_object() -> None:
    """建议3（沉淀·模式 17，深审 H2 清偿）：保存状态四态元数据对象 ST4 + setSaveSt。

    四态 × 纸墨色义映射全部复用既有 badge 类（done=青竹/warn=琥珀/queued=墨灰/
    fail=暗朱，零新增 CSS）；状态栏消费元数据而非零散 if-else；生命周期——
    装载即 saved（与磁盘一致）→ input 即 dirty → 保存中 saving → 成功 saved（带
    HH:MM）/失败 failed（title 就地可读失败原因，dirty 态 title 提示 ⌘S）。
    """
    src = _js()
    assert "var ST4={saved:{c:'done',t:'已保存'},saving:{c:'warn',t:'保存中…'},dirty:{c:'queued',t:'有未保存修改'},failed:{c:'fail',t:'保存失败'}};" in src
    assert "function setSaveSt(st,why){" in src
    assert "el.className='badge '+m.c;" in src  # 消费元数据渲染徽章
    assert "el.title=st==='failed'?state.saveErr:(st==='dirty'?'⌘S / Ctrl+S 保存':'');" in src
    # 生命周期四转移
    assert "state.saveSt='dirty';syncGutter();" in src  # 编辑即 dirty
    assert "ES.disabled=true;setSaveSt('saving');" in src
    assert "setSaveSt('saved');" in src
    assert "setSaveSt('failed',err.message);" in src
    # 装载重置：正文取到后回 saved（savedAt/saveErr 清零）
    assert "state.savedAt='';state.saveSt='saved';state.saveErr='';}" in src
    # updateStatusbar 经状态表渲染（单一出口）
    assert "setSaveSt(state.saveSt);" in src


def test_detail_cmd_s_save_binding() -> None:
    """⌘S/Ctrl+S 保存（任务 §5）：编辑 Tab 上下文拦截默认行为并保存；Esc 收气泡/查看器。"""
    src = _js()
    assert "(e.metaKey||e.ctrlKey)&&(e.key==='s'||e.key==='S')" in src
    assert "state.tab==='edit'){e.preventDefault();handleSaveBody();}" in src
    assert "e.key==='Escape'" in src
    # ARCH-1（2026-11-21）：AB 惰性抓取（article HTML 懒注入）——null 守卫形态
    assert "if(AB&&!AB.hidden)hideBubble();" in src
    # UIX-3（2026-11-23）：Esc 关查看器走 closeDlg（焦点还给触发者）
    assert "if(!$('viewer').hidden)closeDlg('viewer');" in src


def test_selection_annotation_bubble() -> None:
    """划词批注（任务 §6/正典 §5.3）：选区→气泡→行号→POST /api/annotations/add。"""
    parsed = _structure()
    assert {"annot-bubble", "annot-input", "annot-ok", "annot-cancel", "ab-lines"} <= set(parsed.ids)
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 选区检测：selectionStart/End → 起止行号；空选区/纯空白不弹
    assert "function maybeBubble(){" in src
    assert "EA.selectionStart" in src and "EA.selectionEnd" in src
    assert "EA.value.slice(0,s).split('\\n').length" in src
    # 气泡定位在选区上方（.tk-mark 的几何位置）；首行放不下则移到选区下方
    assert "HL.querySelector('.tk-mark')" in src
    assert "AB.style.top=Math.max(0,Math.min(r.top-host.top-AB.offsetHeight-8>=0?r.top-host.top-AB.offsetHeight-8:r.bottom-host.top+8,host.height-AB.offsetHeight))+'px'" in src  # 首行放不下→下方；整体钳回面板内
    # 提交：行号区间 + comment 走既有 /api/annotations/add（模板字面量）
    assert "annotPost(`/api/annotations/add`,{line_start:sel.ls,line_end:sel.le,comment:c},'添加批注失败')" in src
    # 空批注拒绝
    assert "批注内容不能为空" in src
    # 行号点击 + window.prompt 老路径已退役（划词替代）
    assert "window.prompt" not in all_src
    assert "EG.addEventListener('click'" not in all_src
    # mouseup/键盘选区都会探测气泡；Enter 提交 / Esc 取消；键盘路径 300ms 防抖
    # （遗留清偿：连按方向键/shift 选区不逐键闪弹气泡）
    assert "EA.addEventListener('mouseup',maybeBubble)" in src
    assert "bubbleTimer=setTimeout(function(){bubbleTimer=null;maybeBubble();},300);" in src
    assert "$('annot-input').addEventListener('keydown'" in src
    # 审查修复（实测判例）：input focus 会带动祖先滚动 → scroll 监听自噬气泡。
    # 双保险：focus({preventScroll:true}) + 打开后 300ms 宽限期内 scroll 不关气泡
    assert "$('annot-input').focus({preventScroll:true});" in src
    assert "state.bubAt=Date.now();AB.hidden=false;" in src
    assert "if(!state.bubAt||Date.now()-state.bubAt>300)hideBubble();" in src


def test_annotation_sidebar_actions() -> None:
    """批注侧栏（任务 §7/§8）：时间/内容/删除 + 每条「按此修改」→ annotate_rewrite job。"""
    parsed = _structure()
    assert {"annot-side", "annot-title", "annot-list", "edit-annotate-ai", "edit-annotate-toggle"} <= set(parsed.ids)
    src = _js_css()
    # 行渲染：行范围 + created 时间（T→空格截 16 位）+ 按批注修改（全文）+ 删除
    assert "String(x.created||'').slice(0,16).replace('T',' ')" in src
    assert 'data-annot-ai="1">按批注修改（全文）' in src
    assert "data-annot-del=" in src
    # 「按批注修改（全文）」与顶部全局按钮都走 handleAnnotateAI → /api/action/annotate_rewrite
    assert "if(event.target.closest('[data-annot-ai]')) handleAnnotateAI();" in src
    assert "postJSON(`/api/action/annotate_rewrite`,{article:p,confirm:true})" in src
    # AI 修改完成：toast 带「查看对照」操作按钮（遗留清偿：替代 Phase 3 的自动
    # 弹出），点击才 fetch .annotated.md 正文并开对照模态（正典 §八「批注→AI
    # 修改的对照预览」）——行级差异红删绿增、等值段折叠
    assert "var j=await waitForJob(d.job_id);" in src
    assert "{label:'查看对照',fn:function(){" in src
    assert "function diffHtml(a,b){" in src
    assert "'<span class=\"dl-'+(cur[0]>0?'add':'del')+'\">'" in src
    assert "行相同" in src
    assert ".dl-del{color:var(--crimson)}.dl-add{color:var(--jade)}" in src
    assert "$('viewer-body').innerHTML=diffHtml(EA.value,b.content||'');" in src
    # 删除走既有 /api/annotations/delete
    assert "handleAnnotateDel" in src
    # 侧栏收纳开关（批注栏：开/关，让位分屏）
    assert "function toggleAnnotate(){" in src
    assert "'批注栏：'+(side.hidden?'关':'开')" in src


def test_variants_gallery() -> None:
    """变体 Tab（任务 §9-12/正典 §4.3 一稿多态画廊）：卡片四要素 + 三操作 + 一键生成。"""
    parsed = _structure()
    assert {"variant-grid", "variants-batch-btn", "viewer", "viewer-body", "viewer-title", "viewer-close"} <= set(parsed.ids)
    src = _shell_js_css()
    # 逐变体取数（复用 /api/article/body 模板字面量，零新增端点）→ 标题/字数/本地校验
    assert "function renderVariants(){" in src
    assert "variantLocal(b.content||'')" in src
    for msg in ("无 # 一级标题", "正文过短", "本地校验通过"):
        assert msg in src, msg
    # 平台字标 + 标题 + 字数 + 校验徽章（monoline 图标，非 emoji）
    assert "platform-code" in src and "vcard-title" in src and "' 字'" in src
    assert "(v.ok?I('check'):I('warning'))" in src
    # 卡片三操作：预览全文 / 重新生成（复用 handleAdapt）/ 生成物料包（复用 handlePack）
    for attr in ("data-vview", "data-vadapt", "data-vpack"):
        assert attr in src, attr
    assert "if(view){ openViewer(view.getAttribute('data-vview'));return;}" in src
    assert "handleAdapt(adapt.getAttribute('data-vadapt'),adapt)" in src
    assert "handlePack(pack.getAttribute('data-vpack'),pack.getAttribute('data-vrel')||null,pack)" in src
    # handlePack 签名泛化：操作 Tab 下拉与变体卡片共用（askConfirm 计数不增）
    assert "async function handlePack(platform,variant,button){" in src
    assert "handlePack($('pack-platform').value,$('pack-variant').value||null,$('pack-btn'))" in src
    # 底部四平台一键生成（batch_adapt）双入口：变体 Tab + 操作 Tab
    assert "$('variants-batch-btn').addEventListener('click'" in src
    assert "'/api/action/batch_adapt', {article: path, platforms: ['xhs','channels','douyin','zhihu'], confirm: true}" in src
    # 变体清单来自队列 flags.variants（variantFile 推导 <stem>.<平台>.md）
    assert "item.flags.variants" in src or "flags.variants" in src
    # 全文查看器复用 modal 遮罩/面板类（零新增弹层体系）
    assert 'class="modal-overlay" id="viewer"' in src
    assert "$('viewer-body').textContent=v||'（空文件）'" in src
    # 审查修复：变体缓存双失效路径——任务完成回调（refreshArticleState）与全局刷新
    assert src.count("state.vraw={};") == 2
    assert "state.nonce += 1;\nstate.vraw={};" in src


def test_checks_tab_grouped_with_fix_commands() -> None:
    """检查 Tab（任务 §13/§14）：格式/渲染/事实自检三组；FAIL 默认展开；WARN/FAIL 附 CLI 修复命令。"""
    src = _js()
    for label in ("格式检查", "渲染检查", "事实自检"):
        assert label in src, label
    # 分组判定：渲染类关键词路由到渲染组，其余进格式组
    assert "var RENDER_RE=/渲染|外链|外域|协议|宽高比|禁止标签|mermaid|LaTeX|图片来源/" in src
    # <details> 分组 + 含 FAIL 的组默认展开
    assert "'<details class=\"action-group\"'" in src
    assert "?' open':''" in src
    # CLI 修复命令映射：摘要→digest / 主题→theme list / 其余→check --render；事实自检组给 ai check-facts
    assert "function fixCmd(m){" in src
    for cmd in (
        "musespiral digest <文章>",
        "musespiral theme list",
        "musespiral check --render <文章>",
        "musespiral ai check-facts <文章> --note ",
    ):
        assert cmd in src, cmd
    # WARN/FAIL 行附命令（PASS 不附）
    assert "(l==='PASS'?'':'<code class=\"fix-cmd\">$ '+fixCmd(m)+'</code>')" in src
    # 事实自检是 CLI 域（不在 check 门内，只标记不否决）——给出说明而非伪结果
    assert "AI 事实自检不在 check 门内（只标记不否决）" in src
    # 行结构与 e2e 锚点保留：#check-list 内 .check-row（级别类运行时拼接）
    assert "'<div class=\"check-row '+lo+'\">" in src


# ── Phase 4 仪表盘（数据故事）/ 任务面板 / 命令面板 / 快捷键 ──


def test_dashboard_view_structure() -> None:
    """Phase 4 仪表盘（§4.4）：瓷砖 + 趋势柱图 + 平台条形 + 健康卡片容器 id 齐备。

    旧「按平台发布记录/平台能力矩阵」双表格退役（数据故事替代表格堆砌）；
    阅读/分享总量并入「总阅读」瓷砖（stats.total_reads 聚合口径）。
    """
    parsed = _structure()
    assert {"stat-tiles", "trend-chart", "platform-bars", "health-card", "health-grid"} <= set(parsed.ids)
    src = _shell_js_css()
    for label in ("已发布", "待审", "总阅读", "平台数", "发布趋势（近 30 天）", "按平台分布", "环境健康"):
        assert label in src, label
    assert "by_platform" in src  # 趋势/条形数据面
    assert "recent" in src
    # 双列布局：左（趋势+条形）右（健康）；窄屏单列（响应式收编 900px 断点）
    assert ".dash-grid{display:grid" in src
    assert ".dash-grid{grid-template-columns:1fr}" in src
    # 旧表格结构已退役
    for gone in ("stats-table", "stats-body", "capability-table", "capability-body", "readShareCell"):
        assert gone not in src, gone


def test_dashboard_stat_tiles_data_sources() -> None:
    """统计瓷砖四项的数据源：已发布=total_publish / 待审=队列同口径 / 总阅读=total_reads / 平台数=platforms。

    总阅读取 stats.total_reads 全量聚合（F17 回填口径），替代旧表格「阅读/分享」列
    的 recent 前 N 求和；待审与队列筛选 pending 档逐条件一致。
    评审补批 P1-4：瓷砖组并入零发布分支——非零态四砖数据面不变（断言走
    test_review_batch_p14_insight_zero_state_guide），此处锁数据源字面量。
    """
    src = _js()
    assert "var st=state.stats||{},pub=st.total_publish||0;" in src
    assert "[['已发布',pub],['待审',pendingCount()],['总阅读',st.total_reads||0],['平台数',state.platforms.length]]" in src
    # 仪表盘路由补拉文章列表（待审瓷砖数据面），复用既有 '/api/articles' 字面量
    # FEAT-P1B：写作节奏卡数据面随路由并行拉取（loadWriting）
    assert "await Promise.all([loadStats(),loadPlatforms(),loadArticles(),loadMaterials(),loadCompose(true),refreshJobs(),renderHealth(),loadRadar(),loadWriting(true)]);" in src
    # 待审口径与队列 pending 档逐条件一致（无 FAIL/WARN 且 draft 且未发布）
    assert "function pendingCount(){" in src
    assert "return (c.fail||0)===0&&(c.warn||0)===0&&articleStatus(a)==='draft'&&!f.published;}).length;" in src


def test_dashboard_trend_chart_pure_css_bars() -> None:
    """发布趋势（近 30 天）：纯 CSS 柱状图——div height 百分比 + 逐日 title 明细，无图表库。"""
    src = _js_css()
    assert "function renderTrend(){" in src
    # 逐日桶（本地日期键）；P1 起窗口随洞察 range 30/90 自适应 + 标题联动
    assert "var R=state.irange===90?89:29;" in src
    assert "for(i=R;i>=0;i--)days.push({k:dkey(new Date(d.getTime()-i*864e5))" in src
    assert "function dkey(dt){" in src
    # 事件源：by_platform.recent 去重合并（键=ts|平台|动作|标题），无新增端点
    assert "r.ts+'|'+c+'|'+r.action+'|'+(r.title||r.article||'')" in src
    # 柱体：<i style="height:N%">，有记录日着色 .on，零记录日 4% 基线
    assert "' style=\"height:'+(x.n?Math.max(8,Math.round(x.n/max*100)):4)+'%\"" in src
    assert "'<i'+(x.n?' class=\"on\"':'')" in src
    assert ".trend i.on{background:var(--vermilion)}" in src
    assert ".trend{display:flex;align-items:flex-end;gap:2px;height:110px}" in src
    # 逐日 title：条数 + 发表/草稿分解
    assert "条（发表 '" in src and " · 草稿 '" in src


def test_dashboard_platform_bars_horizontal() -> None:
    """按平台分布：水平条形图——div width 百分比（发表数/最大值），附草稿数。"""
    src = _js_css()
    assert "function renderPlatformBars(){" in src
    assert "style=\"width:'+Math.round(r.pub/max*100)+'%\"" in src
    assert ".pbar{display:grid;grid-template-columns:76px 1fr auto;" in src
    assert ".pbar-fill{display:block;height:100%;border-radius:5px;background:var(--vermilion)}" in src
    # 平台名三源回退：registry 名 → PN/PNN 中文名 → code
    assert "p.name||PN[p.code]||PNN[p.code]||p.code" in src
    assert "var PNN={mp:'公众号',kuaishou:'快手'};" in src
    assert "pub:g.publishes||0,dft:g.drafts||0" in src


def test_dashboard_health_cards_grid() -> None:
    """环境健康卡片化（§4.4）：每项独立卡片（ok/warn 左边条着色），未就绪附 CLI 修复命令。"""
    src = _js_css()
    assert "function hcard(ok,m,l,d,f){" in src
    assert "'<div class=\"hcard '+(ok?'ok':'warn')+'\" data-health=\"'+m+'\">'" in src
    assert ".hcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr))" in src
    assert ".hcard.ok{border-left-color:var(--jade)}" in src
    assert ".hcard.warn{border-left-color:var(--amber)" in src
    # 未就绪才附修复命令（就绪或无命令项不附）
    assert "(ok||!f?'':'<code class=\"fix-cmd\">$ '+f+'</code>')" in src


# ── 基础组件类（Phase 1：CSS 类而非 Web Components）────────


def test_base_component_classes_defined() -> None:
    """btn 四变体 / badge 三色胶囊 / card / modal / toast / input / tabs / chips。"""
    src = _css()
    # 按钮：primary/secondary/ghost/danger 四变体 + disabled 态
    assert ".btn{" in src
    for variant in (".btn-primary{", ".btn-secondary{", ".btn-ghost{", ".btn-danger{"):
        assert variant in src, variant
    assert ".btn:disabled{" in src
    # 徽章：success/warning/error 三色胶囊 + 既有语义别名
    for cls in (".badge.pass,.badge.success{", ".badge.warn,.badge.warning{", ".badge.fail,.badge.error{"):
        assert cls in src, cls
    # 卡片：hover 上浮（material 微交互，正典 §4.2）
    assert ".card:hover,.article-card:hover{transform:translateY(-2px)" in src
    # 模态：半透明遮罩 + 圆角 12 内容框
    assert ".modal-overlay{position:fixed" in src
    assert ".modal{width:min(440px,100%)" in src
    assert "var(--radius-modal)" in src
    assert "rgba(0,0,0,0.4)" in src
    # Toast：右下角 + 滑入动画 + 三色左边框
    assert "toast-in" in src and "@keyframes toast-in{" in src
    for cls in (".toast.success{", ".toast.warning{", ".toast.error{"):
        assert cls in src, cls
    assert "border-left-color" in src
    # 输入框：聚焦 accent 边框
    assert ".input,select{" in src
    assert ".input:focus,select:focus{outline:none;border-color:var(--vermilion)" in src
    # Tabs：底部 accent 指示条
    assert ".tabs{" in src and ".tab.is-active{" in src
    assert "border-bottom-color:var(--vermilion)" in src
    # Chips：选中态 accent 底白字
    assert ".chips{" in src
    assert ".chip.is-active{background:var(--vermilion)" in src


# ── fetch 契约（§15.2）────────────────────────────────────


def test_fetch_paths_match_api_contract() -> None:
    src = _js()
    found: set[str] = set()
    for literal in re.findall(r"'(/api/[^']+)'", src):
        found.add(literal.split("?", 1)[0])
    assert found == CONTRACT_PATHS, f"fetch 路径与契约不一致: {sorted(found)}"
    assert "fetch(path, options)" in src
    assert "request('GET'" in src
    assert "request('POST'" in src


def test_detail_and_iframe_endpoints_are_wired() -> None:
    src = _js()
    assert "'/api/articles'" in src
    assert "'/api/article?path='" in src
    assert "'/api/article/html?path='" in src
    assert "'/api/article/review?path='" in src
    assert "'&note='" in src
    assert "'/api/platforms'" in src
    assert "'/api/stats'" in src


def test_action_request_bodies_follow_contract() -> None:
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "'/api/action/adapt', {article: path, platform: platform, confirm: true}" in src
    assert "'/api/action/pack', {article: path, platform: platform, variant: variant, confirm: true}" in src
    assert "'/api/action/publish', {article: path, confirm: true}" in src
    assert (
        "'/api/action/batch_adapt', {article: path, platforms: ['xhs','channels','douyin','zhihu'], confirm: true}"
        in src
    )
    # 只允许 adapt/pack/publish/batch_adapt 四个动作端点，不存在 submit/发表接口
    assert "/api/action/submit" not in all_src
    assert "/api/submit" not in all_src


# ── 自定义 confirm 弹层与 toast ───────────────────────────


def test_custom_confirm_modal_and_toast() -> None:
    parsed = _structure()
    assert {"modal", "modal-title", "modal-message", "modal-confirm", "modal-cancel"} <= set(parsed.ids)
    assert {"toast", "toast-text"} <= set(parsed.ids)
    src = _shell_js_css()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "modal-overlay" in src
    assert ".toast" in src and ".toast.success" in src and ".toast.error" in src
    # 自定义弹层而非浏览器原生 confirm/prompt；取消按钮接线 btn-secondary
    # （遗留清偿：Phase 1 四变体中此前唯一未被消费的次级变体）
    assert '<button class="btn btn-secondary" id="modal-cancel">' in src
    assert "window.confirm" not in all_src
    assert "window.prompt" not in all_src
    assert re.search(r"\bconfirm\s*\(", src) is None
    # 四个动作（adapt/pack/publish/batch_adapt）+ F46 KB 设置保存 + F48 创作台
    # 「采纳入稿」+ P20 M3 删除素材两步 confirm（连坐声明/终确认各一）+ P2 源
    # 两步删除（终步=禁用下线，契约无删除端点）+ F-C 素材撤池（2026-11-18）都先走确认弹层
    assert src.count("askConfirm({") == 11
    for action in ("handleAdapt", "handlePack", "handlePublish", "handleBatch"):
        assert action in src


def test_toast_spec_follows_design() -> None:
    """Toast 正典 §5.6 规格：成功 3s/错误与警告 5s 自动消失 / hover 暂停计时 /
    warning 类型可达 / 可选操作按钮（带操作的 toast 8s，如批注「查看对照」）。"""
    src = _js()
    # ① 分型计时：成功 3s / 错误与警告 5s / 带操作按钮 8s（留点击时间）
    assert "toastLeft=action?8000:(kind==='error'||kind==='warning')?5000:3000;" in src
    assert "el.addEventListener('mouseenter'" in src  # ② hover 暂停计时
    assert "el.addEventListener('mouseleave'" in src  # 离开后按剩余时间续走
    # ③ 三类型可达：error/warning 透传，缺省 success（.toast.warning CSS 不再是死样式）
    assert "el.className='toast '+(kind==='error'||kind==='warning'?kind:'success')" in src
    # ④ 可选操作按钮：toast(…,{label,fn}) 挂 #toast-action；点击先收 toast 再执行
    assert "ab.addEventListener('click',function(){var f=toastAct;toastHide();if(f)f();});" in src
    # ⑤ warning 类型生产可达（审查判例：className 组装表达式存在 ≠ 可达——
    # 须有真实调用点，否则 .toast.warning 是死样式）：标记已审时若该文仍有
    # WARN 级检查项 → WN='warning' toast 提示未处理项数；无 WARN 走 success
    assert "WN='warning'" in src
    assert (
        "toast(w?'已标记为已审（reviewed）；仍有 '+w+' 项 WARN 未处理'"
        ":'已标记为已审（reviewed）',w?WN:OK);" in src
    )


# ── 视觉规格（纸墨视觉语言，正典 §三）─────────────────────


def test_visual_spec_matches_design() -> None:
    src = _css()
    # 字体栈：正文系统栈 + 等宽栈
    assert '--font-body:-apple-system,"SF Pro Text","PingFang SC"' in src
    assert '--font-mono:"SF Mono","JetBrains Mono"' in src
    # 纸墨工坊色板（token v2 亮值，§1.2 色彩语义表）
    for color in ("#faf9f7", "#1a1a1a", "#c0392b", "#2c5f8a", "#e8e6e3", "#6b6b6b"):
        assert color in src.lower()
    # 语义槽亮值（jade/amber 沿用档A WCAG 暗化值；crimson=危险专色）
    for color in ("#1a7038", "#9c5700", "#8e2434"):
        assert color in src.lower()
    # 圆角体系
    assert "--radius-card:8px" in src
    assert "--radius-btn:6px" in src
    assert "--radius-modal:12px" in src
    # 容器宽度与响应式
    assert "max-width:1200px" in src
    assert re.search(r"@media\s*\(max-width:\s*900px\)", src) is not None
    # 状态徽章药丸样式（浅底 + pill 圆角 token）
    assert ".badge.fail" in src and ".badge.warn" in src and ".badge.pass" in src
    assert "--radius-pill:999px" in src


# ── hash 路由 ─────────────────────────────────────────────


def test_hash_routing_is_complete() -> None:
    src = _js()
    # P1 六视图 hash 白名单（§2.2）+ 旧名别名兼容（HALIAS）
    for h in ("#sources", "#material", "#compose", "#publish", "#insight", "#settings", "#article?path="):
        assert h in src, h
    assert "var HALIAS={queue:'publish',dashboard:'insight',discover:'material','plugin-pipeline':'plugin-run'};" in src
    assert "hashchange" in src
    assert "location.hash" in src
    assert "URLSearchParams" in src


def test_refresh_reloads_iframes_with_nonce() -> None:
    """刷新/动作后 iframe 必须换 URL 重载：nonce 追加 &v=，避免同 src 不刷新。"""
    src = _js()
    assert "nonce: 0" in src
    assert src.count("state.nonce += 1") == 2
    assert "'&v=' + state.nonce" in src


# ── §16 F19 前端收尾：root 底栏 meta 化 + 阅读/分享列 ──────


def test_root_chip_fetches_meta_with_fallback() -> None:
    """root 显示 fetch /api/meta 的真实 root（底栏 statusbar）；失败降级默认文案。"""
    src = _shell_js()
    assert "'/api/meta'" in src
    assert "loadMeta" in src and "loadMeta().then(renderRoot)" in src
    assert "state.meta" in src  # meta 到达后优先显示真实 root
    assert "默认（服务 --root）" in src  # 降级文案保留


def test_task_panel_upgraded_rows() -> None:
    """Phase 4 任务面板（§5.4）：状态图标 SVG + 描述 + 进度条（running）+ 相对时间 + 查看/重试。"""
    parsed = _structure()
    assert {"jobs-wrap", "jobs-btn", "jobs-badge", "jobs-menu"} <= set(parsed.ids)
    src = _js_css()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 状态图标映射（复用 25 图标集，零新增 SVG）：排队/运行/完成/失败
    assert "var ic={queued:'clock',running:'refresh',done:'check',failed:'error'}[j.status]||'clock'" in src
    assert "var JSTAT={queued:'排队',running:'运行中',done:'完成',failed:'失败'};" in src
    # 进度条：phase "i/n" → 百分比宽度；无 phase 为不确定动画（indet）
    assert "function jobPct(j){" in src
    assert "var m=String(j.phase||'').match(/^(\\d+)\\/(\\d+)$/)" in src
    assert "'<div class=\"job-prog'+(pct==null?' indet':'')+'\"><i'+(pct==null?'':' style=\"width:'+pct+'%\"')+'></i></div>'" in src
    assert ".job-prog.indet i{width:34%;animation:job-run 1.1s ease-in-out infinite alternate}" in src
    assert "@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation-duration:.01ms !important" in src
    # 相对时间：刚刚 / N 分钟前 / 小时 / MM-DD HH:MM
    assert "function jobAgo(t){" in src
    assert "s<60?'刚刚':s<3600?Math.floor(s/60)+' 分钟前'" in src
    # 行描述带原始 kind（e2e has_text="adapt" 锚点）+ 目标（文章名/URL）
    assert "esc((JN[j.kind]||j.kind)+'（'+j.kind+'）· '+jobTarget(j))" in src
    # 操作按钮：failed→重试（data-resend 既有语义）；done/failed→查看（任务详情查看器）
    assert "'\">重试</button>';" in src
    assert "data-view-job=\"'+esc(j.id)+'\">查看</button>'" in src
    assert "function viewJob(j){" in src
    # 状态徽章保留（e2e .badge.done 锚点）
    assert "<span class=\"badge '+esc(j.status)+'\">'+(JSTAT[j.status]||esc(j.status))+Z" in src
    # 嵌套平衡：job-sub 用 D（</div>）闭合——错用 Z（</span>）会让 job-row 永不闭合、
    # 后续行逐级嵌套进上一行（审查实测：行宽逐级收窄、last-child 边框失效）
    assert "'<div class=\"job-sub\">'+esc(sub)+D:'')" in src
    assert "'<div class=\"job-sub\">'+esc(sub)+Z" not in all_src


def test_task_panel_header_and_clear_done() -> None:
    """任务面板头：进行中计数 + 「清空已完成」；清空仅收起 done 行（§5.4：失败行常驻保留
    重试入口），面板级过滤、jobs.jsonl 留档；无可收起行时按钮禁用。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "'>清空已完成</button></div>'" in src
    assert "var jobsDismissed={};" in src
    # 谓词只收 done——failed 行（带 [重试]）不清除（§5.4 wireframe：失败行常驻）
    assert "state.jobs.forEach(function(j){if(j&&j.status==='done')jobsDismissed[j.id]=1;});" in src
    assert "j.status!=='queued'&&j.status!=='running')jobsDismissed" not in all_src  # 旧谓词退役
    assert "var shown=state.jobs.filter(function(j){return j&&!jobsDismissed[j.id];});" in src
    # 禁用条件=面板内无 done 行（而非「无可清行」——failed 常驻时按钮无操作但仍禁用）
    assert "var done=shown.filter(function(j){return j&&j.status==='done';}).length;" in src
    assert "'+(done?'':' disabled')+'" in src
    # 完成态仍进 jobs-badge 计数口径不变（只数 queued/running）
    assert "j.status==='queued'||j.status==='running'" in src
    # 遗留清偿：面板打开期间 3s 轮询任务进度；三条关闭路径（再点铃铛/外点/Esc）都停
    assert "var jobsPoll=null;" in src
    assert "function jobsPollStop(){if(jobsPoll){clearInterval(jobsPoll);jobsPoll=null;}}" in src
    assert "jobsPoll=setInterval(refreshJobs,3000);" in src
    assert "if(!event.target.closest('#jobs-wrap')){JM.hidden=true;jobsPollStop();}" in src


def test_command_palette_structure_and_open() -> None:
    """命令面板（⌘K）：覆盖层/输入框/列表结构 + ⌘K/Ctrl+K 全局开关。"""
    parsed = _structure()
    assert {"cp", "cp-input", "cp-list"} <= set(parsed.ids)
    src = _shell_js()
    assert 'class="modal-overlay cp-overlay" id="cp"' in src  # 复用 modal 遮罩体系
    assert "(e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){e.preventDefault();$('cp').hidden?cpOpen():cpClose();return;}" in src
    assert "function cpOpen(){" in src and "function cpClose(){" in src
    # 打开即聚焦输入框（UIX-3：经 openDlg 定焦 + 关闭还焦）；文章未载先补拉（复用既有 /api/articles）
    assert "openDlg('cp','#cp-input');" in src
    assert "if(!state.articlesLoaded)loadArticles()" in src
    # 遮罩点击关闭 + 底部键盘提示
    assert "$('cp').addEventListener('click',function(e){if(e.target===this)cpClose();});" in src
    assert "↑↓ 选择" in src


def test_command_palette_filter_and_groups() -> None:
    """面板检索：标题/路径包含匹配 Top 8；三组条目——文章（跳详情）/ 操作（生成变体/创建草稿/检查）/ 跳转。"""
    src = _js()
    assert "function cpArts(q){" in src
    assert "return !q||t.indexOf(q)>=0||p.indexOf(q)>=0;}).slice(0,8);" in src
    assert "function cpBuild(q){" in src
    for group in ("g:'文章'", "g:'操作'", "g:'跳转'"):
        assert group in src, group
    for op in ("'生成变体'", "'创建草稿（发布）'", "'检查'"):
        assert op in src, op
    # 空结果文案（§6.4 空状态）
    assert "没有匹配的文章或操作" in src
    # 遗留清偿：检索词同样作用于操作/跳转组（全组过滤，非只筛文章）
    assert "function hit(s){return !q||s.indexOf(q)>=0;}" in src
    assert "if(hit(o.t))items.push(Object.assign({s:nm},o));" in src
    # 操作目标：详情页当前文章优先，其次首个命中（副标展示目标名）
    assert "if(!$('view-article').hidden&&state.detailPath)t=state.detailPath;" in src
    assert "else if(arts.length)t=arts[0].path;" in src


def test_command_palette_keyboard_navigation() -> None:
    """键盘导航：↑↓ 循环移动 is-cursor / Enter 执行选中项 / Esc 关闭（含 stopPropagation 防全局 Esc 叠加）。"""
    src = _js()
    assert "$('cp-input').addEventListener('keydown'" in src
    assert "e.key==='ArrowDown'||e.key==='ArrowUp'" in src
    assert "CP.cur=(CP.cur+(e.key==='ArrowDown'?1:-1)+CP.items.length)%CP.items.length;cpRender();" in src
    assert "if(CP.items[CP.cur]){var it=CP.items[CP.cur];cpClose();it.run();}" in src
    assert "else if(e.key==='Escape'){e.stopPropagation();cpClose();}" in src
    # 列表点击执行同一路径（data-cp 索引）
    assert "$('cp-list').addEventListener('click'" in src
    assert "var it=CP.items[+row.getAttribute('data-cp')];" in src
    # 渲染：分组头 + is-cursor 高亮 + scrollIntoView 就近滚动
    assert "'<div class=\"cp-item'+(i===CP.cur?' is-cursor':'')+'\" data-cp=\"'+i+'\"" in src
    assert "c.scrollIntoView({block:'nearest'});" in src


def test_command_palette_action_execution() -> None:
    """操作执行：已在目标文章→就地 setTab+触发；跨文章→跳详情（pendAct 由路由落地后触发）。"""
    src = _js()
    assert "function cpGo(path,tab,act){" in src
    # A 类专项（2026-11-20）：setTab 经 VAPI.article 注册表（core 不直呼视图函数）
    assert "if(routeInfo().name==='article'&&state.detailPath===path){if(aa&&aa.setTab)aa.setTab(tab,true);cpFire(act);}" in src
    assert "state.pendAct=act||'';location.hash='#article?path='+encodeURIComponent(path)+'&tab='+tab;" in src
    assert "if(state.pendAct){var act=state.pendAct;state.pendAct='';cpFire(act);}" in src
    # 触发复用既有 handleBatch/handlePublish（confirm 弹层数量不增，仍为 4）
    assert "function cpFire(act){" in src
    assert "if(act==='batch')handleBatch($('variants-batch-btn'));" in src
    assert "if(act==='publish')handlePublish(PB);" in src


def test_keyboard_shortcuts_main_action_and_tabs() -> None:
    """快捷键：⌘⏎ 详情页主操作（编辑→保存/变体→批量/操作→创建草稿）；⌘1-4 切 Tab；Esc 收面板/菜单。"""
    src = _js()
    assert "(e.metaKey||e.ctrlKey)&&e.key==='Enter')" in src
    assert "if(!$('view-article').hidden){e.preventDefault();cpMain();}" in src
    assert "function cpMain(){" in src
    assert "if(state.tab==='edit')handleSaveBody();" in src
    assert "else if(state.tab==='variants')handleBatch($('variants-batch-btn'));" in src
    assert "else if(state.tab==='actions')handlePublish(PB);" in src
    # ⌘1-4：仅详情页生效，映射前四个 Tab（编辑/变体/检查/预览）——2026-11-17
    # 集成轮 ⌘1-7 修为全局分域分支后，tab 优先以「#article 可见且键 ≤4」内联判定；
    # A 类专项（2026-11-20）后 setTab 经 VAPI.article 注册表（core 不直呼视图函数）；
    # 2026-11-22 复核：装载窗口（VAPI.article 未登记）消费为 no-op，绝不回落 DNAV
    # 导航（否则窗口期 ⌘2 被劫持到 #material，违反彼视图优先）
    assert "if((e.metaKey||e.ctrlKey)&&e.key>='1'&&e.key<='7'){" in src
    assert "if(!$('view-article').hidden&&+e.key<=4){if(a1&&a1.setTab)return a1.setTab(['edit','variants','checks','preview'][+e.key-1],true);return;}" in src
    # Esc 逐层收：命令面板 → 批注气泡 → 查看器 → 任务菜单（收菜单同时停轮询）
    assert "if(!$('cp').hidden){cpClose();return;}" in src
    assert "if(!JM.hidden){JM.hidden=true;jobsPollStop();}" in src


# ── 2026-11-15 经验沉淀批：IME 守卫 + 标题 Enter 拆分（沉淀·模式 9/建议1）──


def test_ime_guards_on_all_editor_and_compose_keydown_handlers() -> None:
    """建议1（沉淀·模式 9）：所有 keydown 处理器首行 isComposing/keyCode 229 守卫。

    纯中文创作台不抄必踩：输入法选词期间的 Enter/方向键 keydown（Safari 以
    keyCode 229 发出）不得触发提交/快捷键/面板导航。守卫公式逐字对齐 docmost
    title-editor 判例（沉淀篇 §四-1）。覆盖八处：编辑器批注输入 / 全局快捷键 /
    命令面板输入 / 创作台 slug 与标题两输入 / 素材全库搜索框（F64 Esc 分层：
    先清内容再还焦点）/ 素材行标签输入（F63 Enter 添加——以上七处内联处理器）+
    askConfirm 确认弹层的 document onKey（具名函数，Escape 关闭——IME 取消
    选词的 Escape 不得顺带关掉弹层）。F72 批（2026-11-17）+2 处：dv-tin 行内
    文本输入 + #design 视图域快捷键 document 监听（⌘Z/⌘1-7/Esc）——均守卫首行。
    R4 导入弹窗批（2026-11-19）+1 处：#im 弹层 Esc 关闭的 document 监听
    （IME 取消选词的 Escape 不得顺带关掉导入弹窗）。
    PL-3 插件批（2026-11-20）+1 处：#pr-segs-wrap 段编辑输入 Enter 保存监听
    （IME 选词的 Enter 不得误触保存）——均守卫首行。
    A 类专项（2026-11-20）+3 处：灯箱键盘（material capture）/发布队列 ↑↓·Enter
    （publish）/⌘S·⌘⏎（article）三处 keydown 自 core bindCore 下沉各视图块，
    首行守卫逐字同款——内联 14 处。
    N-2 素材闭环前端批（2026-11-21）+1 处：spark ⌘⇧I 浮层 textarea（IME 选词
    的 Enter 不得误触保存、Escape 不得误关）——内联 15 处。
    AI-3 重生成批（2026-11-21）+1 处：重生成输入条 Enter 提交的 document 监听
    （ks 条在 #pr-segs-wrap、aw 条在 #pr-out，故挂 document 级；IME 选词的
    Enter 不得误触提交）——内联 16 处。
    FEAT-2 智能组稿批（2026-11-22）+2 处：向导主题输入（Enter=下一步）与手动
    追加搜索框（Enter=搜索）两内联监听（IME 选词的 Enter 不得误触向导推进/
    追加搜索）——内联 18 处。
    UIX-3 无障碍批（2026-11-23）+4 处：源详情抽屉 Esc、素材详情抽屉 Esc
    （material-drawer 子段）、插件卡 Enter/Space 激活（plugins 段）、创作工坊
    ⌘⏎ 再生成（compose 段）——内联 22 处。
    """
    src = _js()
    guard = "if(e.isComposing||e.keyCode===229)return;"
    head = "addEventListener('keydown',function(e){\n"
    assert src.count(head + guard) == 22, (
        f"内联 keydown 处理器首行 IME 守卫应为 22 处，实为 {src.count(head + guard)}"
    )
    # 第 23 处：askConfirm 弹层 onKey（具名函数，removeEventListener 需具名引用）
    assert "function onKey(e){ " + guard in src
    assert src.count(guard) == 23, (
        f"IME 守卫总数（内联 22 + onKey 1）应为 23 处，实为 {src.count(guard)}"
    )


def test_compose_title_enter_flows_to_body_start() -> None:
    """建议1 Enter 拆分：slug Enter 前进标题输入；标题 Enter 建会话后焦点流入正文区起点。

    docmost title-editor「Enter 流入正文」的 musespiral 适配——编辑器无独立标题输入
    （标题在 frontmatter，保存时不动），创作台建会话行是唯一标题输入：IME 守卫
    下的 Enter 才生效；会话建立成功（cmNew resolve true）才聚焦正文区，失败停在
    原位（焦点不乱跳）。
    """
    src = _shell_js()
    assert 'id="cm-blocks" tabindex="-1"' in src  # 正文区可编程聚焦（不入 Tab 序）
    assert "$('cm-new-slug').addEventListener('keydown'" in src
    assert "$('cm-new-title').addEventListener('keydown'" in src
    assert "$('cm-new-title').focus();" in src  # slug Enter → 焦点前进到标题
    assert "cmNew().then(function(ok){if(ok)$('cm-blocks').focus();});" in src
    assert "location.hash='#compose?s='+encodeURIComponent(slug);return true;}" in src


def test_material_card_expand_chevron_affordance() -> None:
    """P1 素材行（mat-row）折叠态 chevron + hover 提示 + 展开旋转（P19 affordance 继承）。

    纯 CSS ::after 方案（▾ 字符 + [open] 旋转 180°，零新增 SVG 图标）；hover 提示
    走 summary title 属性（原生 tooltip）；右侧留白 34px 防与行尾已读开关重叠；
    原生 marker 双通道隐藏（webkit-details-marker + list-style:none）。
    """
    src = _js_css()
    assert '.mat-row-sum{display:flex;align-items:center;gap:var(--space-sm);padding:8px var(--space-sm);cursor:pointer;list-style:none;position:relative;padding-right:34px}' in src
    assert '.mat-row-sum::after{content:"▾";position:absolute;' in src
    assert "transition:transform .15s ease}" in src
    assert '<summary class="mat-row-sum" title="展开或收起评分与详情">' in src
    assert ".mat-row-sum:hover::after,.mat-row[open]>.mat-row-sum::after{color:var(--ink-1)}" in src
    assert ".mat-row[open]>.mat-row-sum::after{transform:rotate(180deg)}" in src
    assert ".mat-row-sum::-webkit-details-marker{display:none}" in src

def test_queue_card_keyboard_navigation() -> None:
    """队列卡片 ↑↓/Enter 导航（正典 §6.1 两行快捷键表）：方向键移动 .qsel 描边、Enter 打开选中卡片。

    接管限定队列视图且焦点不在输入控件/按钮、命令面板关闭时——避免劫持
    抓取框输入与 chips 的原生 Enter。
    """
    src = _js_css()
    assert "function moveQSel(d){" in src and "function openQSel(){" in src and "function paintQSel(){" in src
    assert "function qCards(){return [].slice.call((state.sview==='list'?$('pub-rows'):$('article-list')).querySelectorAll(state.sview==='list'?'.pub-row':'.article-card'));}" in src
    # 循环钳位：未选中时 ↓ 取首张 / ↑ 取末张；越界钳回 [0, len-1]
    assert "qsel=qsel<0?(d>0?0:cards.length-1):Math.min(cards.length-1,Math.max(0,qsel+d));" in src
    # Enter 打开选中卡片（复用卡片 href → hash 路由）
    assert "if(qsel>=0&&cards[qsel])location.hash=cards[qsel].getAttribute('href').slice(1);" in src
    # 发布视图自有 keydown 接管（A 类专项 2026-11-20 自 core 下沉本视图；条件与
    # 原文逐字等价：命令面板关闭+队列视图可见+焦点不在输入控件/按钮时不劫持止步）
    assert "if(!$('cp').hidden||$('view-publish').hidden)return;" in src
    assert "if(e.target.closest&&e.target.closest('input,select,textarea,button'))return;" in src
    assert "moveQSel(e.key==='ArrowDown'?1:-1);return;" in src
    assert "if(e.key==='Enter'&&qsel>=0){e.preventDefault();openQSel();return;}" in src
    # 选中描边样式 + 重渲染恢复（renderPublish 尾部 repaint，筛选切换重置）；列表态同款
    assert ".article-card.qsel{outline:2px solid var(--vermilion);outline-offset:2px}" in src
    assert ".pub-row.qsel{outline:2px solid var(--vermilion);outline-offset:2px}" in src
    assert "cards[i].classList.toggle('qsel',i===qsel);" in src
    assert "if(qsel>=cards.length)qsel=cards.length-1;" in src
    assert "qsel=-1;" in src
    # D4（设计终评 §三 P1-D4）：统计行计算移至 renderPublish 头部（先算后渲染，
    # 列表渲染异常也不失更新）；重渲染恢复 repaint 仍在尾部，筛选切换重置
    assert "var items=filterArticles();\nupdateChipCounts();" in src
    assert "}\npaintQSel();\n}" in src


# ── 第八期轮次四（R9）：batch_adapt 一键入口 ────────────────


def test_batch_adapt_button_wired() -> None:
    """操作面板「四平台一键生成变体」按钮：id 存在、点击绑定、四平台与 trackJob 复用。"""
    parsed = _structure()
    src = _shell_js()
    assert "batch-btn" in parsed.ids  # 等价标记：按钮唯一 id
    assert "四平台一键生成变体" in src  # 按钮文案
    assert "$('batch-btn').addEventListener('click'" in src  # 点击绑定
    assert "['xhs','channels','douyin','zhihu']" in src  # 平台清单硬编码四平台
    assert "await queued(data.job_id)" in src  # 复用 trackJob 轮询（queued=toast+trackJob）
    # 重发路径（任务面板）对 batch_adapt 重建 platforms 请求体
    assert "if(j.kind==='batch_adapt')b.platforms=p.platforms||[];" in src


# ── 第九期 F20/F21：环境健康面板 + 队列页从 URL 抓取 ────────


def test_health_card_structure_markers() -> None:
    """F20：仪表盘顶部「环境健康」卡片：容器/渲染目标 id 与标题存在。"""
    parsed = _structure()
    assert {"health-card", "health-grid"} <= set(parsed.ids)
    src = _shell_js()
    assert "环境健康" in src  # 卡片标题
    assert "'/api/health'" in src  # renderHealth 拉取端点


def test_health_card_items_and_fix_commands() -> None:
    """F20：六类健康项 data-health 标记齐全；未就绪项展示对应 CLI 修复命令文本。

    修复命令与后端 /api/health 字段的对应（ui.html 内注释同映射）：
    credentials→musespiral config set；ai→musespiral ai config；
    imagehost→musespiral imagehost config；cookies→musespiral upload login；
    jobs/disk 为积压/磁盘信号，无对应修复命令。
    """
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 行模板统一打 data-health 标记；各项标记以 hrow 调用字面量出现（JS 拼接无整串字面量）
    assert "data-health=\"'+m+'\"" in src
    for mark in ("credentials", "ai", "imagehost", "cookies", "jobs", "disk"):
        assert f"'{mark}'" in src
    for cmd in (
        "musespiral config set …",
        "musespiral ai config …",
        "musespiral imagehost config …",
        "musespiral upload login …",
    ):
        assert cmd in src
    for name in ("视频号", "抖音", "小红书", "快手"):  # cookies 覆盖四平台
        assert name in src
    # 就绪/未就绪两种渲染分支走 monoline SVG（正典 §3.4/§6.4：不用 emoji）
    assert "mdpubIcon(ok?'check':'warning')" in src
    assert "✅" not in all_src and "⚠️" not in all_src  # emoji 出清锁死


def test_fetch_bar_structure_and_hint() -> None:
    """F21：队列页顶部抓取入口：输入框/按钮/固定低频提示文案。"""
    parsed = _structure()
    assert {"fetch-url", "fetch-btn", "fetch-hint"} <= set(parsed.ids)
    src = _source()
    assert "从 URL 抓取" in src  # 按钮文案
    assert "微信有频控；批量建议浏览器另存 HTML 走 CLI --html" in src  # 固定低频提示
    assert "mp.weixin.qq.com" in src  # 白名单域提示进 placeholder


def test_fetch_action_post_contract() -> None:
    """F21：POST /api/action/fetch 请求体 {url, confirm:true} 并复用 job 轮询/toast。"""
    src = _js()
    assert "postJSON('/api/action/fetch', {url: url, confirm: true})" in src
    assert "$('fetch-btn').addEventListener('click'" in src  # 点击绑定
    assert "await queued(d.job_id)" in src  # 复用 trackJob 轮询
    # 失败 fetch 任务的重发：按 kind 重建 url 请求体（避免误路由 batch_adapt 兜底）
    assert "if(j.kind==='fetch')b={url:p.url,confirm:true};" in src


# ── P20 M3：「素材」视图（发现 Tab 更名收编：第三导航 Tab + 素材卡片 + 四维分解）──


def test_material_view_structure() -> None:
    """P1 素材库（§3.2）：集合轨（静态+智能集合+源集合）+ 列表主区 + 详情抽屉三段布局。"""
    parsed = _structure()
    assert "view-material" in parsed.ids
    assert "material" in parsed.attrs["data-view"]
    assert "material" in parsed.attrs["data-nav"]
    assert "discover" not in parsed.attrs["data-nav"]
    # 三段布局锚点（重构设计 §3.2 线框：240px 轨 + 列表 + 320px 抽屉）
    assert {"coll-rail", "coll-static", "coll-smart", "coll-srcs", "mat-list", "mat-empty", "mat-drawer"} <= set(parsed.ids)
    assert {"mat-drawer-title", "mat-drawer-body", "mat-drawer-close"} <= set(parsed.ids)
    assert {"mat-search", "mat-type", "mat-sort"} <= set(parsed.ids)
    src = _shell_js()
    # 数据面：素材查询+评分现算（M3 后经 matQueryUrl 全参上移——q/collection 走服务端 F64/F62）
    assert "getJSON(matQS(state.mquery||state.mcoll?500:200))" in src
    assert "function matQS(limit){" in src
    assert "getJSON('/api/settings')" in src  # 源集合数据源
    # 集合轨数据驱动：静态分区 + 智能集合（预置三规则）+ 源集合（计数）
    assert "function renderCollRail(){" in src
    assert "ci(state.mtab==='hiunread','data-mtab=\"hiunread\"','◈ 高分未读',hi)" in src
    assert "ci(state.mtab==='derived','data-mtab=\"derived\"','◈ 衍生素材',de)" in src
    assert "ci(state.mtab==='revisit','data-mtab=\"revisit\"','◈ 待重温',null)" in src
    assert "ci(state.msrc===s.id,'data-msrc=\"'+esc(s.id)+'\" data-mtab=\"all\"',esc(s.label||s.id),n)" in src
    # 智能集合语义（§3.2）：高分未读=score≥80∧未读；衍生=type=derived；待重温=收藏按周龄降序
    assert "else if(tab==='hiunread')L=L.filter(function(m){return !(m.triage||{}).read&&(m.score||0)>=80;});" in src
    assert "else if(tab==='derived')L=L.filter(function(m){return m.type==='derived';});" in src
    assert "else if(tab==='revisit')L=L.filter(function(m){return (m.triage||{}).starred;}).sort(function(a,b){return String(b.fetched_at||'').localeCompare(String(a.fetched_at||''));});" in src
    # 列表域三件套（M3 后搜索=防抖 300ms 服务端 q；类型/排序仍纯前端）
    assert "searchDebounce=setTimeout(matRefetch,300);" in src or "sDb=setTimeout(matRefetch,300);" in src
    assert "function fillMatType(){" in src
    assert "state.msort==='time'" in src
    # 视图头统计行与空态引导
    assert "' · 收藏 '+sn+' · 未读 '+ur+' · 已生成骨架 '+df" in src  # M3 后前缀按 搜索命中/集合页/全量 三态
    assert "在源中枢验证源后点「刷新」" in src

def test_material_card_score_bar_and_breakdown() -> None:
    """M3（P19 继承）：dc-bar 三档色 + 四维分解条四行条尾数值 + chevron 卡壳 + 分数大数字。"""
    src = _js_css()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 分数横条三档色（≥80 hi / ≥60 mid / <60 lo；无分素材不渲染横条只显 —）
    assert ".dc-bar{display:block;height:5px;" in src
    assert ".dc-bar.hi i{background:var(--vermilion)}" in src
    assert ".dc-bar.mid i{background:var(--ink-1)}" in src
    assert ".dc-bar.lo i{background:var(--ink-2)}" in src
    assert "t=s==null?-1:s,lv=t>=80?'hi':t>=60?'mid':'lo'" in src
    assert "'<span class=\"dc-bar '+lv+'\" title=\"评分 '+s+'\"><i style=\"width:'+t+'%\"></i>'" in src
    # 四维分解条（深度/独特/可读/适配）复用 pbar 三件套，四行齐全（kb 现算才有）
    for label in ("深度", "独特", "可读", "适配"):
        assert f"['{label}'," in src, label
    assert "'<div class=\"pbar\"><span class=\"pbar-name\">'+DD[i][0]" in src
    assert "<span class=\"pbar-track\"><span class=\"pbar-fill\" style=\"width:'" in src
    assert "</span><span class=\"pbar-n\">'+(b[DD[i][1]]||0)+'/'+DD[i][2]+'</span></div>'" in src
    assert "if(b.depth!=null)for(i=0;i<4;i++){" in src  # radar 信号无四维不渲染
    assert "--wash:color-mix(in srgb,var(--ink-1) 8%,transparent)" in src
    assert "--wash:var(--wash)" not in all_src
    # mat-row 卡壳（chevron affordance 见专用测试）+ 类型/评级 tag + 未读点/禁用徽章 + 评分数字入展开区
    assert (
        "'<details class=\"mat-row'+(state.mdens==='full'?' open':'')+'\" data-mid=\"'+esc(m.id)+'\">'" in src
    )
    assert "g+=(g?'·':'')+DD[i][0]+(b[DD[i][1]]||0)+'/'+DD[i][2];" in src
    assert "esc(MTY[m.type]||m.type)+(meta.line?' · '+esc(meta.line):'')" in src
    assert "(tri.read?'':'<i class=\"unread-dot\" title=\"未读\"></i>')" in src
    assert "(tri.enabled?'':' <span class=\"badge archived\">已禁用</span>'+Z)" in src
    assert "'<p class=\"action-hint\">❝ '" in src
    assert "(s==null?'':' · 评分 '+(s==null?'—':s))" in src  # 评分数字入展开区 meta（无分不渲染）


def test_material_draft_button_and_drafted_state() -> None:
    """M3：[生成骨架]（kb text 素材）→ POST discover_draft；drafted 已入队徽章+禁用+队列刷新。"""
    src = _js()
    assert ">生成骨架</button>" in src
    assert "'<button class=\"btn\" data-mdraft=\"'+esc(o.uri)+'\"'+(m.drafted?' disabled':'')" in src
    assert "' <span class=\"badge pass\">已入队</span>'" in src  # drafted 卡片徽章
    # 动作契约：POST /api/action/discover_draft {path: origin.uri, confirm:true}（复用既有端点）
    assert "postJSON('/api/action/discover_draft',{path:p,confirm:true})" in src
    # 成功后：素材置 drafted 重渲染 + 队列强刷（新骨架出现在队列页）
    assert "if((c2.origin||{}).uri===p)c2.drafted=true;" in src
    # 首载骨架（A 类专项：renderSkeleton 真身迁 core，仍被 loadArticles 首载引用）
    assert "if(!state.articlesLoaded) renderSkeleton();" in src
    # 生成后重绘本视图 + 队列强刷（A 类专项：跨视图重绘经 viewRepaint 注册表）
    assert "renderMaterials();\nawait loadArticles(true);\nviewRepaint('publish');" in src
    # 事件绑定：卡片动作委托 + 密度三 chips（document 级委托，切换即重渲染）
    assert "$('mat-list').addEventListener('click'" in src
    # M3 扩：集合/标签深链过滤并入同一 document 级委托
    assert "e.target.closest('[data-msrc],[data-mtab],[data-mdens],[data-mcoll],[data-mtag]')" in src
    # 按钮点击 preventDefault 防误折叠（与 P19 同纪律）；kb 素材才出骨架按钮
    assert "if(el.hasAttribute('data-mdraft')){if(!el.disabled)handleMatDraft(el);return;}" in src
    assert "(m.type==='text'?'<button class=\"btn\" data-mdraft=\"" in src
    # radar 信号：meta.url「查看原文」外链（rel=noopener）
    assert "((meta.url||p.url)?'<a class=\"btn\" href=\"'+esc(meta.url||p.url)+'\" target=\"_blank\" rel=\"noopener\">查看原文</a>':'')" in src  # M3 扩：video payload.url 同亮


def test_material_routing_and_palette_entry() -> None:
    """P1：路由纳入 material（VIEWS 白名单+HALIAS 别名+hash 五参数恢复）+ ⌘K「跳转：素材库」。"""
    src = _shell_js()
    assert "var VIEWS=['sources','material','books','compose','design','publish','insight','article','settings','plugins','plugin-run'];" in src
    assert "var HALIAS={queue:'publish',dashboard:'insight',discover:'material','plugin-pipeline':'plugin-run'};" in src
    assert (
        "VAPI.material={enter:async function(route){\n"
        "await loadSettings();\n"
        "var msp=route.params.get('src'),mtp=route.params.get('tab'),mdp=route.params.get('dens'),mty=route.params.get('type'),mid=route.params.get('m'),mcl=route.params.get('coll'),mtg=route.params.get('tag');\n"
        "if(msp!=null)state.msrc=msp;\n"
        "if(mtp==='review')mtp='revisit';\n"
        "if(mtp==='all'||mtp==='starred'||mtp==='unread'||mtp==='hiunread'||mtp==='derived'||mtp==='revisit')state.mtab=mtp;\n"
        "if(mdp==='compact'||mdp==='src'||mdp==='full')state.mdens=mdp;\n"
        "if(mty!=null)state.mtype=mty;\n"
        "var msk=route.params.get('spark');if(msk!=null)state.mspark=msk?'spark':'';\n"
        "if(mcl!=null)state.mcoll=mcl;\n"
        "if(mtg!=null)state.mtag=mtg;\n"
        "await Promise.all([loadMaterials(),loadColls(),loadTags()]);\n"
        "fillMatType();\n"
        "renderCollRail();\n"
        "renderMaterials();\n"
        "if(mid){await matPart('drawer');await openMatDrawer(mid);}"
        in src
    )
    assert "href=\"#material\"" in src
    # P4 cmdk v2 后跳转组改由 NAVS 表驱动（行为等价；icon/label/hash 三元组）
    assert "['material','素材库','search']" in src
    assert "items.push({g:'跳转',icon:n[2],t:'跳转：'+n[1],run:function(){cpClose();location.hash='#'+n[0];}});" in src

# ── 第十八期 F46/F47：设置视图（P1 shell 后=rail 底部钉住的导航位）──


def test_settings_view_structure() -> None:
    """F46/F47：设置视图 section 骨架——KB 域表格 + AI 三层面板（档案/风格/prompts）。"""
    parsed = _structure()
    assert "view-settings" in parsed.ids
    assert "settings" in parsed.attrs["data-view"]
    # P1 shell（§2.1）形态翻转：设置从「顶栏齿轮弹层」改为 rail 底部钉住的导航位
    # （.rail-foot 内 .nav-item，带 data-nav 供 renderNav 激活态；顶栏不再放齿轮）
    assert "settings" in parsed.attrs["data-nav"]
    assert "settings-btn" in parsed.ids  # rail 底部设置入口（§2.1：设置钉栏底，顶栏无齿轮）
    src = _js()
    # KB 域：根输入 + 源清单表格（key/标签/路径/校验态）+ 增删
    for el_id in ("kb-root-input", "kb-save-btn", "kb-sources-body", "kb-add-key", "kb-add-btn"):
        assert el_id in parsed.ids, el_id
    assert "data-kb-del=" in src  # 表格行删除（本地暂存，保存时全量提交）
    # AI 三层：① 档案与路由（表格+编辑+默认+路由）② 风格资产 ③ 底座 prompts
    for el_id in (
        "ai-profiles-body", "ai-p-name", "ai-p-url", "ai-p-model", "ai-p-key", "ai-p-save",
        "ai-default-select", "ai-default-save", "ai-route-task", "ai-route-profile", "ai-route-save",
        "style-area", "style-save", "prompt-task-select", "prompt-area", "prompt-save",
    ):
        assert el_id in parsed.ids, el_id
    assert "style-habits" in parsed.ids and "prompt-state" in parsed.ids


def test_settings_view_contracts() -> None:
    """F46/F47：设置面写动作契约（confirm:true / key write-only 占位 / 覆盖级联提示）。"""
    src = _js()
    # KB 保存走 confirm 弹层 + POST /api/settings/kb（全量 root+sources）
    assert "postJSON('/api/settings/kb',{root:root,sources:sources,confirm:true})" in src
    assert "放宽扫描边界" in src  # confirm 文案言明该动作放宽扫描边界
    # AI 域：全部动作经 aiPost → POST /api/settings/ai（confirm:true 统一注入）
    assert "postJSON('/api/settings/ai',Object.assign({confirm:true},body))" in src
    for action in ("save_profile", "set_default", "set_route", "save_style", "save_prompt"):
        assert f"action:'{action}'" in src, action
    # key write-only：编辑档案时空值不传（保留旧值），占位「已配置，输入以更换」
    assert "if($('ai-p-key').value)b.api_key=$('ai-p-key').value;" in src
    assert "p.has_key?'已配置，输入以更换'" in src
    # prompts 覆盖级联：未覆盖提示用内置缺省；保存后 GET 刷新（overrides 驱动已覆盖态）
    assert "'未覆盖（用内置缺省）；填写即建 prompts.d/'+t+'.txt'" in src
    # GET /api/settings 两域读取（chips 与设置视图共用）
    assert "state.settings=await getJSON('/api/settings')" in src


def test_kb_origin_icon_via_innerhtml_with_escaped_data() -> None:
    """D-3 回归（验收报告 2026-11-17 §二）：#kb-origin 图标渲染契约。

    根因形态禁令：I() 返回 SVG 标记字符串，赋给 .textContent 会把标记当
    纯文字显示（DOM 取证 childElementCount=0 / textContent 含 '<svg'）。
    契约三件：① 赋值走 .innerHTML（图标真渲染成元素）；② 图标仍由
    I(root_exists?'check':'warning') 内联；③ 服务端数据 kb.root 必经 esc()
    转义后才能进 innerHTML（防注入不随改法降级）。另设全产物同类门：任何
    「.textContent= 赋值行不得含 I(/qArt(/mdpubIcon 标记源」（防下一处复刻）。
    真机 DOM 断言（svg 元素存在 + 转义后无注入元素）在 tests/e2e/test_studio_e2e.py
    的 D-3 用例，本文件按其纪律做静态面互证。
    """
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    rows = [line for line in src.splitlines() if "kb-origin" in line and ("innerHTML" in line or "textContent" in line)]
    assert rows, "#kb-origin 渲染行缺失"
    row = rows[0]
    assert "$('kb-origin').innerHTML=" in row  # ① 标记进 DOM 而非纯文本
    assert "I(kb.root_exists?'check':'warning')" in row  # ② 图标内联两态
    assert "esc(kb.root)" in row  # ③ 数据转义（innerHTML 下的注入防线）
    assert "$('kb-origin').textContent" not in all_src  # 旧形态整体退役
    # 同类门：I()/qArt()/mdpubIcon 产出的标记绝不允许再进任何 .textContent 赋值行
    for line in src.splitlines():
        if ".textContent=" in line:
            assert not any(m in line for m in ("I(", "qArt(", "mdpubIcon")), (
                f"图标标记被赋给 textContent（D-3 同类复发）: {line[:120]}")


# ── 第十八期 F48-F52：创作视图（第四导航 Tab + 三栏工作区，M3）──


def test_compose_view_structure() -> None:
    """F48：第四导航 Tab（队列/发现/创作/仪表盘顺序）+ 三栏 section 骨架 id 齐备。"""
    parsed = _structure()
    assert "view-compose" in parsed.ids
    assert "compose" in parsed.attrs["data-view"]
    assert "compose" in parsed.attrs["data-nav"]  # rail「转化」分组导航位（.nav-item，P1 shell 后）
    assert {"compose-sub", "cm-sessions", "cm-empty", "cm-grid", "cm-new-slug", "cm-new-btn"} <= set(parsed.ids)
    # 三栏容器：素材面板 / 分块裁决面 / 轮次面板（§2.5 线框三栏）
    assert {"cm-pool", "cm-add-path", "cm-add-btn", "cm-sources", "cm-budget"} <= set(parsed.ids)
    assert {"cm-round-title", "cm-blocks", "cm-blocks-empty"} <= set(parsed.ids)
    assert {"cm-rounds", "cm-diff", "cm-generate-btn", "cm-adopt-btn"} <= set(parsed.ids)
    # 三栏布局 CSS（窄屏单列响应式）
    src = _shell_js_css()
    assert ".cm-grid{display:grid;grid-template-columns:280px minmax(0,1fr) 300px" in src
    assert ".cm-grid{grid-template-columns:1fr}" in src
    # 路由白名单（P1 §2.2）：VIEWS 清单 + HALIAS 别名 + hash #compose?s= 会话选中
    assert "var VIEWS=['sources','material','books','compose','design','publish','insight','article','settings','plugins','plugin-run'];" in src
    # A 类专项（2026-11-20）：compose 路由分支归视图（VAPI.compose.enter）
    assert "VAPI.compose={enter:async function(route){" in src
    assert 'href="#compose"' in src
    # 空状态引导文案（未建会话时）
    for label in ("暂无创作会话", "分块三态裁决"):
        assert label in src, label


def test_compose_pool_sources_and_budget() -> None:
    """左栏素材面板：发现页高分候选（≥80）+ 手动添加 + 章节勾选树 + 预算仪表。"""
    src = _js()
    # 候选池：/api/discover 高分过滤 + 已入池改移除态（F-C 2026-11-18）
    assert "return (c.score||0)>=80;}).slice(0,6);" in src
    assert "data-cm-rm=\"'+esc(c.path)+'\"" in src  # 移除按钮（红字 btn-ghost）
    assert ">移除</button>" in src
    assert "addCmSource(b.getAttribute('data-cm-add'));" in src
    # 手动添加：同一入池动作（服务端解析 KB 相对/绝对路径）
    assert "addCmSource(p);" in src
    # 章节勾选树：known_sections 数据驱动 checkbox（未勾选=全文语义保留）+ 已选 N/M 计数
    assert "data-cm-sec=\"'+esc(path)+'\" value=\"'+esc(k)+'\"" in src
    assert "未勾选=全文" in src
    assert "s.sections.length+'/'+known.length+' 章'" in src
    assert "x.checked&&x.getAttribute('data-cm-sec')===path;" in src
    # add 响应缓存 known_sections/原始路径（详情只回 basename，勾选需可解析路径）
    assert "if(d.known_sections)state.cmKnown[nm]=d.known_sections;" in src
    assert "state.cmPaths[nm]=path;" in src
    # 预算仪表：2000 字/素材 × 8000 字会话总预算，pbar 复用
    assert "Math.min(100,Math.round(n*2000/8000*100))" in src
    assert "会话总预算 8000 字" in src


def test_compose_blocks_three_state_adjudication() -> None:
    """中栏分块裁决面：每块头部三态操作（保留/编辑行内改/丢弃弹方向备注）+ 三态着色。"""
    parsed = _structure()
    src = _js_css()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "CM_ACT={keep:'保留',adopt_edited:'改后采纳',discard:'已丢弃'}" in src
    for attr in ("data-cm-keep", "data-cm-edit", "data-cm-drop", "data-cm-save", "data-cm-dropok"):
        assert attr in src, attr
    # 三态动作契约：POST /api/compose/decide（round+block+action+confirm；编辑带 text、丢弃带 direction）
    assert "Object.assign({slug:state.cslug,round:state.cround,block:bid,action:action,confirm:true},extra||{})" in src
    assert "cmDecide(el.getAttribute('data-cm-save'),'adopt_edited',{text:" in src
    assert "'discard',{direction:el.parentNode.querySelector('input').value.trim()}" in src
    # 编辑=行内 textarea 替换块体（划词批注气泡同款 preventScroll 焦点纪律）
    assert "host.innerHTML='<textarea class=\"input cm-edit-area\" rows=\"5\">" in src
    assert "host.querySelector('textarea').focus({preventScroll:true});" in src
    # 丢弃=行内方向备注输入（非 window.prompt——空态引导弹层体系内交互）
    assert "丢弃方向备注（喂下轮 prompt）" in src
    assert "window.prompt" not in all_src
    # 三态着色复用 badge 三色 + 状态左边条（keep 绿 / adopt_edited 橙 / discard 红+删除线）
    for cls in (
        ".cm-block.st-keep{border-left-color:var(--jade)",
        ".cm-block.st-adopt_edited{border-left-color:var(--amber)",
        ".cm-block.st-discard{border-left-color:var(--crimson)",
        ".cm-block.st-discard .cm-block-body{text-decoration:line-through",
    ):
        assert cls in src, cls
    assert "st==='keep'?'pass':st==='adopt_edited'?'warn':'fail'" in src
    # AI 新块 vs 锚继承块视觉区分：左边条双色 + 血统徽章
    assert ".cm-block.inh{border-left-color:var(--vermilion)}" in src
    assert ".cm-block.new{border-left-color:var(--ink-2)}" in src
    assert "inh?'<span class=\"badge running\">锚继承</span>':'<span class=\"badge queued\">AI 新块</span>'" in src
    # 锚继承判定：id 出现在上轮块集（首轮对照 draft 真稿）
    assert "(prev?prev.blocks:(state.csd.draft||[])).forEach(function(b){ids[b.id]=1;});" in src


def test_compose_rounds_panel_and_actions() -> None:
    """右栏轮次面板：轮次时间线 + N-1↔N 按块对照 + 再生成（job）+ 采纳入稿（confirm）。"""
    src = _js_css()
    # 轮次时间线 chips（chip-count=已裁决/总块数；点选切换中栏展示轮）
    assert "data-cm-round=\"'+r.n+'\">第 '+r.n+' 轮<span class=\"chip-count\">" in src
    assert "state.cround=+c.getAttribute('data-cm-round');" in src
    # 按块对照（非按行）：新块/改写（锚继承）/上轮块未再现 三档着色
    assert "'+I('plus')+'+ '+esc(b.id)+' 新块</div>'" in src
    assert "'+I('edit')+'~ '+esc(b.id)+' 改写（锚继承）</div>'" in src
    assert "'+I('error')+'− '+esc(b.id)+' 上轮块未再现</div>'" in src
    for cls in (".cm-diffrow.add{color:var(--jade)}", ".cm-diffrow.del{color:var(--crimson)}", ".cm-diffrow.chg{color:var(--amber)}"):
        assert cls in src, cls
    # 再生成：POST /api/compose/generate 走 job 面板（trackJob 轮询 + 完成重载会话，
    # 并自动跳到新轮次——新产出就是要裁决的对象）
    assert "postJSON('/api/compose/generate',{slug:state.cslug,confirm:true})" in src
    assert "state.cround=R.length?R[R.length-1].n:0;\nrenderCompose();}" in src
    # 任务面板 kind 中文名收编 compose_generate；该 kind 不走动作重发（回创作台再发起）
    assert "compose_generate:'创作再生成'" in src
    assert "if(j.kind==='compose_generate'){toast('请在创作台重新发起再生成',ER);return;}" in src
    # 采纳入稿：confirm 弹层（块级人工最后一步）→ 正稿入队列刷新
    assert "postJSON('/api/compose/adopt',{slug:state.cslug,confirm:true})" in src
    assert "title:'采纳入稿'" in src
    assert "采纳后会话封版不可再生成" in src
    # A 类专项：跨视图重绘经 viewRepaint（原直呼 renderPublish）
    assert "await loadArticles(true);viewRepaint('publish');}" in src
    # 会话状态门：非 composing 禁用两按钮
    assert "$('cm-generate-btn').disabled=d.status!=='composing';" in src
    assert "$('cm-adopt-btn').disabled=!(d.status==='composing'&&R.length);" in src


def test_compose_anchor_filter_shared_renderer() -> None:
    """R2：分块渲染与编辑 Tab 预览抽共享层（mdToHtml），<!--b:id--> 锚在渲染时滤除。

    编辑 Tab 实时预览与创作台分块渲染（blockHtml）走同一渲染函数；锚行在
    mdToHtml 行循环内被跳过——对既有正文（无锚）行为零变更，对带锚真稿
    （人工直接改 draft.md 后重进会话）预览不可见。
    """
    src = _js()
    assert "var BANCHOR=/^<!--b:[A-Za-z0-9_-]+-->[ \\t]*$/;" in src
    assert "if(BANCHOR.test(L)){i++;continue;}" in src
    assert "function blockHtml(b){return mdToHtml(b.text,'');}" in src
    # 共享层两端消费：编辑预览 renderLive 与分块渲染 cmBlockRow 同走 mdToHtml
    assert "mdToHtml(EA.value,dirOf(state.detailPath));}" in src
    assert "'<div class=\"cm-block-body\">'+blockHtml(b)+'</div></div>'" in src


def test_compose_new_session_flow() -> None:
    """建会话链：slug 必填 → POST /api/compose/new（confirm）→ hash 路由进三栏。"""
    src = _js()
    assert "postJSON('/api/compose/new',{slug:slug,title:$('cm-new-title').value.trim()||slug,confirm:true})" in src
    assert "location.hash='#compose?s='+encodeURIComponent(slug);return true;}" in src
    assert "slug 不能为空" in src
    # 会话 chips 选中态 + 点选路由（P1 起 s= 白名单参数；slug= 旧深链兼容读取）
    assert "data-cm-sel=\"'+esc(s.slug)+'\"" in src
    assert "location.hash='#compose?s='+encodeURIComponent(c.getAttribute('data-cm-sel'));" in src
    # 会话详情装载（GET /api/compose/session?slug=；未知 slug 降级清态）
    assert "getJSON('/api/compose/session?slug='+encodeURIComponent(slug))" in src
    # 建会话后强刷会话清单
    assert "await loadCompose(true);toast('会话已建：'+slug,OK);" in src


# ── 第十四期 F39：仪表盘健康卡「数据」行回采按钮（本期 UI 唯一增量） ──


def test_dashboard_health_data_row_sync_stats_button() -> None:
    """F39：健康卡新增「数据」行（含回采按钮）；POST 契约 confirm:true；任务
    面板 kind 标签收编 sync_stats；完成后刷新统计瓷砖（loadStats+renderDashboard）。"""
    src = _js()
    # 健康卡「数据」行：hcard 结构 + 回采按钮
    assert "data-health=\"data\"" in src
    assert "id=\"sync-stats-btn\"" in src
    assert ">回采</button>" in src
    # 动作契约：走既有 POST 动作面 + confirm:true + 既有 job 追踪（queued→trackJob）
    assert "postJSON('/api/action/sync_stats',{confirm:true})" in src
    assert "await queued(d.job_id)" in src
    # 完成后刷新瓷砖（统计面 + 仪表盘重绘）
    assert "await loadStats(true);renderInsight();" in src
    # 任务面板 kind 中文名收编
    assert "sync_stats:'数据回采'" in src


# ── 档A 全局与状态组（建议4/5/6/7 + A2 四维数值化 + 收藏星标三 tab，2026-11-15）──


def test_sys_badge_three_states_silent_when_normal() -> None:
    """建议4（沉淀·模式 18）：静默即正常徽章——离线/磁盘将满/渲染失败三态顶栏
    徽章 + hover 细分文案；正常态零渲染（hidden 直返，不产 DOM 内容）。"""
    parsed = _structure()
    assert "sys-badge" in parsed.ids  # 顶栏徽章锚点（静态 hidden）
    src = _js()
    # 数据化状态表（沉淀·模式 17）：三态各带 图标/短标/细分文案
    assert "var SYSB=[" in src
    for label in ("后端离线", "磁盘将满", "渲染失败"):
        assert label in src, label
    assert "后端 8766 不可达" in src and "磁盘剩余不足 5GB" in src
    assert "musespiral check --render 排查" in src
    # 正常态零渲染：无活动态直接 hidden 返回
    assert "if(!act.length){b.hidden=true;return;}" in src
    # hover 细分文案：title 聚合全部活动态（零依赖 tooltip）
    assert "b.title=act.map(function(s){return s[3];}).join('；');" in src
    # 三态信号源：①fetch 网络异常置 offline（成功即清除）②/api/health 磁盘 <5GB
    # （probeHealth 惰性探测，正常态不渲染）③详情 renderable===false 置 rfail
    assert "catch(err){if(!state.offline){state.offline=true;renderSys();}throw err;}" in src
    assert "if(state.offline){state.offline=false;renderSys();}" in src
    assert "state.diskLow=(h.disk_free_gb||99)<5" in src
    assert "state.rfail=(detail.renderable===false)?path:'';" in src
    assert "probeHealth();" in src  # init 装载（任务角标同批）


def test_filters_persist_in_hash() -> None:
    """建议5（沉淀·模式 2，P19 继承）：视图内筛选一律 replaceState 写 hash（不进历史栈），
    刷新恢复；素材空态二分「过滤无结果（清除筛选）vs 本来就没有（引导）」。"""
    src = _js()
    # 发布中心：syncPubHash 写 #publish?filter=<档>&view=<形态>；renderRoute 读白名单恢复
    assert "history.replaceState(null,'','#publish'+(q.length?'?'+q.join('&'):''));" in src
    assert "var qf=route.params.get('filter');" in src
    assert "['all','draft','pending','reviewed','published','fail'].indexOf(qf)>=0" in src
    assert "if(state.filter!=='all')q.push('filter='+state.filter);" in src
    assert "if(state.sview!=='grid')q.push('view='+state.sview);" in src
    # 素材页：src/tab/dens/type/m 五参数（m= 打开详情抽屉；review=待重温别名）
    assert "var q=['src='+encodeURIComponent(state.msrc)];" in src
    assert "if(state.mtype)q.push('type='+state.mtype);" in src
    assert "if(state.mdrawer)q.push('m='+state.mdrawer);" in src
    assert "history.replaceState(null,'','#material'+(q.length?'?'+q.join('&'):''));" in src
    assert "var msp=route.params.get('src'),mtp=route.params.get('tab'),mdp=route.params.get('dens'),mty=route.params.get('type'),mid=route.params.get('m'),mcl=route.params.get('coll'),mtg=route.params.get('tag');" in src
    # M3 扩：集合页/标签过滤深链（syncMatHash coll/tag 双参）
    assert "if(state.mcoll)q.push('coll='+encodeURIComponent(state.mcoll));" in src
    assert "if(state.mtag)q.push('tag='+encodeURIComponent(state.mtag));" in src
    assert "if(mtp==='review')mtp='revisit';" in src
    assert "if(mtp==='all'||mtp==='starred'||mtp==='unread'||mtp==='hiunread'||mtp==='derived'||mtp==='revisit')state.mtab=mtp;" in src
    # 源中枢：行选中 src= 写 hash；洞察：range 30/90 写 hash
    assert "function syncSrcHash(){history.replaceState(null,'','#sources'+(state.ssrc?'?src='+encodeURIComponent(state.ssrc):''));}" in src
    assert "history.replaceState(null,'','#insight'+(state.irange!==30?'?range='+state.irange:''));" in src
    # 空态二分：filtered（来源/分诊/类型/搜索任一）→ 清除筛选按钮；本来就没有 → 引导
    assert "var filtered=state.msrc!==''||tab!=='all'||!!state.mtype||!!state.mspark||!!state.mquery||!!state.mtag||!!state.mcoll;" in src
    assert "filtered?'没有符合条件的素材'" in src
    assert "清除筛选看全部" in src
    assert "$('mat-empty-action').hidden=!filtered;" in src
    parsed = _structure()
    assert "mat-empty-action" in parsed.ids
    assert "mat-empty-title" in parsed.ids and "mat-empty-text" in parsed.ids
    assert "var MEMPTY_T=$('mat-empty-title').textContent" in src
    # 清除动作：回全部来源+全部 tab+全部类型+清搜索（客户端过滤，无需重拉）
    assert "state.msrc='';state.mtab='all';state.mtype='';state.mspark='';state.mconf='';state.mquery='';" in src  # N-4 清筛选含低置信

def test_check_fail_tooltip_and_material_gap_badge() -> None:
    """建议6（沉淀·模式 22）：check 失败暗朱 chip 悬停见原文（详情汇总 badge
    title=FAIL/WARN 原文串；队列卡 qcheck chip title 指路检查 Tab）；物料缺口
    角标计数（封面/FAIL 检查/凭证三前置）修复即清零。"""
    src = _js()
    # 详情检查 Tab 汇总徽章：title 携带该级别全部 message 原文（esc 转义）
    assert "var msgs=function(l){return checks.filter(function(x){return (x.level||x[0])===l;})" in src
    assert "var wt=counts.WARN?' title=\"'+esc(msgs('WARN'))+'\"':''" in src
    assert "ft=counts.FAIL?' title=\"'+esc(msgs('FAIL'))+'\"':''" in src
    assert "'<span class=\"badge fail\"'+ft+'>FAIL '" in src
    # 队列卡检查摘要 chip：悬停指路（队列数据面无原文，原文在详情）
    assert "有 '+fail+' 项 FAIL——进详情「检查」Tab 看原文与修复命令" in src
    # 物料缺口角标：操作 Tab 按钮内计数徽章，三前置缺口客户端可算
    assert "function pubGaps(){" in src
    assert "if(!(d.meta&&d.meta.cover))n++;" in src
    assert "d.checks.some(function(x){return (x.level||x[0])==='FAIL';})" in src
    assert "if(state.credsOK===false)n++;" in src
    assert "var g=pubGaps(),gb=$('actions-gap');gb.hidden=!g;gb.textContent=g;" in src
    parsed = _structure()
    assert "actions-gap" in parsed.ids
    # 角标只标记不锁死（发布仍人工点击，红线）
    assert "PB.disabled=official.length===0" in src  # 既有门不变（无凭证=平台缺失）


def test_credentials_three_state_and_manual_verify() -> None:
    """建议7（沉淀·模式 24）：凭据三态（已配置/未配置/免凭据·本地）+手动
    「验证状态」按钮（无自动探测）+设置变更生效提示；key 永不回显——UI 层
    不渲染该字段（write-only 空输入 + 状态占位，非打码）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    parsed = _structure()
    assert "cred-verify-btn" in parsed.ids  # 手动验证按钮（设置页头）
    # 三态判定：has_key=已配置；本地端点（localhost/127.*）无 key=免凭据；其余=未配置
    assert "var KC={ok:'<span class=\"badge pass\">已配置</span>',miss:'<span class=\"badge warn\">未配置</span>',free:'<span class=\"badge queued\">免凭据·本地</span>'};" in src
    assert "var lk=p.has_key?'ok':(/localhost|127\\.0\\.0\\.1/i.test(p.base_url||'')?'free':'miss');" in src
    # 手动验证：重拉 /api/health + /api/settings，toast 汇总凭证/AI/图床/磁盘
    assert "$('cred-verify-btn').addEventListener('click'" in src
    assert "toast('验证完成：凭证 '" in src
    # 生效提示：KB 保存/ AI 档案保存成功 toast 携带生效时点
    assert "toast('KB 设置已保存——素材页下次采集起用新边界',OK)" in src
    assert "toast(msg+'（下次 AI 调用生效）',OK)" in src
    # 密钥不回显：编辑档案时空值不传（保留旧值）——write-only（F47 既有）+
    # 表格 key 列只渲染三态徽章，无任何 key 值/掩码串
    assert "$('ai-p-key').value='';" in src
    assert "已配置，输入以更换" in src
    assert "'•'" not in all_src and "****" not in all_src  # 无打码形态


def test_material_star_triage_and_three_tabs() -> None:
    """M3（P19 继承+升级）：收藏星标走 triage.starred（/api/materials/triage 窄门，
    替代档A /api/discover/star 微端点面）+「全部/收藏/未读」三 tab（tab 状态进 hash）。"""
    src = _js()
    parsed = _structure()
    # 三 tab 静态锚点（data-mtab 三档 + 收藏/未读计数；最近档随收编退役——分诊键是 triage 三键）
    assert {"all", "starred", "unread"} <= parsed.attrs["data-mtab"]
    assert {"mt-starred", "mt-unread"} <= set(parsed.ids)
    # 星标数据面：POST /api/materials/triage {id, starred, confirm:true}
    assert "postJSON('/api/materials/triage',Object.assign({id:id,confirm:true},changes))" in src
    assert "async function matTriage(id,changes){" in src
    # 渲染期分诊过滤（收藏=triage.starred；未读=!triage.read）
    assert "if(tab==='starred')L=L.filter(function(m){return (m.triage||{}).starred;});" in src
    assert "else if(tab==='unread')L=L.filter(function(m){return !(m.triage||{}).read;});" in src
    # tab 计数回填 + 选中态同步（renderMaterials 单点）
    assert "$('mt-starred').textContent=sn;" in src
    assert "$('mt-unread').textContent=ur;" in src
    assert "el.classList.toggle('is-active',el.getAttribute('data-mtab')===tab);" in src
    # 星标按钮：summary 首位（独立拼接段）+ preventDefault 防 details 折叠（P19 同纪律）
    assert "'<button class=\"btn btn-ghost btn-icon dstar'+(tri.starred?' on':'')+'\" data-mstar=\"" in src
    assert "if(!el)return;\ne.preventDefault();" in src
    # 收藏空态引导（本来就没有收藏 → 点星标引导，非清除筛选）
    assert "tab==='starred'?'暂无收藏'" in src
    assert "点素材行首星标按钮收藏后" in src


def test_material_density_three_levels() -> None:
    """M3：三档密度切换——紧凑列表（默认）/ 各源计数（组头+计数）/ 全文沉浸（全展开）。"""
    parsed = _structure()
    src = _js_css()
    # 三档静态锚点（data-mdens；紧凑为 is-active 默认档）
    assert {"compact", "src", "full"} <= parsed.attrs["data-mdens"]
    dens_buttons = [t for t in parsed.tags if t[0] == "button" and t[1].get("data-mdens") == "compact"]
    assert dens_buttons and "is-active" in (dens_buttons[0][1].get("class") or "")
    # 渲染：密度档写容器 class（d-compact/d-src/d-full）+ 沉浸强制 open + 各源分组组头计数
    assert "list.className='mat-list d-'+state.mdens;" in src
    assert "(state.mdens==='full'?' open':'')" in src
    assert "state.mdens==='src'?srcGroups(L):L.map(matRow).join('')" in src
    assert "'<h3 class=\"mat-src\">'+esc(names[k]||k)+'<span class=\"chip-count\">'+g[k].length" in src
    # 紧凑档 CSS：预览/四维条/引言/动作区折叠，展开（details[open]）恢复；沉浸档加内边距
    assert ".mat-list.d-compact .mat-row-prev{display:none}" in src
    assert ".mat-list.d-compact .mat-row-x .pbar,.mat-list.d-compact .mat-row-x .action-hint,.mat-list.d-compact .mat-row-x .mat-acts{display:none}" in src
    assert ".mat-list.d-compact details[open] .mat-row-x .pbar{display:grid}" in src
    assert ".mat-list.d-compact details[open] .mat-row-x .action-hint{display:block}" in src
    assert ".mat-list.d-compact details[open] .mat-row-x .mat-acts{display:flex}" in src
    assert ".mat-list.d-full .mat-row-x{padding:var(--space-md)}" in src
    # 密度进 hash（dens 非 compact 才带；P1 起五参数数组化拼接）+ hash 恢复白名单
    assert "if(state.mdens!=='compact')q.push('dens='+state.mdens);" in src
    assert "if(mdp==='compact'||mdp==='src'||mdp==='full')state.mdens=mdp;" in src


def test_material_triage_actions_and_two_step_delete() -> None:
    """M3 分诊动作：已读/禁用走 triage 窄门；删除两步 confirm+连坐声明（朱红=FAIL 专色纪律）。"""
    src = _js_css()
    parsed = _structure()
    # 已读/禁用动作（data-mread/data-moff → POST /api/materials/triage）
    assert "'<button class=\"btn\" data-mread=\"'+esc(m.id)+'\">'+(tri.read?'已读':'未读')" in src
    assert "</button><button class=\"btn\" data-moff=\"'+esc(m.id)+'\">'+(tri.enabled?'禁用':'启用')" in src
    assert "c[k]=!(m&&(m.triage||{})[k]);return c;" in src
    # 删除两步 confirm：第一步连坐声明（GET detail 取 cascade）+ 第二步终确认
    assert "getJSON('/api/materials/detail?id='+encodeURIComponent(id))" in src
    assert "casc=d.cascade||[];" in src
    assert "title:'删除素材 1/2 · 连坐声明'" in src
    assert "条衍生素材引用「'+title+'」，删除后引用处显「素材已删」徽章（不级联删）。" in src
    assert "title:'删除素材 2/2 · 终确认'" in src
    assert "墓碑落册后重采不复活。" in src
    assert "postJSON('/api/materials/delete',{id:id,confirm:true})" in src
    assert "await loadMaterials();renderMaterials();" in src
    # 删除按钮朱红系（--color-error=FAIL 专色；徽章系连坐提示同色语义）
    assert ".btn.mat-del{color:var(--crimson)}" in src
    # 空分诊键/非布尔值由服务层窄门拒绝（400 可读错误）
    assert "toast('分诊更新失败：'+err.message,ER);" in src


def test_settings_sources_block() -> None:
    """P1 源中枢（§3.1）：生态带四微 tile + 源总表（健康灯三态/订阅/素材未读/上次拉取/
    失败计数/启停）+ 数据面 GET /api/sources——设置「源连接」区块整体迁出至本视图。"""
    parsed = _structure()
    src = _shell_js_css()
    assert "view-sources" in parsed.ids
    assert {"eco-tiles", "src-rows", "src-filters", "src-empty", "src-verify-all-btn", "src-ingest-all-btn"} <= set(parsed.ids)
    assert "源连接" in src  # 设置页迁出说明（指向源中枢）
    # 数据面：GET /api/sources 权威行（与 settings.sources 同源同形，契约 §2.1-1）
    assert "async function loadSources(){" in src
    assert "getJSON('/api/sources')" in src
    # 生态带：启用源/今日新增/未读/失败源四微 tile（mats+health+jobs 前端聚合，零新后端）
    assert "function renderSources(){" in src
    assert "['启用源',en+'/'+list.length,'']" in src
    assert "['失败源',bad,bad?'amber':'']" in src
    # 健康灯三态（§四-13）：ok 竹绿实心 / down 绛 / 未探测空心灰 + title 载 last_ok
    assert "'<span class=\"health-dot '+st+'\"" in src
    assert ".health-dot.ok{background:var(--jade)" in src
    assert ".health-dot.down{background:var(--crimson)" in src
    assert ".health-dot.unknown{background:transparent;border:1.5px solid var(--ink-3)" in src
    # 失败计数：任务中心 ingest job 历史按 source_id 聚合（近 7 次窗口，零新后端）
    assert "function srcFailCounts(){" in src
    assert "if(seen[sid]<=7&&j.status==='failed')c[sid]=(c[sid]||0)+1;" in src
    assert "title=\"近 7 次拉取失败 '+fails[s.id]+' 次\"" in src
    # 每源行：订阅数 / 素材（未读徽章）/ 上次正常 / 启停拨杆 / 验证+刷新
    assert "((s.subscriptions||[]).length||'—')" in src
    assert "data-srcen=\"'+esc(s.id)+'\"'+(s.enabled?' checked':'')" in src
    assert "data-srcverify=\"'+esc(s.id)+'\">验证</button> <button class=\"btn\" data-srcrefresh=\"'+esc(s.id)+'\">刷新</button>" in src
    # 写契约：手动验证/启停（confirm:true；无自动轮询——诚实边界：只在手动验证或刷新回写）
    assert "postJSON('/api/sources/verify',{id:id,confirm:true})" in src
    assert "postJSON('/api/sources/enabled',{id:id,enabled:en,confirm:true})" in src
    assert "$('src-rows').addEventListener('click'" in src
    assert "$('src-rows').addEventListener('change'" in src
    assert "async function srcVerifyAll(){" in src  # 页头「验证全部」逐源探测

def test_ingest_job_and_refresh_entries() -> None:
    """M3 任务中心 ingest 行：JN 标签+jobTarget 源名；刷新全部（素材页头）/单步刷新此源（源行）。"""
    src = _js()
    parsed = _structure()
    assert "mat-ingest-btn" in parsed.ids  # 素材页头「刷新全部」
    assert "ingest:'源刷新'" in src  # JN 任务标签
    assert "(p.source_id?'源 '+p.source_id:(p.all?'全部源':''))" in src  # jobTarget 展示源名/全部源
    # 动作契约：POST /api/action/ingest {source_id|all:true, confirm:true} → job 轮询
    assert "postJSON('/api/action/ingest',sid?{source_id:sid,confirm:true}:{all:true,confirm:true})" in src
    assert "await queued(d.job_id);" in src
    assert "on2('mat-ingest-btn',function(){matIngest('');});" in src  # M3 批起 on2 点击绑定助手（等价 addEventListener）
    assert "$('src-ingest-all-btn').addEventListener('click',function(){matIngest('');});" in src  # 源中枢页头入口
    assert "if(r){matIngest(r.getAttribute('data-srcrefresh'));return;}" in src  # 源行「刷新」=单步刷新此源
    # 完成后素材面+源面重拉（新采集立即可见；源中枢在台前时同步刷新生态带）
    assert "await Promise.all([loadMaterials(),refreshSrcData()]);" in src
    assert "if(!$('view-sources').hidden)renderSources();" in src


def test_material_ai_summary_hook() -> None:
    """P3 素材「AI 摘要」真流（契约 §2.2-5）：preview 零落盘→可编辑预览卡→adopt 落 derived。

    沿革：M3 期为占位 toast（refine 归 M4）；P3 前端补完接 POST /api/action/refine
    两形态——{action:'preview'} 出预览卡，{action:'adopt', id, text} 存为素材；
    放弃不调任何 API（拒绝即消失不留痕红线）。
    """
    src = _js()
    assert "</button><button class=\"btn\" data-mai=\"'+esc(m.id)+'\">AI 摘要</button><button class=\"btn mat-del\"" in src
    assert "if(el.hasAttribute('data-mai'))return matPart('drawer').then(function(){matAI(id);});" in src  # UIX-3：matAI 落 drawer 子文件，列表首触经门装载
    # preview 真调用 + 预览卡（可编辑）+ adopt 回传编辑后文本
    assert "postJSON('/api/action/refine',{action:'preview',id:id,confirm:true})" in src
    assert 'id="refine-area"' in src
    assert "postJSON('/api/action/refine',{action:'adopt',id:id,text:$('refine-area').value,confirm:true})" in src
    # 放弃=零 API；预览零落盘文案（红线：AI 只起草，人工采纳才落库）
    assert "$('refine-drop').onclick=function(){paintMatDrawer();toast('已放弃（零落盘零痕迹）',OK);};" in src
    assert "拒绝即消失不留痕" in src


# ── P1 集成验收区段（视觉基础组 2026-11-16 交付）────────────────
# 以下断言按重构设计正典（§2.1 左导航轨 / §1.2 三态主题 / §2.2 hash 正典名）
# 编写，是集成阶段的机器验收单——当前构建中间态（GB 顶栏 Tab 方案 + 双名
# 文件）下预期 FAIL，集成工程师按偏差清单修正 shell/命名后转绿。
# 设计正典=docs/项目方案/musespiral-前端深度重构设计-2026-11-16.md。


def test_p1_shell_left_rail_navigation() -> None:
    """§2.1 app-shell：左侧导航轨（220↔56 折叠）替代顶栏 Tab；分组=供给→转化→出口→回看。"""
    parsed = _structure()
    src = _shell_css()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # app-shell 骨架：rail + 主区（minmax(0,1fr) 防内容压穿）
    assert {"app-shell", "rail", "rail-nav", "rail-fold"} <= set(parsed.ids)
    assert ".app-shell{display:grid;grid-template-columns:var(--rail-w) minmax(0,1fr)" in src
    assert "html[data-rail=\"min\"] .app-shell{grid-template-columns:var(--rail-w-min) minmax(0,1fr)}" in src
    # 四分组标题（内容生命线：发布是出口放低位）+ 底部钉住的设置
    for group in ("供给", "转化", "出口", "回看"):
        assert group in src, group
    assert '<div class="rail-foot">' in src
    # nav-item 组件：激活=朱砂左侧 2px 指示条 + 内凹通道（rail-nav 组件 §四-2）
    assert ".nav-item.is-active::before{content:\"\";position:absolute;left:0;top:6px;bottom:6px;width:2px" in src
    assert ".nav-item.is-active{" in src and "var(--vermilion)" in src
    # 分组标题 micro 级灰字
    assert ".rail-group-title{font-size:var(--fs-micro)" in src
    # 顶栏 Tab 方案退役（nav-tab 类与 .nav 顶栏容器删除）
    assert ".nav-tab" not in all_src
    # 徽标槽（数据接线归视图层）：源/素材/创作/发布四导航位
    for bid in ("nav-badge-sources", "nav-badge-material", "nav-badge-compose", "nav-badge-publish"):
        assert bid in parsed.ids, bid


def test_p1_theme_toggle_three_states() -> None:
    """§1.2 三态主题：顶栏切换器（sun/moon/auto 轮换）+ data-theme 写 html + localStorage 记忆。"""
    parsed = _structure()
    src = _js()
    # DOM：切换器 + 图标锚（三 icon：sun/moon/monitor，P1 新增 monoline 图标）
    assert {"theme-toggle", "theme-icon"} <= set(parsed.ids)
    for icon in ("sun", "moon", "monitor"):
        assert icon in src, icon
    # JS：三态轮换 + localStorage（裁决 §八-6 缺省 localStorage）+ 写 documentElement
    assert "localStorage" in src and "musespiral-theme" in src
    assert "document.documentElement.setAttribute('data-theme'" in src
    # 三态轮换顺序 light→dark→auto（终评 §三 D1：applyTheme 后 DOM 属性即真值）
    assert "var THEME_ORDER=['light','dark','auto'];" in src
    assert "$('theme-toggle').addEventListener('click',cycleTheme);" in src
    # 初始化读 localStorage 恢复（无记忆回 auto；先置 data-theme 再渲染不闪）
    assert "applyTheme(savedTheme||'auto');" in src
    # 切换先置 data-theme 再渲染（不闪）——applyTheme 在 init 首行调用
    assert "applyTheme(" in src


def test_d2_material_type_icons_dedicated() -> None:
    """终评 §三 P1-D2：7 素材类型各配专用 monoline 图标。

    修复前 MTYI 复用通用图标（image/video 同用 eye 零区分；radar_signal=tag /
    crawled=download / derived=edit）——类型是素材库第一视觉维度（§3.2），
    必须一型一形。MTYI 单点重映射，matRow 行渲染 / 血缘链 / ⌘K 三消费端同步生效。
    """
    src = _js()
    mtyi = re.search(r"var MTYI=\{[^}]*\}", src)
    assert mtyi is not None, "缺少 MTYI 类型→图标映射"
    mapping = mtyi.group(0)
    # 7 类型逐一映射专用图标（text 复用 docs=矩形+折角文档形）
    for typo, icon in (
        ("text", "docs"), ("image", "image"), ("video", "video"),
        ("link", "link"), ("radar_signal", "radar"),
        ("crawled", "globe"), ("derived", "derived"),
    ):
        assert f"{typo}:'{icon}'" in mapping, f"{typo} 未映射专用图标 {icon}"
    # 7 图标两两互异（image/video 修复点：不再同形）
    values = re.findall(r"(\w+):'(\w+)'", mapping)
    assert len({v for _, v in values}) == len(values), f"MTYI 存在同形图标: {values}"
    # matRow 行首类型槽经 MTYI 取 SVG（mty-ico 槽 + 未知类型回退 tag）
    assert "I(MTYI[m.type]||'tag')" in src
    # 6 枚新增 monoline 图标入库（path/circle 数据；text 的 docs 已在 ICON_NAMES）
    for icon in ("image", "video", "link", "radar", "globe", "derived"):
        assert icon in ICON_NAMES, icon
        assert re.search(
            rf"(?:^|[\s{{,])(?:'{icon}'|\"{icon}\"|{icon})\s*:\s*'<(?:path|circle)", src
        ), f"ICONS 缺 {icon} 的 path/circle 数据"


def test_d3_nav_active_state_toggle() -> None:
    """终评 §三 P1-D3：路由切换后对应导航项挂 is-active（朱砂左边条+背景微变的 JS 面）。

    CSS 面（.nav-item.is-active / ::before 左 2px 朱砂条）由
    test_p1_shell_left_rail_navigation 断言；此处断言行为链——
    showView→renderNav 按 data-nav 切 class，hashchange→renderRoute 驱动。
    """
    src = _js()
    # renderNav 按 data-nav 精确匹配切 is-active + aria-current=page（UIX-3 无障碍批）
    assert "el.classList.toggle('is-active',on);" in src
    assert "if(on)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');" in src
    # showView 收口调用 renderNav（视图显隐与激活态同步）
    assert "renderNav(view);" in src
    # 路由驱动：hashchange 重跑 renderRoute（切换视图即触发激活态更新）
    assert "window.addEventListener('hashchange',renderRoute);" in src


def test_p1_topbar_crumb_and_cp_entry() -> None:
    """§2.1 顶栏：视图标题 breadcrumb（随 showView 更新）+ ⌘K 命令面板入口按钮。"""
    parsed = _structure()
    src = _css()
    assert {"crumb-title", "cp-btn"} <= set(parsed.ids)
    assert ".crumb{" in src and ".crumb-title{" in src


def test_p1_view_file_names_canonical() -> None:
    """§2.2 hash 正典名统一：视图文件名/marker 与 hash 名一致（publish/insight，
    非 publishing/insights 双名）——消除「文件名 publishing ↔ id view-publish」解耦双轨。

    现状（GB 中间态）将双名文档化为解耦设计；集成裁决若保留双名，删除本测试
    并在 test_build_ui.py 的 view_id 映射处登记豁免依据。
    """
    studio = UI.parent / "ui.views"
    names = sorted(p.stem for p in studio.glob("*.html"))
    # R4 #books 批（2026-11-18）+1 视图文件；PL-3 批 +plugins +plugin-run；
    # ARCH-1 批（2026-11-21）+article（原 tmpl 硬编码骨架外移——十一视图源）
    assert names == ["article", "books", "compose", "design", "insight", "material",
                     "plugin-run", "plugins", "publish", "settings", "sources"], names


def test_p1_nav_badges_wired() -> None:
    """§2.1 徽标接线：四导航位数据回填 + §2.3 深链（修复「死标记」审查项）。

    四口径（数据面前端已持有，零新请求）：
      sources=失败源数（renderSources 的 bad：health down 或近 7 次拉取失败）
      material=未读素材数（renderMaterials 的 ur，全量口径非筛选后）
      compose=进行中会话数（清单载荷 status 字段——compose_service.list_sessions 已下发）
      publish=待审数（updateChipCounts 的 n.pending，与漏斗带同源）
    """
    src = _js()
    assert "function updateNavBadge(id,count){" in src
    assert "el.hidden=!(count>0);" in src  # >0 显数 / 0 隐藏
    for call in (
        "updateNavBadge('nav-badge-sources',bad)",
        "updateNavBadge('nav-badge-material',ur)",
        "updateNavBadge('nav-badge-compose',act)",
        "updateNavBadge('nav-badge-publish',n.pending)",
    ):
        assert call in src, call
    # compose 口径锚定：清单项 status（区别于详情 d.status 的既有字面量）
    assert "s.status==='composing'" in src
    # §2.3 深链：参数走 §2.2 白名单；sources 无 tab 消费端先落视图直达
    for href in ("'#material?tab=unread'", "'#publish?filter=pending'", "'#sources'"):
        assert href in src, href


# ── P3/P2/P4 前端补完验收区段（2026-11-16：refine 真流 + 块结构操作 + 源注册/抽屉
#    + 右键菜单三处 + cmdk 前缀 + g 序列 + 排期周历；设计正典 §五/§六）──


def test_p3_block_structure_ops() -> None:
    """P3 块结构操作：块头部 前插/后插/拆分/合并（data-cmop 单分派）；本地暂存+decide 持久化。"""
    src = _js()
    # 工作副本：轮内暂存（切轮/再生成即弃），本地块带 loc 徽标；
    # 缓存键=slug+轮号（2026-11-20 复核 HIGH 修复——只比轮号跨会话串台）
    assert "state.cmWork={slug:state.cslug,round:state.cround,locN:0,seq:0,list:" in src
    assert "state.cmWork.slug===state.cslug&&state.cmWork.round===state.cround" in src
    assert "b.loc?'<span class=\"badge warn\">本地块</span>'" in src
    # 四操作经统一 data-cmop 分派（op 码 + 块 id 两段）
    for frag in (
        'data-cmop="i0 \'', 'data-cmop="i1 \'', 'data-cmop="sp \'', 'data-cmop="mg \'',
        "if(pv[0]==='i0')cmIns(bid,0);", "else if(pv[0]==='sp')cmSplit(bid);",
    ):
        assert frag in src, frag
    # 持久化走既有 decide：拆分上半 adopt_edited / 合并全文 adopt_edited + 下块 discard
    assert "cmDecide(id,'adopt_edited',{text:b.text});" in src
    assert "cmDecide(n.id,'discard',{direction:'已并入上块'});" in src
    # 结构面参数不进 compose 端点（契约 §2.3-6）——本地块提示 + 切轮重置
    assert "state.cround=+c.getAttribute('data-cm-round');state.cmWork=null;" in src
    assert "$('cm-generate-btn').addEventListener('click',function(){state.cmWork=null;cmGenerate(this);});" in src


def test_p2_source_register_form_and_drawer() -> None:
    """P2 源注册表单（类型三选→参数域联动→POST /api/sources）+ 源详情抽屉（行点击展开）。"""
    parsed = _structure()
    assert {"src-add-btn", "src-form", "sf-type", "sf-id", "sf-base", "sf-subs", "sf-root", "sf-path", "sf-ok"} <= set(parsed.ids)
    assert {"src-drawer", "src-drawer-title", "src-drawer-body", "src-drawer-close"} <= set(parsed.ids)
    src = _js()
    # 参数域联动：radar_hub→base+订阅 / kb→root / dir→path
    assert "$('sf-base').hidden=$('sf-subs').hidden=t!=='radar_hub';" in src
    # B4a 扩：rss 第四型（url 域联动 + K-map 注册）
    assert "var K={radar_hub:['base','sf-base'],kb:['root','sf-root'],dir:['path','sf-path'],rss:['url','sf-url']}[t];" in src
    assert "$('sf-url').hidden=t!=='rss';" in src
    # 写契约：POST /api/sources {id,type,config,subscriptions,confirm:true}
    assert "postJSON('/api/sources',{id:id,type:t,config:cfg,subscriptions:subs,confirm:true})" in src
    # 抽屉：订阅列表 + 启停/验证（同源委托属性）+ 两步删除降级（契约无删除端点→禁用下线）
    assert "data-srcverify=\"'+esc(s.id)+'\">验证</button>" in src
    assert "postJSON('/api/sources/enabled',{id:id,enabled:false,confirm:true});await refreshSrcData();refreshSourcesView();paintSrcDrawer(id);" in src
    assert "renderSources();paintSrcDrawer(state.ssrc);" in src


def test_p4_context_menus_cmdk_prefix_gseq_plan() -> None:
    """P4 交互补全：右键菜单三处（同源 data 属性复用委托处理器）+ cmdk >/@/# 前缀 + g 序列 + 周历。"""
    parsed = _structure()
    assert "ctx" in parsed.ids
    src = _js_css()
    # 右键菜单：三处 contextmenu + 菜单项复用行内按钮的 data 属性（同源同函数）。
    # A 类专项（2026-11-20）：#ctx 委托改由各视图块自挂（matActsClick 是 bindMaterial
    # 局部函数，core 直呼即 ReferenceError；data-m* 与 data-cm* 两族属性互斥）
    assert "$('ctx').addEventListener('click',matActsClick);" in src
    assert "$('ctx').addEventListener('click',cmBlocksClick);" in src
    for frag in (
        "$('mat-list').addEventListener('contextmenu'",
        "$('view-publish').addEventListener('contextmenu'",
        "$('cm-blocks').addEventListener('contextmenu'",
    ):
        assert frag in src, frag
    assert "data-mdel=\"'+id+'\">删除</button>'" in src  # 危险项 .dg 绛色
    assert ".cp-item.dg{color:var(--crimson)}" in src
    # cmdk v2 前缀：> 命令 / @ 文章 / # 素材（本地 state 线性扫，Enter=开素材抽屉）
    assert "var pre=q.charAt(0);" in src
    assert "if(pre==='>'||pre==='@'||pre==='#')q=q.slice(1);" in src
    assert "location.hash='#material?m='+encodeURIComponent(m.id);" in src
    # g 序列：首键 g 600ms 内待次键；g s/m/c/p/i + g ,
    assert "var gSeq=0,GK={s:'sources',m:'material',b:'books',c:'compose',p:'publish',i:'insight'};" in src
    assert "var gh=e.key===','?'settings':GK[e.key];" in src
    assert "setTimeout(function(){gSeq=0;},600);" in src
    # 排期周历：GET /api/plan → 三态着色（计划/已交付/已发布 复用 badge 三色）
    assert "getJSON('/api/plan')" in src
    assert "replace(/计划:/,'<b class=\"badge warn\">计划</b>:')" in src
    assert "replace(/已发布:/,'<b class=\"badge pass\">已发布</b>:')" in src


# ── Phase C F71 封面工坊（2026-11-17）：/api/visual 族前端接线 ──────────────


def test_cover_studio_visual_render_two_phase() -> None:
    """F71 真预览两段式：编辑 debounce 300ms → skel 占位 → SVG inline 注入。

    面板全新建（前端此前零 /api/action/cover 调用=无模拟预览可换调）；render 带
    article 由服务端自动取材，merged params 首次回填表单；error 态保留上次稿不闪空。
    """
    parsed = _structure()
    assert {"cv-tpl", "cv-title", "cv-hook", "cv-fs", "cv-al", "cv-prev", "cv-x", "cv-pt"} <= set(parsed.ids)
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 换调 /api/visual/render（真 SVG，与导出同源）；/api/action/cover 保留为 CLI 对齐面
    # 评审补批 P1-3：面板搬入 #compose 后取材改 CV.art（compose=会话正稿；空值纯参数渲染）
    assert (
        "postJSON('/api/visual/render',{confirm:true,template:CV.tpl,params:cvP(),spec:'mp',article:CV.art||null})"
        in src
    )
    assert "'/api/action/cover'" not in all_src
    assert "setTimeout(cvGo,300)" in src  # debounce 300ms（F71 步 2）
    assert '<div class="cv-skel"></div>' in src  # rendering(skel) 占位
    assert "$('cv-prev').innerHTML=d.svg;" in src  # SVG inline 注入预览容器
    assert "if(CV.svg)$('cv-prev').innerHTML=CV.svg;" in src  # error 保留上次稿
    assert "if(!$('cv-title').value){" in src  # 首渲 merged params 表单预填
    # 模板 chips（内置双色）+ 取材惰性初始化（防刷新丢编辑态；P1-3 后由 renderCompose 驱动）
    assert "['vermilion-slab','朱砂拓'],['indigo-slab','青黛拓']" in src
    assert "if(d.final_article&&CV.art!==d.final_article)cvInit(d.final_article);" in src


def test_cover_studio_export_save_to_job_chain() -> None:
    """F71 步 4 导出链：save（版本池先行落盘）→ export（visual_export job）→
    800ms 轮询（复用 waitForJob）→ done toast「cover.png 900×383」→ 队列封面刷新。"""
    src = _js()
    assert (
        "postJSON('/api/visual/save',{confirm:true,article:CV.art,"
        "kind:'cover',template:CV.tpl,params:cvP(),spec:'mp'})" in src
    )
    assert "postJSON('/api/visual/export',{confirm:true,article:CV.art,kind:'cover'})" in src
    assert "var j=await waitForJob(d.job_id),r=j.result||{};" in src
    assert "j.status==='failed'||r.ok===false" in src  # B/C 路降级失败同门
    assert "r.size[0]+'×'+r.size[1]" in src  # done toast「cover.png 900×383」
    assert "await refreshArticleState();" in src  # 步 5：#publish 队列封面即刷新


def test_cover_studio_preset_modal_and_grouping() -> None:
    """F74 步 4-5：「存为我的模板」modal（id kebab 输入+label）→ POST preset；
    模板 chips 增「我的模板」分组（GET /api/visual/presets，kind=cover 过滤）。"""
    parsed = _structure()
    assert {"cv-pm", "cv-pid", "cv-pl", "cv-pm-ok", "cv-pm-x"} <= set(parsed.ids)
    src = _shell_js()
    assert "class=\"modal-overlay\" id=\"cv-pm\" hidden" in src
    assert "id=\"cv-pid\"" in src and "id=\"cv-pl\"" in src
    assert "/^[a-z0-9]+(-[a-z0-9]+)*$/.test(id)" in src  # kebab 前置校验
    assert (
        "postJSON('/api/visual/preset',{confirm:true,id:id,"
        "label:$('cv-pl').value.trim()||id,template:CV.tpl,params:cvP(),kind:'cover'})" in src
    )
    assert "'<span class=\"cv-grp\">我的模板</span>'" in src  # chips 分组标记
    assert "getJSON('/api/visual/presets')" in src  # 分组数据面（init + 保存后重拉）
    assert "p.kind==='cover'&&p.template" in src  # 分组过滤：仅封面预设且模板有效
    # 保存成功后重拉分组；预设 chips 应用=切模板起点（可再改不锁定）
    assert "CV.pr=(await getJSON('/api/visual/presets')).items||[];cvChips();" in src
    assert "data-cvt=" in src


# ── Phase C F72 社媒图编辑器（#design 视图，2026-11-17 批）──────────────


def test_design_view_structure_and_stage() -> None:
    """F72 编辑器视图结构：#design section + svg-stage 舞台 + 交互态类名 + 状态机面。

    组件规格（C 档 §二 F72）：design-editor 视图容器（状态机 idle→loading→ready→
    dirty→saving|exporting，error 走 toast）/ svg-stage（letterboxing 容器 contain
    适配视口，交互态 none/hover/dragging，选中=--indigo 描边覆盖层）/ token-color-chips /
    spec-chips / typo-chips / undo-hud。
    """
    parsed = _structure()
    assert "view-design" in parsed.ids
    assert "design" in parsed.attrs["data-view"]
    # 舞台与三态占位（skel/empty/stage）
    assert {"dv-stage", "dv-skel", "dv-wrap"} <= set(parsed.ids)
    # 顶栏：文章选择器 + 载入/存稿/导出 + dirty 圆点
    assert {"dv-art", "dv-load-btn", "dv-save-btn", "dv-export-btn", "dv-dirty"} <= set(parsed.ids)
    # 侧栏 chip 组：spec-chips 三选 / token-color-chips / typo-chips 字号三档对齐三态
    assert {"dv-specs", "dv-colors", "dv-fs", "dv-al"} <= set(parsed.ids)
    assert {"xhs-3x4", "xhs-4x5", "square"} <= parsed.attrs.get("data-dspec", set())
    assert {"S", "M", "L"} <= parsed.attrs.get("data-dfs", set())
    assert {"left", "center", "right"} <= parsed.attrs.get("data-dal", set())
    # 层级前后移 + 撤销 HUD
    assert {"dv-fwd", "dv-bwd", "dv-undo"} <= set(parsed.ids)
    # 双击行内文本输入 + 切换两步确认 + 导出弹层（当前/全部 × A/B 路线）
    assert {"dv-tin", "dv-sw", "dv-ex"} <= set(parsed.ids)
    assert {"cur", "all"} <= parsed.attrs.get("data-exspec", set())
    assert {"a", "b"} <= parsed.attrs.get("data-exroute", set())
    src = _js_css()
    # 舞台 letterboxing（contain 适配：max-width/max-height 双夹）与交互态类名
    assert ".dv-stage svg{display:block;width:auto;height:auto;max-width:100%;max-height:72vh" in src
    assert ".dv-stage [data-o]:hover" in src and ".dv-drag" in src
    # 选中描边 --indigo 2px（.dv-hd 覆盖层，vector-effect 保屏幕恒宽）
    assert ".dv-hd rect{fill:none;stroke:var(--indigo);stroke-width:2}" in src
    assert 'vector-effect="non-scaling-stroke"' in src
    # 状态机：dvSet 五态齐全（error 态走 toast 不落视图态）
    for st in ("idle", "loading", "ready", "saving", "exporting"):
        assert f"dvSet('{st}')" in src, st
    # dirty 圆点/存稿/导出按钮使能联动
    assert "$('dv-dirty').hidden=!DV.dirty;" in src
    assert "$('dv-save-btn').disabled=!DV.dirty||DV.st!=='ready';" in src


def test_design_nav_seven_views_and_routing() -> None:
    """F72 载体裁决落地：七视图（导航「转化」组素材库之后加「视觉编辑」）+ hash
    白名单 +#design + ⌘1-7 同步 + 命令面板跳转同步。"""
    parsed = _structure()
    assert {"sources", "material", "compose", "design", "publish", "insight", "settings"} <= parsed.attrs["data-nav"]
    src = _shell_js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "var VIEWS=['sources','material','books','compose','design','publish','insight','article','settings','plugins','plugin-run'];" in src
    assert '<a class="nav-item" data-nav="design" href="#design">' in src
    assert '<span class="nav-label">视觉编辑</span>' in src
    assert "var DNAV=['sources','material','design','compose','publish','insight','settings'];" in src
    # ⌘1-7 七视图快捷键——全局分域分支（深构 §5.1/§八-4：视图外管导航、
    # #article 内 ⌘1-4 管 tab 彼视图优先；2026-11-17 集成轮自 design 域修为全局；
    # A 类专项后 setTab 经 VAPI.article 注册表，core 不直呼视图函数；
    # 2026-11-22 复核：装载窗口内 ⌘1-4 消费 no-op 不回落 DNAV）
    assert "if((e.metaKey||e.ctrlKey)&&e.key>='1'&&e.key<='7'){" in src
    assert "if(!$('view-article').hidden&&+e.key<=4){if(a1&&a1.setTab)return a1.setTab(['edit','variants','checks','preview'][+e.key-1],true);return;}" in src
    assert "location.hash='#'+DNAV[+e.key-1];return;" in src
    # 防回退：design 域限定的旧实现（view-design 门后导航 → 其余 6 视图失效）不得复现
    assert "if(m&&e.key>='1'&&e.key<='7'){e.preventDefault();location.hash='#'+DNAV[+e.key-1];return;}" not in all_src
    # 路由白名单：#design 进 renderRoute 分发（VIEWS 白名单回落语义复用；A 类专项
    # 后 design 入口经 VAPI 注册表——分支体归视图块）
    assert "VAPI.design={enter:dvEnter};" in src
    assert "CRUMBS={sources:'源中枢',material:'素材库',books:'书籍',compose:'创作工坊',design:'视觉编辑'" in src
    assert "['design','视觉编辑','image']" in src  # ⌘K 跳转同步


def test_design_editor_data_flow() -> None:
    """F72 数据面：GET design 载入（无稿 404→xhs-card 缺省稿 render）→ spec 切换
    非拉伸重渲（未存修改两步确认：丢弃/先存稿）→ 色槽/排版 chips 防抖 300ms render
    往返 → 存稿（svg 随稿白名单复验）→ 导出 A 前端光栅/B job 轮询（恒 PNG）。"""
    src = _js()
    assert "getJSON('/api/visual/design?article='+encodeURIComponent(path))" in src
    assert "/无设计稿|HTTP 404/.test(err.message)" in src
    assert "postJSON('/api/visual/render',{confirm:true,template:'xhs-card',spec:DV.spec,article:path})" in src
    # spec 切换：同 params 新 spec 重渲；未存修改先两步确认
    assert "if(DV.dirty){DV.pendSpec=spec;$('dv-sw').hidden=false;return;}" in src
    assert "dvApplySpec(s);" in src and "DV.dpos={};DV.order=null;DV.sel=-1;DV.spec=spec;" in src
    # chips 防抖 300ms render 往返（typo/色槽视图级统一委托→dvGo）
    assert "DV.h=setTimeout(dvRenderNow,300);" in src
    # 存稿：design.json+params+svg（手工位移/层级随 svg 进稿，服务端白名单复验）
    assert (
        "postJSON('/api/visual/save',{confirm:true,article:DV.art,kind:DV.kind,"
        "template:DV.tpl,params:DV.params,spec:DV.spec,svg:DV.svg})" in src
    )
    # 导出：A 路画布光栅（dataURI 天然离线）/ B 路 job 轮询；B 路未存修改自动先存稿
    assert "'image/png'" in src
    assert "if(DV.dirty)await dvSave();" in src
    assert "postJSON('/api/visual/export',{confirm:true,article:DV.art,kind:DV.kind})" in src
    # 点选/拖动：SVG DOM 原生事件零命中检测；8px 网格吸附公式逐字
    assert "Math.round(dx/k/8)*8,gy=Math.round(dy/k/8)*8" in src
    assert "e.target.closest('[data-o]')" in src
    # 层级前后移：DV.order 重排（doc.elements 顺序的会话内重排）
    assert "o.splice(to,0,o.splice(at,1)[0]);" in src
    # 零依赖红线：拖动全走原生 pointer 事件（无任何画布库引用，test_ui_has_no_third_party_libraries 同门）
    assert "addEventListener('pointerdown'" in src
    assert "addEventListener('pointermove'" in src
    # 撤销失效在途防抖渲染（undo 后旧渲染不得覆盖恢复态）
    assert "DV.q++;" in src


def test_design_undo_stack_boundary_marks() -> None:
    """F72 撤销栈：内存快照 N=50（⌘Z/⇧⌘Z 编辑器域内生效；刷新即弃——存稿才是
    持久化边界）。F72-T3（连续 51 步栈深恒 50）为 e2e 冒烟标记用例，见
    tests/e2e/test_studio_e2e.py（playwright 缺席整目录 skip，不进 CI）。
    """
    src = _js()
    assert "if(DV.undo.length>50)DV.undo.shift();" in src
    assert "e.shiftKey?dvTravel(0):dvTravel(1);return;}" in src
    assert "JSON.stringify(DV.dpos)" in src  # 快照含位移（与层级 order 同存）
    assert "$('dv-undo').textContent='撤销 '+DV.undo.length+' 步 · ⌘Z/⇧⌘Z';" in src


def test_d6_material_row_more_button() -> None:
    """终评 §三 P2-D6 右键暗示（方案 A「安静但不隐藏」）：素材行 hover 右上角 ⋯
    按钮，点击弹同右键菜单（matMenu 单一实现——contextmenu 与 ⋯ 点击逐字节同源，
    危险项恒 .dg 绛色；CSS hover 显隐，零新增 JS 监听）。"""
    src = _js_css()
    assert 'class="btn btn-ghost btn-icon mat-more" data-mmore=' in src
    assert "function matMenu(id){" in src
    assert "ctxShow(e,matMenu(el.getAttribute('data-mmore')));" in src  # ⋯ 点击
    assert "ctxShow(e,matMenu(id));" in src  # 行右键
    assert ".mat-row-sum:hover .mat-more{display:inline-flex}" in src
    assert "if(el.hasAttribute('data-mmore'))return" in src  # 委托分支


# ── 评审补批（2026-11-17，《musespiral-界面功能交互整体评审》§五 七项）──────────────


def test_review_batch_p23_theme_indicator_three_states() -> None:
    """P2-3 主题态指示：三态各自文案 + auto 态解析系统偏好写「当前：深色/浅色」。

    修复前 title 只写所选档（「跟随系统」看不出系统实际给了深色还是浅色），
    且无 aria-pressed——按 review §三 P2-3 双补：动态 title + aria-pressed
    （pressed=true 即当前渲染为深色）。
    """
    src = _shell_js()
    # auto 态用 matchMedia 解析系统偏好，title 展开为「跟随系统（当前：深色|浅色）」
    assert "matchMedia('(prefers-color-scheme: dark)').matches" in src
    assert "'跟随系统（当前：'+(dk?'深色':'浅色')+'）'" in src
    # 三态文案同源：light/dark 直书档名（dk 布尔由 mode==='dark' 或 auto+系统深色得出）
    assert "var dk=mode==='dark'||(mode==='auto'&&window.matchMedia" in src
    assert "(dk?'深色':'浅色'))+'（点击切换）'" in src
    # aria-pressed 跟随解析后的实际明暗（非所选档）
    assert "b.setAttribute('aria-pressed',dk?'true':'false');" in src
    # 静态初始 title 仍是 auto 缺省（JS applyTheme 在 init 首行覆盖为动态文案）
    assert 'title="主题：跟随系统（点击切换）"' in src


def test_review_batch_p11_hit_area_expansion() -> None:
    """P1-1 触控热区：实测 <28px 的可点目标以 ::after 透明外扩（零视觉改动）。

    实测清单（playwright 逐视图量测，非评审原文的「16px 图标按钮」——该族
    .btn-icon 实测 33px 高本就达标）：set-table 行内按钮 ~24px / dv-colors
    色槽 32×26 / job-ops·cm-block-ops·toast-action 微按钮 ~26px / cf-dot 15px。
    ::after 无背景无边框无位移——像素层不可见，纯热区扩展。
    """
    src = _css()
    assert (
        ".set-table .btn,.dv-colors .chip,.job-ops .btn,.cm-block-ops .btn,"
        "#toast-action,.cf-dot{position:relative}" in src
    )
    assert (
        ".set-table .btn::after,.dv-colors .chip::after,.job-ops .btn::after,"
        ".cm-block-ops .btn::after,#toast-action::after{content:\"\";position:absolute;inset:-4px}" in src
    )
    # inset 自 padding box 起算、边框吃进外扩：2px 边框族补 -5px（色槽净外扩 3px）
    assert ".dv-colors .chip::after{inset:-5px}" in src
    # cf-dot 15px 色点 -6px（2px 边框净外扩 4px；8px 列距两侧恰好相接不重叠，
    # 受集群间距约束达 23px——记录性部分达标，见评审补批报告）
    assert '.cf-dot::after{content:"";position:absolute;inset:-6px}' in src
    # 零视觉红线：两条热区规则为逐字断言（上方 in 检查即规则全文）——
    # 规则体只含 content/position/inset，无 background/border/transform 字节
    hotzone = '.set-table .btn::after,.dv-colors .chip::after,.job-ops .btn::after,.cm-block-ops .btn::after,#toast-action::after{content:"";position:absolute;inset:-4px}'
    for frag in ("background", "border", "transform", "margin", "padding"):
        assert frag not in hotzone


def test_review_batch_p22_stat_line_matches_fb_seg() -> None:
    """P2-2 统计双份口径统一：保留 fb-seg/筛选芯片作筛选器，统计行四段数字与
    count-draft/pending/reviewed/published 芯片逐一对应（同 pubCounts 单源）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 统计行与芯片同读 pubCounts()（updateChipCounts 内同一次聚合）
    assert "function updateChipCounts(){" in src
    assert "var n=pubCounts();" in src
    line = "'共 '+n.all+' 篇 · 草稿 '+n.draft+' · 待审 '+n.pending+' · 已审 '+n.reviewed+' · 已发布 '+n.published"
    assert line in src
    # 四段与四个 count-* 芯片同变量同名（draft/pending/reviewed/published 逐一可对）
    for seg, chip in (("n.draft", "count-draft"), ("n.pending", "count-pending"),
                      ("n.reviewed", "count-reviewed"), ("n.published", "count-published")):
        assert f"$('{chip}').textContent={seg};" in src, chip
    # 「有问题」保留为正交筛选芯片（n.fail 只喂 count-fail，不进统计行）
    assert "$('count-fail').textContent=n.fail;" in src
    assert "· 有问题 '+n.fail" not in all_src


def test_review_batch_p13_compose_visual_fold() -> None:
    """P1-3 创作台视觉折叠区：封面工坊自 #article·actions **搬运**（非复制）入
    #compose 右栏 details#cm-visual 折叠区——cv-* id 全族单实例迁址，绑定不动。"""
    parsed = _structure()
    assert "cm-visual" in parsed.ids
    src = _shell_js()
    # 迁址落位：cv 面板 id 全族必须在 #view-compose <section> 段内、且不在
    # #panel-actions（article 详情操作 tab）至其 </section> 段内——下标序证
    # 搬运（id 唯一，getElementById 绑定零改动；构建产物无 BUILD 标记）
    cm = src.index('id="view-compose"')
    cm_end = src.index("</section>", cm)
    pa = src.index('id="panel-actions"')
    pa_end = src.index("</section>", pa)
    assert src.index('id="cm-visual"') > cm
    for cv_id in ("cv-tpl", "cv-title", "cv-hook", "cv-fs", "cv-al", "cv-prev", "cv-x", "cv-pt", "cv-pm"):
        pos = src.index(f'id="{cv_id}"')
        assert cm < pos < cm_end, f"{cv_id} 不在 #view-compose 内"
        assert not (pa < pos < pa_end), f"{cv_id} 仍在 #panel-actions 内（未搬运）"
    # 折叠语义：details 缺省收起 + summary 复用 health-sum 折叠行样式（health-card 同款）
    assert '<details class="action-group" id="cm-visual">' in src
    assert '<summary class="health-sum">视觉设计 · 封面工坊</summary>' in src
    # 数据面：取材=会话正稿（compose 服务 final_article；无正稿纯参数渲染）
    assert "if(d.final_article&&CV.art!==d.final_article)cvInit(d.final_article);" in src
    assert "article:CV.art||null" in src
    assert "CV.art=path||'';" in src


def test_review_batch_p14_insight_zero_state_guide() -> None:
    """P1-4 洞察零态引导：发布数=0 时瓷砖区换引导卡（文案+跳发布中心按钮）；
    非零态保持原四砖。两态同函数内分支，数字与文案锚点齐断言。"""
    src = _js_css()
    # 零态门：total_publish 为 0/缺省 → 引导卡
    assert "var st=state.stats||{},pub=st.total_publish||0;" in src
    assert "if(!pub)$('stat-tiles').innerHTML=" in src
    # 引导卡三要素：文案 + 跳转按钮（a.btn 原生 hash 导航零 JS）+ empty 容器类
    assert "发布第一篇后，此处显示漏斗与趋势" in src
    assert '<a class="btn btn-primary empty-action" href="#publish">去发布中心</a>' in src
    # 非零态：原四砖模板保留（else 分支同一 innerHTML 赋值）
    assert "else $('stat-tiles').innerHTML=[['已发布',pub]," in src
    # 布局：引导卡横跨四砖位（.tiles .empty 网格跨列）
    assert ".tiles .empty{grid-column:1/-1}" in src


def test_review_batch_p21_insight_subtitle_plain_language() -> None:
    """P2-1 副标题分层：洞察 .view-sub 去除数据源技术路径，只留人话叙事。"""
    src = _source()
    assert "什么在变好：创意漏斗 · 源贡献 · 创作节奏 · AI 用量" in src
    for gone in ("数据源：", "materials / articles", "publish_log / jobs"):
        assert gone not in src, gone


# ── Phase C/D 入口 C：洞察「选题雷达」卡（设计 §五；纯只读，2026-11-17）──


def _view_section(name: str) -> str:
    """取某视图 <section> 整段（结构位置断言用——顺序依赖父容器内的出现序）。"""
    src = _source()
    start = src.index(f'<section id="view-{name}"')
    return src[start:src.index("</section>", start)]


def test_radar_card_structure_and_position() -> None:
    """#radar-card 在洞察视图内，且位于瓷砖行之前（设计 §5.1：顶部第一决策）。

    卡头三件套：标题「选题雷达」+ [重新生成] btn-ghost + body 容器；
    卡容器复用 .action-group（洞察七卡同族，零新视觉语言）。
    """
    parsed = _structure()
    assert {"radar-card", "radar-body", "radar-refresh"} <= set(parsed.ids)
    sec = _view_section("insight")
    i_card, i_tiles = sec.index('id="radar-card"'), sec.index('id="stat-tiles"')
    assert i_card < i_tiles, "#radar-card 必须在瓷砖行之前"
    assert '<div class="action-group" id="radar-card">' in sec
    assert '<h3 class="action-title">选题雷达</h3>' in sec
    assert '<button class="btn btn-ghost" id="radar-refresh"' in sec
    assert sec.index('id="radar-refresh"') < sec.index('id="radar-body"')


def test_radar_rows_three_kind_colors() -> None:
    """kind 徽三色（设计 §5.2）：radar=--viz-1 青黛 / kb=--viz-5 石板 / star=--jade 竹绿。

    徽章复用 .badge（内联 color 由 JS 按 kind 取 token），零新徽章类；
    score 条复用 .dc-bar 三段（hi/mid/lo 阈值 80/60，与素材库同口径）。
    """
    src = _js_css()
    assert "var RK={radar:'var(--viz-1)',kb:'var(--viz-5)',star:'var(--jade)'};" in src
    assert "'<div class=\"radar-row\"><span class=\"badge\" style=\"color:'" in src
    # dc-bar 三段语义（高/中/低）与既有素材卡同阈值
    assert "(s>79?'hi':s>59?'mid':'lo')" in src
    assert '<i style="width:' in src and "Math.min(s,100)" in src
    # 标题单行截断走 [data-t]（CSS 规则不在产物里断言字面量，断类名规则存在）
    assert ".radar-row [data-t]{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" in src
    # score 条定宽（真浏览器审计踩过：缺此规则条宽为 0）
    assert ".radar-row .dc-bar{flex:none;width:56px}" in src
    # reasons 首条 + 折叠更多（原生 details，零 JS 状态）
    assert "esc(q[0])" in src
    assert "'<details><summary>+'+(q.length-1)+'</summary>'" in src


def test_radar_ai_angle_indigo_only() -> None:
    """硬纪律①：AI 角度行用 --indigo 左边线 2px + 斜体；卡内零 --vermilion/--crimson。

    青黛是 AI 标记唯一色；朱砂只给人工动作按钮——雷达卡纯只读、无人工动作按钮，
    故整卡不得出现朱砂系样式（机械可核）。
    """
    src = _js_css()
    assert "c.ai_angle?'<em>AI 角度：'+esc(c.ai_angle)+'</em>':''" in src
    assert ".radar-row em{flex-basis:100%;border-left:2px solid var(--indigo)" in src
    assert "font-style:italic" in src
    # AI 角度行必须挂 --fs-caption（继承自 .radar-row 的 font-size）
    assert "font-size:var(--fs-caption)" in src
    # 卡内零朱砂/绛（雷达行与卡体渲染函数体内不出现这两个 token）
    a = src.index("function rr(c){")
    b = src.index("function renderRadar(){")
    card = src[a:b]
    for banned in ("var(--vermilion", "var(--crimson", "btn-primary"):
        assert banned not in card, f"雷达卡内出现人工动作色/主按钮: {banned}"


def test_radar_excluded_group_folded() -> None:
    """excluded 组：<details> 折叠 + 灰化（--ink-3 装饰线）+ 含 matched_title/similarity。"""
    src = _js_css()
    assert "'<details id=\"radar-ex\"><summary class=\"action-hint\">已写过排除 '" in src
    assert "相似度 ≥0.6" in src
    # 被剔行携带 matched_title 与 similarity（原始快照形状，前端不加工语义）
    assert "esc(e.matched_title)" in src
    assert "Math.round(e.similarity*100)/100" in src
    # 灰化走 --ink-3 装饰线（不是文本色——ink-3 文本仅 3:1 不达 AA）
    assert "#radar-ex{border-left:2px solid var(--ink-3)" in src


def test_radar_degraded_down_sources_amber() -> None:
    """降级态：inputs.down_sources 非空 → 卡头 --amber「外部腿缺失」，其余候选照常渲染。

    注意：--amber 是注意色（不是 --crimson 错误色）；本卡无重试语义（纯只读无任务态）。
    """
    src = _js()
    assert "d=sn.inputs.down_sources||[];" in src
    assert "外部腿缺失（" in src
    assert 'style="color:var(--amber)"' in src
    # 降级不阻断候选渲染（down_sources 只影响卡头注记，不 return）
    a = src.index("function renderRadar(){")
    b = src.index("function renderIdeaFunnel(){")
    body = src[a:b]
    i_dn = body.index("down_sources")
    i_rows = body.index(".slice(0,8).map(rr)")
    assert i_dn < i_rows, "down_sources 注记在候选渲染之前，但不得提前 return"


def test_radar_empty_state_guides_next_step() -> None:
    """空态：snapshot=null → .empty「尚无选题快照」+ 具体下一步 + [立即生成]（只弹引导 toast）。"""
    src = _shell_js()
    assert "尚无选题快照" in src
    assert "周一 08:20 自动生成；或运行 musespiral topic-radar 立即生成。" in src
    assert '<button class="btn empty-action" type="button" id="radar-gen">立即生成</button>' in src
    # 空态组件复用 .empty 族三件套
    assert '<div class="empty"><div class="empty-title">' in src
    assert '<div class="empty-text">' in src
    # 空态判定是 snapshot 为 null（非 404 兜底）——payload 空态即 {ok:true,snapshot:null}
    assert "if(!sn)return b.innerHTML=" in src


def test_radar_zero_write_surface() -> None:
    """硬纪律②：AI 产物永不自动落库——雷达卡零 POST、零「生成骨架」、按钮只弹引导 toast。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 只消费 GET /api/topic_radar（CONTRACT_PATHS 已含该端点，无新增）
    assert "getJSON('/api/topic_radar')" in src
    assert "postJSON('/api/topic_radar" not in all_src
    assert "'/api/topic_radar/refresh'" not in all_src
    # 卡内无「生成骨架」（discover_draft 职责边界，设计 §5.4）
    a = src.index("var RK={radar:")
    b = src.index("function renderIdeaFunnel(){")
    card = src[a:b]
    assert "生成骨架" not in card
    assert "discover_draft" not in card
    # 刷新/立即生成走引导 toast（CLI 命令提示），不发请求
    assert "function radarHint(){toast('请运行 musespiral topic-radar --refresh" in src
    assert "$('radar-refresh').addEventListener('click',radarHint);" in src
    # 空态按钮走 #radar-body 委托（innerHTML 重绘不丢监听——首轮实测踩过 onclick 丢失）
    assert "$('radar-body').addEventListener('click',function(e){if(e.target.closest('#radar-gen'))radarHint();});" in src


def test_radar_candidate_refs_routing() -> None:
    """候选点击分流（设计 §5.4）：material_id→#material?m=<id> / url→外链 noopener / path→mono。"""
    src = _js()
    a = src.index("function rr(c){")
    b = src.index("function renderRadar(){")
    row = src[a:b]
    # material_id → 素材详情抽屉深链（encodeURIComponent 防注入）
    assert 't=\'<a data-t href="#material?m=\'+encodeURIComponent(r.material_id)+\'">\'' in row
    # url → 外链（target=_blank + rel=noopener noreferrer）
    assert 'target="_blank" rel="noopener noreferrer"' in row
    # path → <code> mono 可复制
    assert "'<code>'+esc(r.path)+'</code>'" in row
    # 分流优先级：material_id 先判、url 次判、都无 → 纯文本不可点
    assert row.index("if(r.material_id)") < row.index("else if(r.url)")
    assert "'<span data-t>'+esc(c.title)+'</span>'" in row


# ── Phase C/D 入口 B：素材详情「建议标签」（设计 §四；AI 只建议不落库，2026-11-17）──


def _paint_src() -> str:
    """paintMatDrawer 函数体本身（到 tsug 家族定义前为止）。"""
    src = _js()
    return src[src.index("function paintMatDrawer(d){"):src.index("var tsug={};")]


def _mat_drawer_src() -> str:
    """paintMatDrawer + tsug 家族源码段（tsug* 定义就插在 closeMatDrawer 之前）。"""
    src = _js()
    return src[src.index("function paintMatDrawer(d){"):src.index("function closeMatDrawer(){")]


def _tsug_src() -> str:
    """建议标签区主体（tsug 状态 + 三个渲染/动作函数）。"""
    body = _mat_drawer_src()
    return body[body.index("var tsug={};"):]


def test_tag_suggest_position_after_lineage_before_acts() -> None:
    """§4.1 精确锚点：建议标签区在血统链之后、.mat-acts 动作行之前（多步流程排在单步按钮前）。"""
    body = _paint_src()
    i_lin = body.index("lineageHtml(m)")
    i_tsug = body.index("tsugBox(m)")
    i_acts = body.index('<div class="action-row mat-acts">')
    assert i_lin < i_tsug < i_acts, "建议标签须在血统链之后、动作行之前"
    # 紧邻锚点（R3.5 批 2026-11-18 起更新）：tsugBox(m)+ 之后为 T4「原始章节」
    # 条件行（meta.original_ref 存在时），再接动作行——正典 §4.1 按钮位=.mat-acts 前
    assert 'tsugBox(m)+' in body and '(meta.original_ref?' in body
    assert body.index("tsugBox(m)+") < body.index('(meta.original_ref?') < i_acts
    # 区容器复用 .tag-row（既有标签行族），id=tag-suggest
    src = _js()
    assert '<div class="tag-row" id="tag-suggest">' in src
    # 标题 --fs-caption（设计 §4.1：比动作行 --fs-micro 略大，避免抢层级）
    assert '<span class="action-title" style="font-size:var(--fs-caption)">建议标签</span>' in src


def test_tag_suggest_initial_not_auto_triggered() -> None:
    """初始态只有 [获取建议]（btn-ghost + 青黛文字）——打开详情不静默调 AI（§4.2 初始行）。"""
    src = _js()
    assert '<button class="btn btn-ghost" data-tsg="1" style="color:var(--indigo)">获取建议</button>' in src
    # 自动触发探针：paintMatDrawer / openMatDrawer 内不得直接调 tsugGo
    body = _paint_src()
    assert "tsugGo(" not in body, "paintMatDrawer 内不得自动发起 AI 调用"
    assert "openMatDrawer" in src
    o = src[src.index("async function openMatDrawer(id){"):src.index("function paintMatDrawer(d){")]
    assert "tsugGo" not in o, "openMatDrawer 内不得静默调 AI"


def test_tag_suggest_chips_default_unselected() -> None:
    """硬纪律②：chips 默认全部未选——渲染时不预设 sel，选中态只在点击 data-tso 后出现。"""
    src = _js()
    # 成功态初始化 sel 为空对象（不是按 tags 预填）
    assert "sel:{},cue:d.cache_hit" in src
    # chip 的 on 类由 t.sel[i] 决定（未点击即无）
    assert """'<button class="chip-tag syn-chip'+(t.sel[i]?' on':'')+'" data-tso="'+i+'">'""" in src
    # 点击切换是唯一置位入口
    assert "tsug.sel[ti]=!tsug.sel[ti]" in src


def test_tag_suggest_existing_tag_disabled() -> None:
    """与既有标签同值的建议标「已有」= 禁用 + 删除线 + --ink-3（设计 §4.2 成功态）。"""
    src = _js_css()
    assert """'<button class="chip-tag syn-chip off" disabled title="已有该标签">'""" in src
    assert "（已有）" in src
    assert ".syn-chip.off{border-color:var(--line-1);color:var(--ink-3);text-decoration:line-through;cursor:not-allowed}" in src
    # 冲突判定同时看服务端 existing 与素材自身 tags
    assert "mk.indexOf(g)>=0" in src


def test_tag_suggest_adopt_writes_full_replacement() -> None:
    """采纳：写 /api/materials/triage 的 tags 是全量替换——必须带上既有标签（否则被抹）。"""
    src = _js()
    assert "postJSON('/api/materials/triage',{id:mid,tags:b.concat(p).slice(0,12),confirm:true})" in src
    # 基准优先取素材自身 triage.tags（既有优先），服务端 existing 仅补充
    assert "function tsugBase(){var m=matById(state.mdrawer)||((state.mdrawerData||{}).material)||{};return (((m.triage||{}).tags)||[]).slice()}" in src
    assert "tsug.ex.forEach(function(g){if(b.indexOf(g)<0)b.push(g)});" in src
    # 采纳后区收起（tsug 清空 → 回到 [获取建议]）+ 标签行即时刷新
    assert "tsug={};toast('标签已更新',OK);" in src
    assert "await loadMaterials();renderMaterials();await loadTags();renderCollRail();paintMatDrawer()" in src


def test_tag_suggest_over_limit_warned_beforehand() -> None:
    """超限（既有+采纳 >12）：--amber 注意条 + 列出被弃项——事前告知（服务端 12 上限
    是 ValueError→400 拒绝而非截断，故前端必须自己截断并提前说明）。"""
    src = _js()
    assert "if(u.length>12)h+='<p class=\"action-hint\" style=\"color:var(--amber)\">超出 12 个上限，将保留前 12（既有优先），被弃：'+esc(u.slice(12).join('、'))+'</p>'" in src
    assert "var u=x.concat(p);" in src
    # 与 core/materials/tags.py MAX_TAGS=12 同口径
    from musespiral.core.materials.tags import MAX_TAGS
    assert MAX_TAGS == 12


def test_tag_suggest_empty_and_failure_states() -> None:
    """空=「无建议」--ink-2 灰字非错误；失败=toast 原文 + 保留 [获取建议] 可重试。"""
    src = _js()
    assert '<span class="action-hint">无建议</span>' in src
    assert "st:d.tags&&d.tags.length?'ok':'no'" in src
    # 失败：crimson toast 带原文；st='err' 落回初始分支（[获取建议] 仍在）
    assert "catch(e){tsug={id:mid,st:'err'};toast('获取建议失败：'+e.message,ER);}" in src
    assert "if(t.st!=='ok')return h+" in src
    # 采纳失败同样保留建议面（不清空 tsug）
    assert "catch(e){toast('采纳失败：'+e.message,ER)}" in src


def test_tag_suggest_cache_badge_is_honest() -> None:
    """"（缓存）」徽只在服务端真回 cache_hit 时显示（设计 §4.3：Cache 徽必须诚实）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "cue:d.cache_hit" in src
    assert "+(t.cue?'<span style=\"color:var(--indigo);font-size:var(--fs-micro)\">（缓存）</span>':'')" in src
    # 无 cache_hit 字段（后端当前不回传）→ falsy → 不显示，绝不伪造
    assert "cache_hit?1:0" not in all_src


def test_tag_suggest_chip_colors_indigo_only() -> None:
    """硬纪律① 双色分工：AI 建议 chips 青黛（机器给的）；人工采纳按钮朱砂（我要拍的）。

    2026-11-17 修正：原断言「建议区零朱砂」过度泛化——D 档正典 §F77.3
    （doc:319）明写「采纳主按钮=--vermilion」，复核轮据此把采纳钮改朱砂后
    本门误报。语义边界是**产物 vs 动作**：chips（AI 产物）只准青黛，
    采纳钮（人工动作）必须是朱砂主按钮。
    """
    src = _css()
    assert ".syn-chip.on{background:var(--indigo);color:var(--paper-1)}" in src
    assert ".chip-tag{display:inline-flex;align-items:center;gap:5px;font-size:var(--fs-micro);padding:2px 9px;border-radius:var(--radius-pill);border:1px solid var(--indigo);color:var(--indigo);cursor:pointer}" in src
    # AI 产物区（chips 渲染）只准青黛——用完整抽屉源码定位 chips 段，不截断
    drawer = _mat_drawer_src()
    for banned in ("var(--vermilion", "var(--crimson"):
        chip_seg = drawer[drawer.index("chip-tag"):drawer.index("data-tsg=\"ok\"")] if "chip-tag" in drawer and "data-tsg=\"ok\"" in drawer else ""
        assert banned not in chip_seg, f"AI 建议 chips 段出现人工动作色: {banned}"
    # 人工采纳按钮必须是朱砂主按钮（D 档正典 doc:319）
    assert 'data-tsg="ok"' in drawer
    assert 'class="btn btn-primary" data-tsg="ok"' in drawer


def test_tag_suggest_uses_only_existing_endpoints() -> None:
    """零新增端点：只消费既有 /api/action/tags_suggest（POST）+ /api/materials/triage（POST）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "postJSON('/api/action/tags_suggest',{id:mid,confirm:true})" in src
    assert "/api/tag_suggest" not in all_src  # 拼写守卫（正确路径带 /api/action/ 前缀）
    # 无新增容器 id 外泄
    parsed = _structure()
    assert "tag-suggest" not in parsed.ids, "#tag-suggest 是运行时生成节点，静态骨架不应存在"


# ── Phase C/D 入口 A：素材库「AI 综合」（设计 §三；多选→编排采纳，2026-11-17）──


def _sel_src() -> str:
    """入口 A 主体（sel 工具条 + syn 面板状态机 + 绑定）。

    UIX-3（2026-11-22）起 syn 族落 material-media 子段——切片改在该子段内
    （selSync 到 bindMatMedia 之间，覆盖 selSync/selToggle/synSteps/renderSyn/
    synPoll/synRun/synAdopt 全族，与拆分前等价域一致）。
    """
    src = _mat_part("media")
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    return src[src.index("function selSync(){"):src.index("function bindMatMedia(){")]


def test_material_view_sel_bar_and_syn_panel_structure() -> None:
    """结构：.sel-bar 在 pub-toolbar 之下、#mat-list 之上；#syn-panel 复用 .drawer 容器。"""
    parsed = _structure()
    assert {"sel-bar", "sel-n", "syn-btn", "sel-clear"} <= set(parsed.ids)
    assert {"syn-panel", "syn-n", "syn-body", "syn-close"} <= set(parsed.ids)
    sec = _view_section("material")
    i_tb = sec.index('class="pub-toolbar"')
    i_bar = sec.index('id="sel-bar"')
    i_list = sec.index('id="mat-list"')
    assert i_tb < i_bar < i_list, "工具条须在 pub-toolbar 之下、#mat-list 之上"
    # 复用 .drawer 容器（零新视觉语言）+ 默认收起
    assert '<aside class="drawer" id="syn-panel" hidden aria-label="跨素材综合">' in sec
    # 工具条三件套语义：计数 --ink-2 / AI 综合 btn-primary（朱砂=人工发起）/ 取消选择 ghost
    assert '<span>已选 <b id="sel-n">0</b> 篇</span>' in sec
    assert '<button class="btn btn-primary" id="syn-btn" disabled>AI 综合</button>' in sec
    assert '<button class="btn btn-ghost" id="sel-clear">取消选择</button>' in sec
    # 仅两个设计预批新类（.sel-bar/.syn-step）
    css = _css()
    assert ".sel-bar{display:flex;align-items:center;gap:var(--space-sm)" in css
    assert ".syn-step{display:flex;align-items:center;gap:8px" in css


def test_syn_disable_is_explainable() -> None:
    """硬约束⑥：n<2 或 n>8 时 AI 综合 disabled **且 title 非空**——禁止只置灰不解释。"""
    body = _sel_src()
    assert "b.disabled=!ok;b.title=ok?'':'需选 2-8 篇有正文面的素材'" in body
    assert "var n=state.msel.length,ok=n>1&&n<9" in body
    # 与 plan 边界同口径（plans_builtin.SYNTHESIS_MIN/MAX_MATERIALS）
    from musespiral.core.agent import plans_builtin as pb
    assert (pb.SYNTHESIS_MIN_MATERIALS, pb.SYNTHESIS_MAX_MATERIALS) == (2, 8)


def test_syn_multiselect_has_no_mode_switch() -> None:
    """多选进入靠 ⌘/Ctrl+点击行（零模式开关）；首次给一次性提示条（localStorage 记）。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    assert "(e.metaKey||e.ctrlKey))return matPart('media').then(function(){selToggle(id)" in src  # UIX-3：selToggle 落 media 子文件
    body = _sel_src()
    assert "localStorage.mdpubSynTip" in body
    assert "toast('已进入多选，选中 2-8 篇后点 AI 综合',OK)" in body
    # 运行中禁止变更选择（§3.5）
    assert "function selToggle(id){if(state.syn.busy)return;" in body
    # 零「多选模式」开关按钮
    assert "data-mselmode" not in all_src and "多选模式" not in all_src


def test_syn_six_state_machine_and_progress_points() -> None:
    """六态：idle/submitting/running/done/failed/budget_stopped；进度点三态着色。"""
    body = _sel_src()
    for st in ("'submitting'", "'running'", "'done'", "'failed'", "'budget_stopped'"):
        assert st in body, st
    # 进度点：done 竹绿实心 / running 朱砂脉冲（复用 skel-pulse）/ 未到 --line-2 空心
    css = _css()
    assert ".syn-step i{flex:none;width:8px;height:8px;border-radius:50%;border:1px solid var(--line-2)}" in css
    assert ".syn-step.done i{background:var(--jade);border-color:var(--jade)}" in css
    assert ".syn-step.running i{background:var(--vermilion);border-color:var(--vermilion);animation:skel-pulse" in css
    # 步级行内容：序位 + 步名 + 字符数（mono）+ cache✓ 青黛 chip
    assert "'<div class=\"syn-step'+(f==='done'?' done':f==='running'?' running':'')+'\"><i></i><span>['" in body
    assert "' <em>cache✓</em>'" in body
    assert ".syn-step em{font-style:normal;border:1px solid var(--indigo);color:var(--indigo)" in css


def test_syn_failed_vs_budget_stopped_text_layers() -> None:
    """硬纪律③：failed=crimson 原文+[重试]+[关闭]；budget_stopped=amber 且**无重试**。"""
    body = _sel_src()
    # failed：crimson 条 + 原文 + 重试 + 关闭
    assert """style="color:var(--crimson)">'+esc(S.err||'运行失败')""" in body
    assert '<button class="btn" id="syn-retry">重试</button><button class="btn btn-ghost" id="syn-shut">关闭</button>' in body
    # budget_stopped：amber + 「已完成 N/M 步」+ [去调整预算]，**该分支无重试**
    assert """style="color:var(--amber)">预算已触顶，已完成 '+(S.done||0)+'/'+(S.max||0)+' 步</""" in body
    assert '<a class="btn" href="#settings">去调整预算</a>' in body
    a = body.index("st==='budget_stopped'?")
    b = body.index("st==='done'?")
    branch = body[a:b]
    assert "syn-retry" not in branch, "budget_stopped 分支不得提供重试按钮"
    assert "crimson" not in branch, "budget_stopped 必须用 amber 而非 crimson"


def test_syn_submit_and_poll_contract() -> None:
    """提交 POST /api/agent/run{plan:synthesis_v1,material_ids,confirm} → 轮询 GET /api/agent/runs?id=。"""
    body = _sel_src()
    assert "postJSON('/api/agent/run',{plan:'synthesis_v1',material_ids:state.msel,confirm:true})" in body
    assert "getJSON('/api/agent/runs?id='+encodeURIComponent(id))" in body
    # 零新增端点（三条 agent 端点均已在 test_architecture 登记）
    src = _js()
    for p in ("'/api/agent/run'", "'/api/agent/runs?id='", "'/api/agent/adopt'"):
        assert p in src, p
    # 构造期拒绝（无正文面）前端不预判：toast 原文（§3.5）
    assert "toast('综合失败：'+e.message,ER)" in body


def test_syn_dual_adopt_weights() -> None:
    """双采纳：存为素材=btn-primary（朱砂主）+ 发起创作会话=btn 次型线框；两者文案与去向。"""
    body = _sel_src()
    assert '<button class="btn btn-primary" id="syn-mat">存为素材</button><button class="btn" id="syn-cs">发起创作会话</button>' in body
    assert "toast('已存为素材，去素材库查看',OK)" in body
    assert "toast('已建会话：'+(d.slug||''),OK)" in body
    assert "location.hash='#compose'" in body
    assert "postJSON('/api/agent/adopt',{run_id:state.syn.runId,target:t,confirm:true})" in body


def test_syn_ai_indigo_marker_and_zero_autowrite() -> None:
    """硬纪律①②：AI 标记青黛唯一；AI 产物永不自动落库（只有 done 出采纳按钮、采纳才写）。"""
    body = _sel_src()
    # 步级 cache 徽青黛；AI 综合按钮是朱砂（人工发起动作，不是 AI 标记）
    assert "cache✓" in body
    # 未 done 不出采纳按钮（渲染分支：只有 st==='done' 含 syn-mat/syn-cs）
    s_done = body.index("st==='done'?")
    s_run = body.index("st==='running'?")
    assert "syn-mat" not in body[:s_done].split("innerHTML=")[-1]
    # 唯一写面是 adopt（显式 confirm）；提交本身不落素材
    assert "confirm:true" in body
    assert "materials" not in body.split("synRun")[0].split("function synRun")[0][-200:]


# ── G1 组合批（2026-11-17/18）：文生图面板 / AI-GENERATED 徽 / F1 用量条 / E1 金路径 ──


def _gen_src() -> str:
    # matIngest 已随 2026-11-20 修红迁 core 段；UIX-3（2026-11-22）起 gen 族落
    # material-media 子段——切片端标记改用同段 bindMatMedia（覆盖 gPaint/gGo/gAdopt 不变）
    body = _mat_part("media")
    return body[body.index("function gPaint("):body.index("function bindMatMedia(){")]


def _src_of(start: str, end: str) -> str:
    body = _js()
    a = body.index(start)
    return body[a:body.index(end, a)]


def test_g1_gen_panel_structure_and_entry() -> None:
    """G1 结构：素材工具条「AI 生成图」入口（青黛 class gen-ai）+ .drawer 壳 #gen-panel。"""
    src = _css()
    s = _view_section("material")
    assert 'id="mat-gen-btn"' in s and "AI 生成图" in s
    assert 'class="btn gen-ai"' in s
    assert 'id="gen-panel"' in s and 'class="drawer"' in s
    # 面板要件：prompt textarea / 尺寸 chips / 模型 chips / 生成+取消
    assert 'id="gen-prompt"' in s
    assert 'data-size="1024x1024"' in s and 'data-size="1536x1024"' in s
    assert 'data-model=""' in s and 'data-model="Qwen-Image"' in s
    assert 'id="gen-run"' in s and 'id="gen-cancel"' in s
    assert 'id="gen-result"' in s
    # 青黛=AI 标记样式在产物 CSS 内
    assert ".gen-ai{color:var(--indigo)}" in src


def test_g1_state_machine_idle_generating_preview_failed() -> None:
    """G1 状态机三态齐备：生成中 skel / 失败 crimson / 预览图 + 存为素材+重新生成。"""
    body = _gen_src()
    assert "st==='gen'" in body and "skel" in body
    assert "st==='bad'" in body and "gen-fail" in body and "重试" in body
    assert "g.pv" in body and '<img alt="" src=' in body
    assert "存为素材" in body and "重新生成" in body
    # 生成中禁用生成按钮 + 显示取消
    assert "$('gen-cancel').hidden=st!=='gen'" in body
    assert "$('gen-run').disabled=st==='gen'" in body


def test_g1_preview_is_server_temp_url_not_b64() -> None:
    """G1 A-2 UI 断言：预览 <img> 直指服务端临时文件 URL，前端零 b64 拼接。"""
    body = _gen_src()
    assert "g.pv.url" in body
    assert "atob" not in body and "data:image" not in body and "base64" not in body
    # 采纳走 confirm:true（人审闸门）
    assert "postJSON('/api/action/adopt_generated'" in body and "confirm:true" in body


def test_g1_generate_request_shape() -> None:
    """G1 请求形状：prompt/size/confirm 恒在；model 走 params 透传（A-3 扩展位）。"""
    body = _gen_src()
    assert "var n=++gN,b={prompt:p,size:g.size,confirm:true}" in body
    assert "if(g.model)b.params={model:g.model}" in body
    assert "fetch('/api/action/generate_image'" in body
    # 取消用 AbortController + 序号守卫（丢弃迟到响应）
    assert "AbortController" in body and "n!==gN" in body


def test_g1_ai_generated_badge_and_source_line() -> None:
    """AI-GENERATED 第四枚举徽（琥珀 lic-ai）+ 生成来源行（provider/model/时间）。"""
    src = _js_css()
    assert "lic-badge lic-ai" in src
    assert ".lic-badge.lic-ai{color:var(--amber)" in src
    assert "AI-GENERATED" in src
    # 来源行在 licBadge 内（一次调用同时出徽与来源，meta.ai_generated 守卫）
    a = src.index("function licBadge(")
    seg = src[a:src.index("function matPreview(m){", a)]
    assert "x.ai_generated" in seg and "gen-src" in seg
    assert "x.provider" in seg and "x.model" in seg and "generated_at" in seg


def test_f1_usage_bar_and_budget_line() -> None:
    """F1：设置 AI 域底部「近 7 日用量」pbar 行（任务×档案）+ 预算行（两读面）。"""
    s = _view_section("settings")
    assert "近 7 日用量" in s and 'id="ai-meter"' in s
    assert 'id="media-verify-btn"' in s and 'id="media-dot"' in s and 'id="media-txt"' in s
    body = _src_of("function loadAiMeter(", "function mediaVerify(")
    assert "getJSON('/api/ai/usage?days=7')" in body
    assert "getJSON('/api/ai/budget')" in body
    assert 'class="pbar"' in body and "pbar-fill" in body  # 对齐仪表盘 viz 语义
    assert "日预算" in body
    # 设置路由装载时拉取
    assert "loadAiMeter();" in _js()


def test_f1_media_verify_three_state_health_lamp() -> None:
    """G1 A-4：设置面「验证」→ media_verify → 健康灯三态（ok/down/unconfigured）。"""
    body = _src_of("function mediaVerify(", "function renderSources(){")
    assert "postJSON('/api/action/media_verify',{confirm:true})" in body
    assert "var MEDH={ok:'正常',down:'不可达'}" in _js()
    assert "'health-dot '+" in body and "'unknown'" in body
    assert "MEDH[s]||'未配置'" in body


def test_e1_pipeline_button_and_job_submit() -> None:
    """E1：文章详情操作 Tab 顶部「一键到物料」btn-primary → POST /api/action/pipeline。"""
    # ARCH-1：panel-actions 骨架自 tmpl 迁 ui.views/article.html（壳内只剩占位）
    art = (Path(__file__).resolve().parent.parent / "musespiral" / "studio"
           / "ui.views" / "article.html")
    t = art.read_text(encoding="utf-8")
    panel = t[t.index('id="panel-actions"'):]
    assert "一键到物料" in panel
    assert 'class="btn btn-primary" id="pipe-run"' in panel
    # 主按钮出现在其它 action-group 之前（面板首段）
    assert panel.index('id="pipe-run"') < panel.index("生成平台变体")
    assert 'id="pipe-progress"' in panel
    body = _src_of("function pRun(", "function pubGaps(){")
    assert "postJSON('/api/action/pipeline',{article:path,confirm:true})" in body
    assert "trackJob(d.job_id)" in body
    assert "jobsPoll=setInterval(refreshJobs,3000)" in body


def test_e1_pipeline_step_rows_render() -> None:
    """E1 步级：{step,status,detail,artifacts} → .syn-step 行（done 绿点/running 脉冲）。

    渲染共享函数 stepRows 供任务面板（jobRow pipeline 分支）与操作 Tab（pPaint）
    双处调用——同源同形；失败时 pPaint 追加 crimson 行。
    """
    body = _src_of("function stepRows(", "function jobRow(")
    assert "x.status==='done'" in body and "x.status==='running'" in body
    assert "syn-step" in body and "esc(x.step)" in body and "x.detail" in body
    assert "x.artifacts" in body and "pipe-arts" in body
    assert "(i+1)+'/'" in body  # 步序号 n/N
    pp = _src_of("function pPaint(", "function pRun(")
    assert "stepRows(p.steps)" in pp and "var(--crimson)" in pp
    jr = _src_of("function jobRow(", "function renderJobs(")
    assert "j.kind==='pipeline'" in jr and "stepRows(" in jr
    assert "pipeline:'一键金路径'" in _js()


# ── F-A/B/C 前端体验四项改进批（2026-11-18，Refinement）──────────────


def test_fa_drawer_media_by_type() -> None:
    """F-A：image 抽屉顶部原图（file full 非 thumb）+灯箱按钮；video 播放条；余类型 pre。"""
    src = _js()
    body = _src_of("function paintMatDrawer(d){", "function closeMatDrawer(){")
    # image→原图（variant=full）+既有 data-mlight 灯箱体系
    assert "if(m.type==='image')media='<img class=\"drawer-media\" src=\"'+esc(full)+'\"" in body
    assert "&variant=full'" in body  # 非 thumb
    assert 'data-mlight="\'+esc(m.id)+\'">灯箱查看' in body
    # video→controls+preload=metadata；local_path 走 file full，外链走 payload.url/o.uri
    assert "else if(m.type==='video')" in body
    assert '<video class="drawer-media" controls preload="metadata" src="' in body
    assert "p.local_path?full:(p.url||o.uri)" in body
    assert "新窗口打开" in body
    # 分流兜底：其余类型维持 pre 正文（media 为空时渲染）
    assert "(media||'<pre class=\"drawer-preview\">'+esc(prev)+'</pre>')+" in body
    # 灯箱按钮复用既有委托（data-mlight → lbOpen）
    assert "if(el.hasAttribute('data-mlight'))return matPart('media').then(function(){lbOpen(" in src  # UIX-3：lbOpen 落 media 子文件


def test_fb_swatch_colors_and_recent() -> None:
    """F-B：色 chip 改圆形色点（背景=slot_hexes[s]、title=槽名）+扩槽跟随+最近 5 槽置顶。"""
    src = _shell_js_css()
    # slot_hexes 数据面来自 GET /api/visual/templates（DV 缓存）
    assert "getJSON('/api/visual/templates')" in src
    assert "DV.slotHexes=d.slot_hexes||{}" in src
    # 扩槽自动跟随：槽集取服务端 color_slots（DV.slots），非写死 5
    assert "DV.slots=((d.items||[])[0]||{}).color_slots||Object.keys(DV.slotHexes)" in src
    # swatch 色点：style background=hex + title=槽名
    sw = _src_of("function dvSwatch(s){", "function dvColorChips(){")
    assert "style=\"background:'+esc(DV.slotHexes[s]||'var(--paper-2)')+'\"" in sw
    assert "title=\"'+esc(s)+'\"" in sw
    # 最近使用行：DV.recent 置顶 + localStorage 'musespiral-dv-recent'
    cc = _src_of("function dvColorChips(){", "function dvSync(){")
    assert "var rec=DV.recent.filter(function(s){return slots.indexOf(s)>=0;});" in cc
    assert "'<span class=\"dv-cap\">最近</span>'+rec.map(dvSwatch).join('')" in cc
    assert "musespiral-dv-recent" in src
    assert "DV.recent=[s].concat(DV.recent.filter(function(x){return x!==s;})).slice(0,5);" in src
    # CSS：圆形色点
    assert ".dv-colors .chip{width:26px;height:26px" in src and "border-radius:50%}" in src
    # F72-T2 纪律不破：hint 文案保留（禁任意色值）
    assert "仅主题色槽，禁任意色值（F72-T2）" in src


def test_fc_pool_remove_restores_add_state() -> None:
    """F-C：已入池按钮变「移除」（data-cm-rm）→askConfirm 连坐文案→POST→恢复加入态+重拉。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    pool = _src_of("function renderCmPool(){", "function renderCmSources(){")
    # 已入池=移除按钮（data-cm-rm），未入池=加入（data-cm-add）
    assert 'data-cm-rm="\'+esc(c.path)+\'"' in pool
    assert ">移除</button>" in pool
    assert 'data-cm-add="' in pool and ">加入</button>" in pool
    # 点击委托：data-cm-rm → removeCmSource
    assert "if(r){removeCmSource(r.getAttribute('data-cm-rm'));return;}" in src
    rm = _src_of("async function removeCmSource(path){", "function renderCmPool(){")
    # 连坐文案（2026-11-18 集成修复）：'source_ref' 块归因在生产代码无赋值处
    # （compose 只产 ai/human），原「草稿中 N 块」计数恒 0 为死代码——禁返锁，
    # 改取素材勾选章节/全文（session 详情 sources.sections）+已生成轮次保留
    assert "b.origin==='source_ref'" not in all_src
    assert "s.path===nm" in rm and "(ps||{}).sections||[]" in rm
    assert "'该素材已选 '+ns.length+' 章，移除后后续再生成不再取其内容。'" in rm
    assert "'该素材为全文入池，移除后后续再生成不再取其内容。'" in rm
    assert "'已生成轮次保留，不再回改。'" in rm
    assert "askConfirm(" in rm
    # POST remove_source（confirm:true）→响应 sections 入成功 toast→重拉详情+重渲染
    # （池行恢复加入态 + cm-sources 刷新）
    assert "postJSON('/api/compose/remove_source',{slug:state.cslug,path:path,confirm:true})" in rm
    assert "d.sections&&d.sections.length?'（已选 '+d.sections.length+' 章）'" in rm
    assert "await loadComposeDetail(state.cslug,true);renderCompose();" in rm


def test_fabc_contract_lock_remove_source_and_templates() -> None:
    """契约锁：CONTRACT_PATHS 含 F-C remove_source 与 F-B templates；两端点在前端有字面量。"""
    assert "/api/compose/remove_source" in CONTRACT_PATHS
    assert "/api/visual/templates" in CONTRACT_PATHS
    src = _js()
    assert "'/api/compose/remove_source'" in src
    assert "'/api/visual/templates'" in src


# ── R4 #books 书籍视图 + R3.5 提炼触点（2026-11-18 批）──────────────


def test_books_view_structure_and_detail_state() -> None:
    """R4 §二/§四：八视图导航第三入口 + 详情态全规格结构（info-grid 三栏/
    目录卡/操作条；换源清勾选/过滤不改勾选/范围 chips/卷全选/500 章按需渲染）。"""
    parsed = _structure()
    # 视图容器与三子态分区（hash 驱动：默认搜索占位 / ?book=&source= 详情 / ?tab=subs）
    assert {"view-books", "bk-search", "bk-search-box", "bk-guide",
            "bk-detail", "bk-absent", "bk-body", "bk-cover", "bk-name",
            "bk-src-sel", "bk-toc", "bk-filter", "bk-specs", "bk-range",
            "bk-opbar", "bk-digest", "bk-import-btn", "bk-prog", "bk-subs"} <= set(parsed.ids)
    assert "books" in parsed.attrs["data-view"] and "books" in parsed.attrs["data-nav"]
    src = _shell_js_css()
    # 导航：供给组第三入口「书籍」（源中枢/素材库之后）
    assert '<a class="nav-item" data-nav="books" href="#books">' in src
    assert "var VIEWS=['sources','material','books','compose','design','publish','insight','article','settings','plugins','plugin-run'];" in src
    # hash 三子态分发
    assert "route.params.get('tab')==='subs'" in src
    assert "bkOpen(route.params.get('book'),route.params.get('source')" in src
    # info-grid 三栏（160px 封面 | flex | 280px 来源卡）
    assert ".bk-grid{display:grid;grid-template-columns:160px minmax(0,1fr) 280px" in src
    # 换源 select 变更=重拉 info/toc+清空勾选（bkOpen 内 BK.sel={} 复位）
    assert "BK.sourceUrl=v;bkOpen(BK.bookUrl,v,BK.name);" in src
    assert "BK.gs=[];BK.sel={};BK.spec='all';bkChips();" in src
    # 过滤只是视图（display 收窄），勾选态写 BK.sel 不受过滤影响
    assert "BK.sel[t.getAttribute('data-bkurl')]=t.checked;" in src
    assert "r.style.display=r.querySelector('.t').textContent.indexOf(BK.filter)>=0?'':'none'" in src
    # 范围预设 chips（全部/最新 5/最新 10/自定义起止）——点击即批量设勾选
    assert {"all", "l5", "l10", "custom"} <= parsed.attrs["data-bkspec"]
    assert "(spec==='all'?BK.toc:BK.toc.slice(-n)).forEach(function(c){BK.sel[c.url]=true;});" in src
    # 卷分组+卷头全选/反选
    assert 'data-bkvsel="\'+j+\'' in src and "function bkVolSel(gi){" in src
    assert "BK.gs[gi];if(!g)return;" in src
    # 500+ 章按需渲染：非首卷 data-pending 占位，details toggle（捕获）首次展开才渲染
    assert "' data-pending=\"'+j+'\"'" in src
    assert "$('bk-toc').addEventListener('toggle'," in src
    assert "bkRenderRows(pi);}},true);" in src
    # 底部操作条 sticky：分章（默认）/合并（disabled，R4 后端批）双 radio
    assert ".op-bar{position:sticky;bottom:8px" in src
    assert src.count('name="bk-mode"') == 2 and 'name="bk-mode" disabled' in src
    # 合计约 X 字=wordCount 均摊估计（tabular 口径「约」）
    assert "Math.round((/万/.test(w)?parseFloat(m[1])*1e4:+m[1])/BK.toc.length*n):0" in src


def test_books_digest_switch_default_and_hint() -> None:
    """R3.5 T1（§2.1）：操作条「AI 提炼」switch 默认开+localStorage 记忆+
    off 提示「原始文本直入」；on=朱砂边框态（复用 .switch 圆点滑轨）。"""
    src = _shell_js_css()
    assert "localStorage.getItem('musespiral-book-digest')!=='0'" in src  # 缺省=开
    assert "localStorage.setItem('musespiral-book-digest',this.checked?'1':'0')" in src
    assert "原始文本直入（不做 AI 提炼）" in src
    assert '<input type="checkbox" id="bk-digest"><i></i>AI 提炼</label>' in src
    assert ".switch input:checked+i{border-color:var(--vermilion)" in src
    assert ".switch input:checked+i::after{left:15px;background:var(--vermilion)}" in src
    assert "digest:!!BK.digest}" in src  # 开关态随提交传端点


def test_books_fetch_progress_steps_and_partial() -> None:
    """R3.5 T2（§2.2）：提交→job book_fetch 步级轮询（jobs phase+agent runs 步明细）
    +syn-step 三态+cache✓ 青黛徽+综述等待行+取消（集成轮=真取消）+部分成功 toast+深链。"""
    src = _js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 提交面：confirm 闸在端点；digest 开关随行
    assert "postJSON('/api/materials/from-chapters',{confirm:true,sourceUrl:" in src
    assert "book:{name:BK.name,author:(BK.info||{}).author||''},chapters:chs,digest:!!BK.digest}" in src
    # 上限前门=后端同款（提炼 15/直入 30）
    assert "提炼模式上限 15 章（原始直入 30 章）" in src
    # 步级轮询双源：jobs（phase/终态）+ agent runs（digest_N 步明细）
    assert "j=await getJSON(`/api/jobs/${id}`);" in src
    assert "run=(await getJSON('/api/agent/runs?id='+encodeURIComponent(id))).run||null;" in src
    # 步级行=.syn-step 三态 + digest_ 前缀替换 + book_synthesis 综述 + cache✓
    assert "x.status==='done'?' done':x.status==='running'?' running':''" in src
    assert "String(x.id).replace(/^digest_/,'第 ')" in src
    assert "x.id==='book_synthesis'?'全书综述'" in src
    assert "(x.cache_hit?' <em>cache✓</em>':'')" in src
    assert "全书综述 · 等待（≥5 章追加）" in src  # ≥5 章未到步的等待行
    # 取消=真取消（POST /api/jobs/cancel）——专测见
    # test_books_cancel_wires_jobs_cancel_endpoint（2026-11-18 集成轮落地）
    assert "data-bkcancel" in src
    # 旧「停止跟踪」死文案退役（精确整句——「任务在后台继续」另有 syn-close
    # 面板的合法同款，不能整串禁入）
    assert "已停止跟踪" not in all_src
    assert "已停止跟踪——任务在后台继续，完成后见右上任务中心" not in all_src
    # 部分成功是合法终态：toast 列 M 完成/N 未完成
    assert "，'+ff.length+' 章未完成'" in src
    assert "ff.map(function(x){return '第 '+(x.index!=null?x.index:'?')+' 章'" in src
    # 完成深链素材库过滤视图（M5）
    assert "esc('#material?q=book:\"'+BK.name+'\"')" in src


def test_books_cancel_wires_jobs_cancel_endpoint() -> None:
    """集成轮（2026-11-18，R3.5 正典 §2.2 取消语义落地）：取消钮=POST
    /api/jobs/cancel（协作式——已完成章保留/未完成章放弃）+终态 M/N toast
    「已取消：已入库 M 章，N 章未完成」+面板取消态标注；旧「停止跟踪」文案退役。"""
    src = _js()
    assert "postJSON('/api/jobs/cancel',{job_id:BK.job.id,confirm:true})" in src
    assert "'取消请求已发送——正在于检查点收束（已完成章保留）'" in src
    assert "'任务已终态，无需取消'" in src
    # 终态 toast（bkPoll done 分支）：cancelled 分支明列 M/N（正典 §2.2 红线 2）
    assert "r.cancelled?('已取消：已入库 '+M+' 章，'+N+' 章未完成')" in src
    assert "M=(r.materials||[]).length,N=(r.chapters_total||BK.job.chs)-M" in src
    # 面板取消态：amber「已取消」标注+放弃说明
    assert "(r.cancelled?'<span style=\"color:var(--amber)\">已取消</span>——':'')" in src
    assert "未完成章已放弃（已完成章保留入库）" in src


def test_material_book_badge_source_line_and_original() -> None:
    """R3.5 T3+T4：素材行「未提炼」琥珀徽+《书名》第 N 章来源行（列表+抽屉）+
    抽屉「原始章节」btn-ghost→details 就地展开（骨架→GET original→pre 400px+
    >50k 两段展开全部）。"""
    parsed = _structure()
    src = _js_css()
    # T3 徽章：distilled=false 降级标记（AI-GENERATED 徽=来源维度，可并存）
    assert "bk&&!bk.synthesis&&bk.distilled===false" in src
    assert "AI 提炼失败，此条为原始文本；可在详情重新提炼" in src
    # T3 列表来源行：《书名》第 N 章（截断 20 字，title 全名）深链 #books
    assert 'href="#books?search=\'+encodeURIComponent(bk.name||\'\')+\'"' in src
    assert "String(bk.name||'').slice(0,20)" in src
    # T3 抽屉来源行：《书名》 作者 · 第 N 章 章名（综述态分支）
    assert "meta.book.chapter_index?' · 第 '+meta.book.chapter_index+' 章 '" in src
    assert "meta.book.synthesis?' · 全书综述（'+(meta.book.chapters||0)+' 章）'" in src
    # T4 按钮：.mat-acts 前 btn-ghost「原始章节」，统一走 matActsClick 委托
    assert 'data-morig="1"' in src and "[data-morig]'" in src
    assert "meta.original_ref?'<div class=\"action-row\"><button class=\"btn btn-ghost\" data-morig=\"1\"" in src
    # T4 就地展开：details.orig-view 骨架→GET→pre；已有则 toggle 开合（再点收起）
    assert "className='orig-view'" in src
    assert "dv.hasAttribute('open')?dv.removeAttribute('open'):dv.setAttribute('open','')" in src
    assert "getJSON('/api/book/original?ref='+encodeURIComponent(ref))" in src
    assert ".orig-view pre{max-height:400px;overflow:auto" in src
    # 50k 折叠两段：folded→前 20k+「展开全部」带 full=1
    assert "if(d.folded){" in src and "'&full=1'" in src
    assert "more.textContent='展开全部（共 '+(d.chars||0)+' 字）'" in src


def test_books_search_subs_placeholder_and_engine_lamp() -> None:
    """本批占位面：搜索态=搜索框+引擎灰灯（absent 引导卡+disabled）；
    订阅态=导入弹窗入口（R4 导入弹窗批 2026-11-19）+订阅表仍归 R4 批；
    搜索结果渲染归 R4 批（POST /api/book/search 不接）。"""
    src = _shell_js()
    # M1 分域读取后，否向断言的检查域回到全产物并集（=单文件时代的
    # 等价语料）：退役文案/旧类名从任一产物回流都必须被抓到。
    all_src = _all()
    # 引擎灯：GET /api/booksource/engine→绿点可搜/灰点未就绪；absent 搜索框 disabled
    assert "getJSON('/api/booksource/engine')" in src
    assert "(ok?'ok':'unknown')" in src
    assert "bookengine '+(ok?'可搜':'未就绪')" in src
    assert "$('bk-search-box').disabled=!ok;" in src
    # 详情/搜索双引导文案（absent=引擎未就绪；ok=R4 批开放）
    assert "'bookengine 引擎未就绪'" in src
    assert "'搜索结果渲染 · R4 批'" in src
    # 订阅态：导入弹窗入口钮（两步导入已落地）；订阅表本身仍 R4 批
    assert 'id="im-open"' in src and "导入书源" in src
    assert "订阅表 R4 批落地" in src
    assert "musespiral booksource" in src
    # 搜索提交未接（任务书裁决：结果渲染 R4 批）——契约锁同步排除
    assert "'/api/book/search'" not in all_src


# ── M3 视图拆分归属断言（2026-11-19；题面第 6 条：视图专属断言改读对应 views/*.js）──


def test_m3_view_products_exist() -> None:
    """视图产物齐全且非空（core+views 拆分的物理证据；PL-3 批起 11 视图；
    UIX-3 起 material 为三子段产物 material-core/drawer/media，单文件退役）。"""
    expected = {"sources", "books", "compose", "design",
                "publish", "insight", "article", "settings",
                "plugins", "plugin-run"} | {"material-core", "material-drawer",
                                            "material-media"}
    # ARCH-1：views/ 另有 <view>.html.js 骨架注入器（stem 带 .html 后缀）——排除
    actual = {p.stem for p in VIEWS_JS.glob("*.js")
              if not p.name.endswith(".html.js")}
    assert actual == expected, f"视图产物集合不符: {actual}"
    assert not (VIEWS_JS / "material.js").exists(), "单文件 material.js 应已退役"
    for name in expected:
        assert (VIEWS_JS / f"{name}.js").stat().st_size > 1000, name


def test_m3_view_render_functions_in_own_file() -> None:
    """每个视图的主渲染函数落在**对应** views/<view>.js（不是 core，也不是别家）。"""
    mapping = {
        "publish": "function renderPublish(){",
        "material": "function renderMaterials(){",
        "compose": "function renderCompose(){",
        "insight": "function renderInsight(){",
        "article": "function renderDetail(){",
        "settings": "function renderSettings(){",
        "books": "function bkPaint(){",
    }
    core = _core_js()
    for view, decl in mapping.items():
        # UIX-3：material 的主渲染落 material-core 子段
        own = _mat_part("core") if view == "material" else _view_js(view)
        assert decl in own, f"{decl} 不在 views/{view} 的产物"
        assert decl not in core, f"{decl} 不应残留 core.js"
    # design/sources 的入口是 dvEnter / renderSources（非 render* 命名）
    assert "function dvEnter(){" in _view_js("design")
    assert "function renderSources(){" in _view_js("sources")


def test_m3_core_owns_shared_boundaries() -> None:
    """共享边界留 core.js：状态/工具/路由/命令面板/toast/jobs/懒加载器/图标。"""
    core = _core_js()
    for token in ("var state={", "function $(id)", "function esc(value)",
                  "function request(method,path,body)", "function postJSON(path,body)",
                  "function getJSON(path)", "function toast(message,kind,action)",
                  "function trackJob(id)", "function waitForJob(id)",
                  "async function renderRoute(){", "function cpBuild(q)",
                  "function ensureView(view)", "var ICONS={"):
        assert token in core, f"共享边界 {token!r} 不在 core.js"
    # 视图事件绑定不属于 core（已拆到各 views/<view>.js 的 bind<View>）
    assert "function bindCore(){" in core
    assert "function bindArticle(){" not in core
    assert "function bindArticle(){" in _view_js("article")


def test_m3_lazy_load_wiring() -> None:
    """core.js 路由含 ensureView(view) 注入 + assets/views/ 路径（幂等 Promise）；
    视图脚本用顶层 function 声明（注入即全局）。"""
    core = _core_js()
    assert "__viewPromises" in core
    # ARCH-1 两步装载：html.js（innerHTML 填充壳内空占位）→ <view>.js（行为脚本）
    assert '__loadScript("assets/views/"+view+".html.js")' in core
    # UIX-3：JS 首载名经 VJS 映射（material→material-core.js，余视图原名单文件）
    assert '__loadScript("assets/views/"+(VJS[view]||view+".js"))' in core
    assert "__onViewHtml" in core
    assert "await ensureView(view)" in core
    # 至少 3 个视图脚本用顶层函数声明（零 import/export）
    for view in ("publish", "article", "material"):
        own = _mat_part("core") if view == "material" else _view_js(view)
        assert re.search(r"^function \w+\(", own, re.MULTILINE), view
        assert "import " not in own and "export " not in own
    # UIX-3：material 懒加载链——首载 -core（VJS 映射），drawer/media 由 matPart 按需拉
    assert "VJS={material:'material-core.js'}" in core
    assert "assets/views/material-'+name+'.js" in _mat_part("core")


def test_all_negative_gate_covers_lazy_assets() -> None:
    """fix-6（AI-3 复核 2026-11-21）：否向断言域 `_all()` 必须覆盖 core.js:685
    懒加载的 assets/*.js——否则「window.prompt 不得出现」这类全局红线在
    iterations.js 等懒加载产物里的违例永远扫不到（真实假门判例：iterations.js
    用系统输入框收集重生成 feedback 却全绿）。

    锁法：iterations.js（懒加载）确实在 `_all()` 域内；且当前全域无
    window.prompt / 系统确认对话框泄漏。
    """
    all_src = _all()
    # iterations.js 的独有标记串确在域内（证明它被读了，不是靠猜声明生效）
    assert "window.__mdpubMatIter" in all_src, "iterations.js 未进入 _all() 否向域"
    # 全手写产物（含懒加载）无系统对话框泄漏
    assert "window.prompt" not in all_src
    assert "window.confirm" not in all_src


# ── FEAT-1 语义搜索前端（2026-11-22）：三模式 / 相关度徽章 / 语义相关区 / 降级 ──


def test_feat1_search_modes_in_shell() -> None:
    """搜索框三模式指示器 + 相关度排序项 + 降级提示位进入素材视图骨架。"""
    src = _source()
    seg = src[src.index('<div class="pub-toolbar">'):src.index('<div class="sel-bar"')]
    assert 'id="mat-modes"' in seg
    for m in ("auto", "keyword", "semantic"):
        assert 'data-searchmode="%s"' % m in seg
    assert '<option value="relevance">' in seg  # 相关度排序
    assert 'id="mat-mode-hint"' in seg  # 降级提示


def test_feat1_search_mode_logic_in_core() -> None:
    """三模式自动切换 + 相关度徽章 + 降级重搜在 core 共享块。"""
    src = _core_js()
    assert "function effectiveMode(q){" in src
    assert "function looksNatural(q){" in src
    assert "function simPct(v){" in src
    assert "function matSearch(q){" in src
    # auto 按输入形态选 hybrid/keyword
    assert "looksNatural(q)?'hybrid':'keyword'" in src
    # 降级：ok:false + semantic 模式 + 未就绪 → 切关键词重搜 + 提示
    assert "mode==='semantic'&&state.searchReady===false" in src
    assert "state.searchMode='keyword'" in src
    assert "已自动切换为关键词搜索" in src
    # 三路搜索端点 + 相关度排序
    assert "/api/materials/search?q=" in src
    assert "'relevance'" in src or "msort==='relevance'" in _js()


def test_feat1_related_section_and_badges() -> None:
    """详情「语义相关」区 + 点击跳转 + 列表相关度徽章。"""
    all_src = _all()
    assert "function matRelatedHtml(related){" in all_src
    assert "data-mrelated=" in all_src
    assert "simPct(m.similarity)" in all_src
    assert "matRelatedHtml(d.related)+" in _js()
    assert ".sim-badge{" in _css() and ".sim-related{" in _css()


# ── FEAT-3 数据回流：洞察阅读/在看/分享趋势（SVG 自绘，按周/月）────────

def test_feat3_stats_trend_skeleton() -> None:
    """洞察视图含 stats 趋势容器 + 周/月粒度 chips + 说明节点。"""
    src = _source()
    assert 'id="stats-trend-card"' in src
    assert 'id="stats-trend"' in src
    assert 'id="stats-trend-note"' in src
    assert 'data-srange="week"' in src and 'data-srange="month"' in src
    # 位置：紧随发布趋势卡之后、源贡献之前
    i_pt, i_st, i_src = (src.index(x) for x in
                         ('id="trend-chart"', 'id="stats-trend"', 'id="contrib-bars"'))
    assert i_pt < i_st < i_src


def test_feat3_stats_trend_logic_in_view_js() -> None:
    """statsBuckets 按周/月聚合 + renderStatsTrend SVG 自绘 + 周/月切换绑定。"""
    src = _view_js("insight")
    assert "function statsBuckets(mode){" in src
    assert "if(mode==='month')k=d.slice(0,7)" in src  # 月桶 YYYY-MM
    assert "dt.setDate(dt.getDate()-wd)" in src  # 周桶归一到周一
    # 三指标口径：优先 stats 子对象，兼容顶层 reads/shares（zaikan 恒取 stats）
    assert "s.reads!=null?s.reads:r.reads" in src
    assert "+s.zaikan||0" in src
    assert "s.shares!=null?s.shares:r.shares" in src
    # SVG 自绘：三条 path + 圆点 + 网格 + 图例，零图表库
    assert "function renderStatsTrend(){" in src
    assert "<svg viewBox=\"0 0 '+W+' '+H+'\"" in src
    for cls in ("st-reads", "st-zaikan", "st-shares"):
        assert 'class="st-line st-' in src and cls in src
    assert "st-legend" in src
    # 空态引导（不是空白闪烁）
    assert "发表并回填数据后，此处显示趋势" in src
    # 接进 renderInsight 渲染链 + 周/月 chip 委托（FEAT-P1B：renderWriting 同链）
    assert "renderTrend();renderStatsTrend();renderWriting();renderPlatformBars()" in src
    assert "state.srange=c.getAttribute('data-srange')" in src
    assert "renderStatsTrend();" in src


def test_feat3_stats_trend_css_dark_safe() -> None:
    """趋势 SVG 三线色走 CSS 变量（暗色覆写自动生效），零硬编码色。"""
    css = _css()
    for sel in ("#stats-trend-card .chips", ".stats-trend svg", ".st-legend"):
        assert sel in css
    assert ".stats-trend .st-reads{stroke:var(--vermilion)}" in css
    assert ".stats-trend .st-zaikan{stroke:var(--amber)}" in css
    assert ".stats-trend .st-shares{stroke:var(--viz-1)}" in css


# ── FEAT-5 前端交互升级（2026-11-22）：快捷键速查 / 空态引导 / 首访骨架 / 暗色补全 ──

def test_feat5_kbd_help_modal_shell_and_bindings() -> None:
    """? 唤起快捷键速查：壳 modal 结构 + core 键位绑定（?=/）+ Escape 链 + 命令面板底注。"""
    shell = _shell_bare()
    for i in ("kbd-modal", "kbd-title", "kbd-body", "kbd-close"):
        assert f'id="{i}"' in shell, f"壳缺 #{i}"
    assert '<kbd>?</kbd> 全部快捷键' in shell  # 命令面板底注可发现性
    core = _core_js()
    # UIX-3 无障碍批：kbdOpen/kbdClose 走 openDlg/closeDlg（焦点入弹 + 关闭还焦）
    assert "function kbdOpen(){kbdPaint();openDlg('kbd-modal','#kbd-close');}" in core
    assert "function kbdClose(){closeDlg('kbd-modal');}" in core
    assert "function kbdPaint(){" in core
    # ? 唤起（输入框外守卫，与 g 序列同款）；/ 聚焦当前视图搜索框
    assert "if(e.key==='?'){e.preventDefault();kbdOpen();return;}" in core
    assert "var kf=KFOCUS[routeInfo().name];if(kf){var ke=$(kf);if(ke){e.preventDefault();ke.focus();" in core
    assert "var KFOCUS={material:'mat-search',books:'bk-search-box',publish:'fetch-url'};" in core
    # Escape 关闭链：速查弹层与其他浮层同层可关（spark/cp 之后、ctx 之前）
    esc_seg = core[core.index("if(e.key==='Escape'){"):core.index("if(!$('ctx').hidden)")]
    assert "if(!$('kbd-modal').hidden){kbdClose();return;}" in esc_seg
    # 弹层点击遮罩/关闭钮两条关路
    assert "$('kbd-modal').addEventListener('click',function(e){if(e.target===this)kbdClose();});" in core
    assert "$('kbd-close').addEventListener('click',kbdClose);" in core


def test_feat5_shortcuts_data_covers_existing_bindings() -> None:
    """速查数据是既有键位的可发现层（不新增键位）：全局段覆盖 ⌘K/⌘1-7/g 序列/⌘⇧I/Esc，
    视图段按 SHORTCUTS 组名对齐路由名（文章详情/素材库/视觉编辑）。"""
    core = _core_js()
    for lit in ("⌘K / Ctrl+K", "⌘1-7", "g 然后 s/m/c/p/i/,", "⌘⇧I", "Esc",
                "⌘1-4", "⌘S", "⌘⏎", "← →"):
        assert lit in core, f"速查数据缺既有键位: {lit}"
    # 视图专属段仅在当前路由展示（kPaint 里组名!==全局 && 组名!==cur 即跳过）
    assert "if(g[0]!=='全局'&&g[0]!==cur)return;" in core


def test_feat5_empty_state_guidance_with_actions() -> None:
    """四视图空态从「披露现状」升级为「你可以做什么」+CTA：文案不再裸提 API，
    每个空态至少一个现在就能点的按钮，绑定与头部既有动作同路。"""
    src = _source()
    # 源中枢：不再是「settings.json / POST /api/sources」开发者措辞
    assert "还没有内容源" in src and "注册第一个源" in src
    assert "POST /api/sources 注册" not in src
    assert 'id="src-empty-add"' in src
    # 插件：真空态双 CTA（新建/重扫）
    assert 'id="pg-empty-new"' in src and 'id="pg-empty-rescan"' in src
    # 视觉编辑：空态可直达「载入」
    assert 'id="dv-empty-load"' in src
    # 书籍：搜索占位引导卡配可行动按钮（订阅管理/导入书源）——R4 批文案契约不动
    assert 'id="bk-guide-subs"' in src and 'id="bk-guide-import"' in src
    assert "搜索结果渲染 · R4 批" in src  # 契约锁仍成立（壳静态文案；JS 赋值字面量另锁于 books.js）
    # 绑定与头部动作同路（sources→sfOpen / plugins→hash 与 refresh-btn / design→dvLoad / books→subs tab 与 imOpen）
    vs = _view_js("sources")
    assert "$('src-empty-add').addEventListener('click',sfOpen);" in vs
    vp = _view_js("plugins")
    assert "$('pg-empty-new').addEventListener('click',function(){location.hash='#plugins?new=1';});" in vp
    assert "$('pg-empty-rescan').addEventListener('click',function(){$('pg-refresh-btn').click();});" in vp
    vd = _view_js("design")
    assert "on('dv-empty-load',function(){dvLoad();});" in vd
    vb = _view_js("books")
    assert "$('bk-guide-subs').addEventListener('click',function(){location.hash='#books?tab=subs';});" in vb
    assert "$('bk-guide-import').addEventListener('click',imOpen);" in vb


def test_feat5_skeleton_on_first_visit() -> None:
    """首访骨架屏：通用件 skelCards/skelRows 在 core；material/sources 首访占位画在
    __onViewHtml（material.js 贴 40KiB 水位不加逻辑）；plugins 在 pgEnter 画骨架卡。"""
    core = _core_js()
    assert "function skelCards(n){" in core and "function skelRows(n){" in core
    # material：首访且无数据时画骨架卡（回访有缓存不闪）
    assert "if(view==='material'&&!(state.mats||[]).length){var ml=$('mat-list');if(ml)ml.innerHTML=skelCards(4);}" in core
    # sources：首访无源时表体画行式骨架
    assert "if(view==='sources'&&!matSrcs().length){var sr=$('src-rows');if(sr)sr.innerHTML='<tr><td colspan=\"9\"><div class=\"skel\">'+skelRows(4)+'</div></td></tr>';}" in core
    vp = _view_js("plugins")
    assert "if(!PG.list.length&&!PG.err){$('pg-grid').innerHTML=skelCards(4);$('pg-grid').hidden=false;$('pg-empty').hidden=true;}" in vp
    # material 视图产物未因此增重（骨架逻辑归 core 的判例锚；UIX-3 域=三子段并集）
    assert "skelCards" not in _mat_js()


def test_feat5_dark_mode_live_preview_and_tokens() -> None:
    """暗色补全：live 预览 iframe 双色板（基座+深色覆写，renderLive 按 themeDark 取
    生效态）；切主题由 applyTheme 同步钩子重注；灯箱错误色 token 化；速查/骨架样式
    全 token 无硬编码色。"""
    va = _view_js("article")
    assert "var LIVE_CSS_DARK=" in va
    assert "themeDark()?LIVE_CSS_DARK:''" in va
    core = _core_js()
    assert "function themeDark(){" in core
    # applyTheme 末尾同步 live 预览——经 VAPI 触达（跨视图治理：core 不直呼视图函数）
    assert "if(va&&va.live&&!$('view-article').hidden)va.live();" in core
    assert ",repaint:renderDetail,setTab:setTab,live:renderLive}" in _view_js("article")
    vm = _mat_part("drawer")  # UIX-3：crimson 落 bkOrigToggle（详情抽屉子段）
    assert 'color:var(--crimson)' in vm and "#ff7b89" not in _mat_js()
    css = _css()
    for sel in (".kbd-modal{", ".kbd-row{", ".skel-row{", ".empty-actions{"):
        assert sel in css
    # 新增样式零硬编码色（暗色全覆盖走 token/color-mix(var) 路径；压缩产物无注释，
    # 段落以 .empty-actions{ 为界——FEAT-5 三族规则在 core.css 中连续排列）
    kbd_seg = css[css.index(".kbd-modal{"):css.index(".empty-actions{")]
    assert "#" not in kbd_seg, f"FEAT-5 样式混入硬编码色: {kbd_seg[:120]}"
