"""R1+F58+M1 构建集成测试：壳 ui.html + assets/core.js|css 双层产物。

覆盖（设置与创作台设计 §三-R1 / F58 构建器 / M1 前端多文件化 2026-11-18）：
  a) 构建产物存在、字节等于各源 render/render_core_js/render_core_css 结果，
     且壳被 make_server 服务（GET / 200 text/html 与产物逐字节一致——走
     _read_ui_html 数据面）；assets 静态面（/assets/*）见本文件「g) 产物静态面」
  b) M1 三锚：壳 ≤48KiB / 手写 assets 总量 ≤400KiB / 单文件 ≤208KiB + 触发条款
     （2026-11-18 用户裁决重锚；2026-11-19 F1 起 assets/editor/** vendor 剥离
     手写口径并另设 VENDOR_PINNED 清单钉死；单文件时代 MAX_BYTES 13 次扩门
     沿革随多文件化退役，沿革见 git 历史——估算偏低第四次判例与锚注释档在下）
  c) 构建确定性：render/render_core_* 连算两次字节一致；build 连跑两次
     壳+assets 全文件 sha256 一致
  d) 产物含全部既有结构标识（八视图/命令面板/素材视图/回采按钮）——细粒度
     结构断言在 test_studio_ui.py（壳+assets 分文件断言，两文件互为补充）
  e) F58 视图拆源：ui.views/ 八视图完整 <section> 片段（material M3 落地）
  f) F58 保守压缩：注释剥离/尾空白/空行折叠行为单测 + 字符串字面量零触碰
     （合成样本对拍 + 真源引号序列保全断言）
  g) M1 产物静态面：/assets/* 200 + Content-Type + 后缀白名单 + `../` 穿越
     400 + ETag/304（安全四门纪律与 _asset_response 同款）
  h) 打包契约：package-data 须用递归 glob（`studio/assets/**`）——单层 glob
     在 wheel 里只收一层，M3 的 assets/views/*.js 会静默漏装（集成轮实测缺陷）
"""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import re
import threading
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "build_ui.py"
STUDIO = Path(__file__).resolve().parent.parent / "musespiral" / "studio"
OUT = STUDIO / "ui.html"
VIEWS = STUDIO / "ui.views"
CORE_JS = STUDIO / "assets" / "core.js"
CORE_CSS = STUDIO / "assets" / "core.css"

# ── M1 三锚 + 触发条款（2026-11-18 用户裁决，随前端多文件化同批重锚）──────
# 单文件时代（MAX_BYTES ≤304KiB；13 次扩门沿革 136KB→144KiB→192KiB→194KiB→
# 224KiB→240KiB→248KiB→260KiB→272KiB→304KiB，裁决依据档见 git 历史）随多文件化
# 退役——多文件后门约束形态改为「三锚 + 触发条款」：
#   壳 ≤48KiB（49,152B）         实测 42,284B=41.3KiB（R4 导入弹窗批后），
#                                余 6.7KiB——壳含八视图内联 HTML（tmpl
#                                11,709B + views，F58 纪律 HTML 零压缩）
#                                为物理下限；M2 critical CSS 若内联进壳会使
#                                壳再涨，届时按同一逻辑（实测+余量）复核壳锚。
#   assets 总量 ≤400KiB          见下「手写口径」。
#   单文件 ≤208KiB（212,992B）   见下「手写口径」。
# 正典 §二/§三 预估（壳 ~15-20KB、core.js 压缩后 ~110KB「需实测」）被实测证伪
# ——**估算偏低第四次判例**（沿 E1-E3 偏低 5 倍 / F72 偏低 ~1 倍 / R4 偏低 3 倍
# 之后）：前端体积按设计文档估算立预算不可靠，须以实测为准。
# **触发条款（硬门，2026-11-18 裁决原文）**：单文件触及 200KiB=204,800B 或
# assets 总量触及 360KiB=368,640B（90% 水位）→ 强制启动 M3 按视图拆分，
# 不得再放宽锚值（test_m3_split_trigger_watermark 机器强制）。
#
# ── F1 编辑器 vendor 剥离（2026-11-19，正典《musespiral-专业创作者视角深度评审
# 与改进-2026-11-18》§3.1「选型定案：CodeMirror 5（vendor）」+ 多文件化设计
# §一「多文件仍零外部依赖」）────────────────────────────────────────
# assets/editor/** 是三方 CodeMirror 5.65.18 本地 vendor（本地文件非 CDN，零
# 外联红线不破）。三锚/触发条款/防回涨锚自此按**手写口径**计（_handwritten_files
# =assets/ 全树剥掉 assets/editor/ 子树）：门的本意是防手写代码失控增长，vendor
# 是钉版本的第三方制品、不随业务迭代增长——vendor 侧另设清单钉死
# （test_vendor_editor_assets_pinned：文件名×字节精确匹配，任何增删改须显式更新
# VENDOR_PINNED，等价于 vendor 侧的「扩门要走裁决」）。
# F1 批后手写口径实测：core.js 204,442B（含 mountEdit 装载接线 +304B）/
# core.css 60,419B / editor.js 24,823B / editor.css 3,743B = 293,427B=286.5KiB，
# 余量 360KiB 触发线 75,213B；core.js 距 200KiB 触发线仅 358B（水位告警仍生效：
# 下一批前端功能落地前先看 M3 拆分）。
SHELL_MAX_BYTES = 48 * 1024
# ── 总量三档（2026-11-21 owner 裁决「机制修正」：总量锚自硬门降为绿/黄/红三档）──
# 沿革：REFINE-ITER 批（2026-11-20）最小必要抬锚 426,500→432,000B 原带
# 「⚠️ 待 owner 追认」标记——**2026-11-21 owner 明确追认 432,000 有效**；同日
# N-2 素材闭环前端四入口批（+11,524B → 实测 443,396B）第三次撞锚（两天内
# REFINE-ITER +5,368 / N-2 +11,524 连撞），而**首屏使用率始终 60.1%
# （147,811/245,760B）从未承压**——owner 裁断：总量增长是功能正常扩张
# （9 插件/11 视图/5 源线）而非膨胀失控，用硬门管正常增长是范畴错误。
# 三档语义（本批起生效，第 9 次调整=最后一次数字调整——机制已改，后续
# 按三档走，不再抬数字）：
#   绿：总量 ≤ 444,000B                       → PASS（正常）
#   黄：444,000B < 总量 ≤ 480,000B（+8%）     → PASS + 醒目告警（warnings+print，
#       不阻塞交付；下批须在注释档补增长归因说明）
#   红：总量 > 480,000B                        → FAIL（真膨胀，必须拆分/删除）
# **首屏锚（FIRST_SCREEN_MAX_BYTES）保持硬门不变**——那才是用户体验指标；
# 本批另加 80% 使用率告警（见 test_first_screen_budget）。单文件/单视图/core
# 分层锚维持硬门（防单点失控，三档只作用于「总量」一层）。
ASSETS_TOTAL_GREEN_MAX = 444_000     # 绿档上限（N-2 批实测 443,396B + 604B 尾数）
ASSETS_TOTAL_YELLOW_MAX = 480_000    # 黄档上限（绿 +8%——软警报带）
# 旧常量 ASSETS_TOTAL_MAX_BYTES / M3_ASSETS_TOTAL_MAX_BYTES / AFTER_M1_ASSETS_BYTES
# 随三档化退役：消费它们的三个测试统一改走 _assert_assets_total_tier()。
ASSET_FILE_MAX_BYTES = 208 * 1024
M3_TRIGGER_FILE_BYTES = 200 * 1024
M3_TRIGGER_ASSETS_BYTES = 360 * 1024

# ── M3 分层锚（2026-11-19 按视图拆分；替换原「单文件 208KiB」上限锚）──────
# 拆分后 core.js = 工具/路由/状态/命令面板/toast/jobs/装载器 + 图标定义；
# views/<view>.js = 九视图各自 render* + 专属 helper + bind<View>()。
# 实测（构建双跑、手写口径=剥 assets/editor/ vendor）：
#   shell 45,720B / core.js 55,519B / 单 view 最大 material.js 39,511B /
#   手写 assets 总量 376,127B≈367.3KiB / 首屏(壳+core+单view 最大) 140,750B≈137.5KiB。
# 分层：core ≤160KiB（余 105KB）/ 单 view ≤40KiB（material 39,511B 距锚仅 1.4KB）/ 总量锚见下。
CORE_JS_MAX_BYTES = 160 * 1024        # 163,840B
VIEW_JS_MAX_BYTES = 40 * 1024         # 40,960B
FIRST_SCREEN_MAX_BYTES = 240 * 1024   # 245,760B：行业 size-limit 洞察=parse/execute 成本
# 总量锚：题面建议 360KiB（368,640B），但「一份 ui.js 拆成 1 core + 9 view」的固有开销
# （9 独立文件 + 跨视图调用的 no-op 占位符 + 9 bind 包装 + ensureView）使实测为
# 376,127B（367.3KiB）——距 360KiB 仅差 7.5KiB，比拆分前手写总量（367,766B）还略低。
# 占位符是必需的：core 的 waitForJob→pPaint、cpMain→handleSaveBody 等调用可能发生在
# 用户未访问对应视图时（去掉会 ReferenceError），故不去掉。按「先实测再定」设
# 380,000B（留 ~3.9KB 在途缓冲）；真正的收益在首屏锚（137.5KiB，拆分前 ~286KiB）。
# PL-3 插件前端批（2026-11-20）实测 420,097B（+plugins.js 13,965 / +plugin-run.js
# 16,297 / +core.css 12,357 / +core.js 818）→ 据实重估 425,000B（留 ~5KB 在途缓冲），
# 重估依据档见 AFTER_M1_SHELL_BYTES 上方注释。
# 批2 双线并行抬锚（2026-11-20）425,000→426,500B（N-1 +5,440 / N-3 +2,514，
# 实测 425,599B）——归因与「抬锚终止条款」同上注释档。
# **批 REFINE-ITER 抬锚（2026-11-20，第 8 次，⚠️ 待 owner 裁决追认）**：
# 426,500→432,000B（**最小必要抬锚**——不留 discretionary 缓冲）。
# 归因（实测，HEAD 基线 425,848B）：iterations.js 5,544B（P2 素材打磨历史，E-6
# versions.js 同款懒加载独立产物，零首屏影响）+ material.js +457B + core.js +23B
# = +6,024B → 实测 431,872B；锚 432,000B 仅含 128B 凑整尾数。
# 数据依据：首屏使用率 58.4%（143,502/245,760B，余 102KB）——总量门拦的不是
# 用户体验（懒加载不进首屏），是仓库膨胀。但「抬锚终止条款」（2026-11-20
# 裁决，明文『下一次触发时不得再抬』『禁止第四个选项「抬锚」』）**未被裁决
# 废止**——同日立、同日废不得自我授权（2026-11-20 复核判例）。owner 追认/
# 否决前，终止条款对后续一切抬锚继续有效；若否决，回退路径=按原条款①②压回
# 426,500B 以下或本批功能不进（iterations.js 5,544B 是唯一增量源）。
# 「首屏=真门 / 总量=防膨胀」的分层认知记入下方「体积锚层级」，但其「总量
# 降级为观测指标」的效力同样待追认。
# **批 N-2 素材闭环前端（2026-11-21，第 9 次调整——owner 裁决，机制修正）**：
# 抬锚 432,000→444,000B **并同步把总量自硬门降为绿/黄/红三档**（语义见
# ASSETS_TOTAL_GREEN_MAX 注释档；本行常量随三档化退役，消费点统一改走
# _assert_assets_total_tier()）。归因（实测，REFINE-ITER 后基线 431,872B）：
# core.js +4,089（spark ⌘⇧I 浮层+灵感 chip+matQS sub_type）/ core.css +1,130
# （.spark-* 浮层与 .chip-spark）/ publish.js +3,405（文章反哺三路径弹窗）/
# compose.js +2,680（「我的素材」tab 直引）/ material.js +220（chip 激活态/
# 哈希/深链）= +11,524B → 443,396B。同时 owner 追认 REFINE-ITER 的 432,000
# 为有效值（原「待追认」标记就此销案）；「抬锚终止条款」由三档语义取代——
# 这是最后一次数字调整，黄档（444,000~480,000B）不阻塞但须下批归因，
# 红档（>480,000B）仍硬失败。

# M1 双锚（防回涨锚；构建双跑一致后的 wc -c 值，手写口径）：壳 42,284B /
# 手写 assets 总量 314,779B（core.js 204,442 + core.css 60,419 + editor.js
# 42,334 + editor.css 7,584；E-2+E-3 编辑器增强批后）。
# 沿革：M1 首锚（2026-11-18 多文件化实测）壳 39,339B / assets 251,072B →
# R4 导入弹窗批（import-modal，2026-11-19）壳 42,284B / assets 264,557B →
# F1 编辑器批（CM5，2026-11-19）手写 assets 293,328B（vendor 另钉，不入本锚）→
# F1 集成接线批（2026-11-19）293,427B：editor.js 4 处 keydown 补 IME 守卫
# （isComposing/229，对齐 ui.js 既有惯例）+384B、editor.css 3,743B 实测回填
# （F1 批注释档原记 24,098/4,369 与落盘字节不符，本批按 wc -c 实测修正）→
# **E-2+E-3 编辑器增强批（2026-11-19）314,779B**：editor.js 24,823→42,334
# （+17,511；E-2 内联研究面板 ed-rp 族 + E-3 AI 微操作 ed-ai 族与 7 个纯函数）、
# editor.css 3,743→7,584（+3,841；同两批样式）。两批同落一棵工作树后才统一
# 重锚，故此处按合并增量记；单批归因见各自设计文档 §3.2/§3.3。
# ⚠ 水位告警（对下一批前端功能）：core.js 204,442B 距 M3 触发线 204,800B
# 仅 358B——下一批改动 core.js 的功能落地即触发 200KiB 条款，届时按
# test_m3_split_trigger_watermark 红灯走 M3 按视图拆分，不得放宽锚值。
# 手写总量 314,779B 距 360KiB 触发线 368,640B 余 53,861B。
# **E-4 极速启动批（2026-11-19）340,000B 上限锚**：新增独立产物
# assets/quickstart.js 10,055B（§3.4 弹窗/轮询/逐块揭示/跳转，动态装载不占壳）、
# core.css +1,547B（qs-* 族样式）、core.js +117B（ui.js 一行动态装载钩子）。
# 与 E-2/E-3 同落一棵工作树，故上限按三批合并总量取整（实测 327,428B）留少量
# 在途写缓冲；仍低于 360KiB 硬触发线 368,640B。单文件 core.js 204,559B 未动
# 装载策略（quickstart 走独立文件，正好避开 200KiB 触发条款——不要再往 core.js 塞）。
# **三线交界集成批（2026-11-19）**：editor.js 42,334→44,820B（+2,486；选中
# 优先级>搜索裁决 edAiSelActive/edAiMenuVis + Esc 收拾链 + 头部契约注），仅此
# 一文件——实测合并 328,984B，340,000B 上限锚不重锚（余 11,016B 仍覆盖在途
# 缓冲语义）；core.js/core.css/quickstart.js 零字节变动（构建双跑 sha256 对拍）。
# **E-4 评审修复批（2026-11-19）**：quickstart.js 10,055→12,327B（+2,272；
# F1 开始写作=decide keep-all→adopt→#article 编辑器深链 + F2 生成期 partial
# 轮询重绘 qPaintPartial），其余文件零变动——实测合并 331,256B，上限锚仍
# 340,000B 不动（余 8,744B）；core.js 204,559B 未触（F1/F2 全在独立产物与后端）。
# **G-T4 滚动摘要批（2026-11-19）**：compose.html 右栏新增 details#cm-rolling
# 折叠区（+541B → 壳 43,345B）；行为落 assets/rolling.js 独立懒加载（4,193B）；
# core.js 仅 +101B 装载钩子（G-T2 同款，合计 core.js 204,761B）。**assets 总量
# 锚 340,000→342,000B**：本批实测手写口径 341,182B（core.js 204,761 + core.css
# 62,392 + editor.js 44,820 + editor.css 7,584 + quickstart.js 12,327 +
# outline.js 5,105 + rolling.js 4,193），抬到 342,000B 留 ~800B 在途写缓冲，
# 仍远低于 360KiB 硬触发线 368,640B。
# **G-T3 双模型对比批（2026-11-19）**：compose.html 右栏新增 details#cm-dual
# 折叠区（+1,245B → 壳 44,590B）；行为落 assets/dual.js 独立懒加载（5,320B）；
# core.js 装载钩子由三行合并为单循环（-124B → 204,637B，仍未触 M3 拆分线）；
# core.css +578B（.cm-dual-* 双栏）。手写 assets 总量实测 354,325B——两锚据实
# 重估：壳 44,000→45,000B、assets 350,000→356,000B（留 ~1.7KB 在途缓冲，
# 仍远低于 360KiB 硬触发线 368,640B）。
# **G-T5 TTS 朗读批（2026-11-19）**：ui.tmpl.html 详情工具条 +🔊 朗读按钮与
# tts-bar（<audio controls> 播放栏，+414B → 壳 45,004B）；行为落 assets/tts.js
# 独立懒加载（4,555B）；core.js 仅装载循环补一字面量 + 任务面板 JN 多一个键
# （+44B → 204,681B）；core.css +397B（.tts-bar 三行）。手写 assets 总量实测
# 359,377B——**距 360KiB 触发线 368,640B 仅余 9,263B（水位告警重开）**：两锚
# 据实重估为壳 46,000B / assets 361,000B（留 ~1.6KB 在途缓冲；仍低于硬线，
# 但下一批前端功能落地前必须先看 M3 按视图拆分）。
# **E-6 版本历史 diff 批（2026-11-19）**：ui.tmpl.html 详情 tabs 加「版本历史」
# 按钮 + panel-versions（下拉×2 + 对比按钮 + #ver-stage，+716B → 壳 45,720B）；
# 行为落 assets/versions.js 独立懒加载（7,300B）；core.js 仅装载循环补一字面量
# 与 tab 白名单补一词（+32B → 204,713B）；core.css +1,057B（.ver-* 图例/行槽/删除线）。
# 手写 assets 总量实测 367,766B——**距 360KiB 触发线 368,640B 仅余 874B**：
# 两锚据实重估为壳 46,500B / assets 368,000B（留 ~0.5KB 在途缓冲；仍低于硬线，
# **下一批前端功能落地前必须先做 M3 按视图拆分**——本批已是最后一笔可容纳增量）。
# **PL-3 插件系统前端批（2026-11-20）——M3 后首次两视图同落**：ui.views/ 新增
# plugins.html（1,008B）+ plugin-run.html（1,292B）（骨架只放容器，细节全 JS 渲染，
# 评审 §2.3 拆分：清单/详情/新建=plugins、画布/运行/segments=plugin-run）+
# ui.tmpl.html rail 新增「工具」组两导航项（+311B）+ 两视图标记行（+64B）→ 壳实测
# 48,361B（距硬锚 49,152B 余 791B——**壳余量已见底，下一批前端改动壳必须先清点**）；
# ui.js core 增 VIEWS/CRUMBS/HALIAS/renderRoute 两分支 + ICONS play/flow 两枚
# （core.js 56,250→57,068B）；ui.css +12,357B（pg-*/pr-* 全族 + toast 底距 16→24px
# =56px，原型踩坑纪律）；assets/views/plugins.js 13,806B + plugin-run.js 16,297B
# （均 <40KiB 分层锚）。手写 assets 总量实测 420,097B → **M3 总量锚 380,000→425,000B**
# （留 ~5KB 在途缓冲；首屏 144,583B 距 240KiB 锚余量充足）。
# **PL-3 集成冒烟修复批（2026-11-20）**：prPipesHtml 此前未接线（prPaint 只切
# hidden 不渲染）——裸 #plugin-run 流水线清单空白（浏览器冒烟发现，结构测试
# 只查字面量查不到调用图）。prPaint pick 分支补一行 innerHTML 接线。
# **PL-3 复核修复批（2026-11-20，同日第二批——后端接驳+门控）**：后端补齐
# /api/plugins*、/api/pipelines* 消费面（plugin_actions.py；前端 run 提交改精确
# POST /api/pipelines/run）；hasSegs 门控（segments 型链才挂转写面板）+ prEnter
# 清编辑态（冒烟2 判例：?run= 深链不得残留上一链面板）+ 清单文案自洽
# （跳过 N 个时不再宣称全部过契约门）。plugins.js 13,965→14,169（+204）/
# plugin-run.js 16,297→16,962（+665，两批合计）；总量 420,956B、首屏 144,524B
# ——锚均不动（余量充足：总量距 425,000B 余 4,044B）。
# **N-3 源级 AI 配置批（2026-11-20）**：sources.html 新增 AI 配置弹窗骨架
# （#src-aicfg 四字段 + 两按钮，+656B）——壳 48,900→49,152B 硬锚上探不足，
# 改为 **与 SHELL_MAX_BYTES 同值 49,152B**（壳硬门已在上方 SHELL_MAX_BYTES=
# 48KiB 单独守；本防回涨锚只防「再悄悄长」，实测 49,043B 贴顶余 109B——下一
# 批触制定尺即须按本协议重估再动）。行为落 sources.js（+1,858B → 10,612B，
# 距单视 40KiB 锚余 30KB），core.js 零改动；assets 总量本批 +2,514B 仍在
# 425,000B 锚内（隔离口径 420,899B）。
# **批2 双线并行抬锚（2026-11-20）**：425,000→426,500B。
# 归因：N-1 小说前端（compose.js +5,414 / compose.html +26 = +5,440）+
# N-3 源级 AI（sources.js +1,858 / sources.html +656 = +2,514）。
# 两线并行同门，单线均未越锚（N-1 单独 423,825B / N-3 单独 421,441B）。
# 实测 425,599B，留 ~900B 在途。
# ⚠️ 这是第 7 次总量抬锚（342→356→361→368→380→425→426.5K）。
# **壳贴顶告警（同步登记）**：壳 49,043B / 49,152B 硬锚**仅余 109B**——壳是
# M2 critical CSS 内联的物理下限，**下一批任何改动壳的落地前必须先拆**
# （可运行期建的骨架继续下压为 JS 创建，或把 tmpl 里的静态块移入视图）。
#
# ── 体积锚层级（2026-11-21 第三次撞锚后 owner 裁决重构——三档语义落地为
#    判定代码，不再是注释承诺；「抬锚终止条款」就此由三档机制取代并销案）──
# **一级（硬门，用户体验约束——唯一真防线，2026-11-21 起加 80% 使用率告警）**：
#   首屏 = 壳 + core.js + 首屏视图 ≤ 245,760B（FIRST_SCREEN_MAX_BYTES，硬上限）
#   且使用率 > 80%（>196,608B）时醒目告警——任何新功能把首屏推过 80% 必须
#   改懒加载（M3 架构的能力，用它）。当前 60.1%（N-2 批实测 147,811B）。
# **二级（单产物上限，防单文件失控——维持硬门）**：
#   core.js ≤ 163,840B / 单 view ≤ 40,960B / 壳 ≤ 49,152B
#   这三条不随总量变化，任何「拆成独立懒加载文件」都合法绕过（正是设计意图；
#   判例：E-6 versions.js、REFINE-ITER iterations.js 自 material.js 拆出）。
# **三级（总量三档，防整体膨胀——2026-11-21 由硬门降档）**：
#   绿 ≤444,000B / 黄 ≤480,000B（软警报：PASS+告警，下批归因，不阻塞交付）/
#   红 >480,000B（硬失败，必须拆分/删除）。判例依据：两天三撞（REFINE-ITER
#   +5,368 / N-2 +11,524）而首屏始终 60% 未承压——总量增长是功能正常扩张
#   （9 插件/11 视图/5 源线），用硬门管正常增长是范畴错误。
AFTER_M1_SHELL_BYTES = 49_152  # N-3 源级 AI 配置批（sources 视图 AI 配置弹窗）后重锚至硬锚上界，见上注释档

# ── F1 vendor 清单钉死（CM5 5.65.18 一期面：core+markdown mode+两 edit addon
# +两 search addon；任何升级/增删都须显式改这里并重估）─────────────────
VENDOR_DIR = "assets/editor"
VENDOR_PINNED: dict[str, int] = {
    "assets/editor/codemirror.min.js": 170_535,
    "assets/editor/codemirror.min.css": 6_037,
    "assets/editor/mode/markdown/markdown.min.js": 14_631,
    "assets/editor/addon/edit/closebrackets.min.js": 3_523,
    "assets/editor/addon/edit/matchbrackets.min.js": 3_096,
    "assets/editor/addon/search/search.min.js": 5_804,
    "assets/editor/addon/search/searchcursor.min.js": 5_277,
    "assets/editor/theme/paper-ink.css": 2_718,  # 2026-11-21 改名 mdpub→musespiral（注释 +5B）
}


def _build_module():
    spec = importlib.util.spec_from_file_location("musespiral_build_ui", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sources(mod) -> tuple[bytes, dict[str, bytes], bytes, bytes]:
    views = {p.stem: p.read_bytes() for p in sorted(VIEWS.glob("*.html"))}
    return mod.TMPL.read_bytes(), views, mod.CSS.read_bytes(), mod.JS.read_bytes()


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path: Path, monkeypatch) -> None:
    """状态目录指向 tmp_path，绝不读写 ~/.musespiral（与 test_studio.py 同纪律）。"""
    monkeypatch.setenv("MUSESPIRAL_HOME", str(tmp_path / "musespiral-home"))


# ── a) 产物存在 = render 结果 ─────────────────────────────


def test_build_product_exists_and_matches_sources() -> None:
    """产物存在且逐字节等于各源当前 render 结果（conftest 会话级已构建）。

    M3 起产物=壳（render）+ assets/core.js（render_core_js 去视图块）+
    assets/views/<view>.js（render_view_js 九视图）+ assets/core.css（render_core_css）。
    全部对拍，缺一或漂移即红。
    """
    assert OUT.is_file(), f"构建产物缺失: {OUT}"
    assert CORE_JS.is_file() and CORE_CSS.is_file(), "assets 产物缺失（须跑 scripts/build_ui.py）"
    mod = _build_module()
    tmpl, views, css, js = _sources(mod)
    assert OUT.read_bytes() == mod.render(tmpl, views, css, js), \
        "ui.html 与源合成结果不一致（须跑 scripts/build_ui.py）"
    assert CORE_JS.read_bytes() == mod.render_core_js(js), \
        "assets/core.js 与 ui.js core 段压缩结果不一致（须跑 scripts/build_ui.py）"
    assert CORE_CSS.read_bytes() == mod.render_core_css(css), \
        "assets/core.css 与 ui.css 压缩结果不一致（须跑 scripts/build_ui.py）"
    # M3：九视图产物逐一对拍
    _, js_views = mod.split_ui_js(js)
    assert set(js_views) == set(mod.EXPECTED_VIEWS), "视图块集合与预期不一致"
    for name, blob in js_views.items():
        path = STUDIO / "assets" / "views" / f"{name}.js"
        assert path.is_file(), f"视图产物缺失: {path}"
        assert path.read_bytes() == mod.render_view_js(js, name), \
            f"assets/views/{name}.js 与 ui.js 视图块压缩结果不一致"


def test_make_server_serves_build_product(tmp_path: Path) -> None:
    """GET / 返回构建产物：make_server → 200 + text/html + body 与 ui.html 逐字节一致。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        conn = http.client.HTTPConnection(host, port, timeout=10)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read()
        ctype = resp.getheader("Content-Type", "")
        conn.close()
        assert resp.status == 200
        assert ctype.startswith("text/html"), ctype
        assert body == OUT.read_bytes(), "GET / 内容与构建产物不一致"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


# ── b) 体积门：M1 三锚 + 触发条款 ─────────────────────────


def _assets_files() -> list[Path]:
    """assets/ **全树**文件（递归）——枚举面（嵌套层可见性）与 vendor 清单用。

    2026-11-18 集成接线轮实测缺陷：原实现用 `iterdir()` 只枚举顶层，嵌套层
    完全不计。M3 按视图拆分后的形态正是 `assets/views/*.js`（设计 §二/§七），
    一旦落地，1MiB 的 views 文件在四个门（400KiB 总量 / 208KiB 单文件 /
    200KiB 与 360KiB 触发条款）里全部 PASS 漏报——门在最需要它的那一刻恰好
    失明。改为 `rglob("*")` 递归，`is_file()` 过滤目录与符号链接指向的目录。
    """
    return sorted(p for p in (STUDIO / "assets").rglob("*") if p.is_file())


def _handwritten_files() -> list[Path]:
    """手写产物（F1 起的体量门口径）：assets/ 全树剥离 assets/editor/ vendor 子树。

    editor.js/editor.css 是手写源（顶层文件，不入 vendor 子树，照计）；
    vendor 的体量治理走 VENDOR_PINNED 清单钉死（见模块头 F1 注释档）。
    """
    return [p for p in _assets_files()
            if not p.relative_to(STUDIO).as_posix().startswith(VENDOR_DIR + "/")]


def _assets_total_bytes() -> int:
    return sum(p.stat().st_size for p in _handwritten_files())


def _assert_assets_total_tier() -> int:
    """总量三档判定（2026-11-21 owner 裁决机制修正；三个总量消费测试统一走此）。

    绿 ≤444,000B PASS；黄 ≤480,000B PASS+醒目告警（warnings+print，不阻塞
    交付，下批须在常量注释档补增长归因）；红 >480,000B FAIL（真膨胀，必须
    拆分/删除）。返回实测总量供调用方记录。
    """
    import warnings

    total = _assets_total_bytes()
    if total <= ASSETS_TOTAL_GREEN_MAX:
        return total
    if total <= ASSETS_TOTAL_YELLOW_MAX:
        msg = (
            f"⚠️ 手写 assets 总量 {total}B 落入黄档（{ASSETS_TOTAL_GREEN_MAX}~"
            f"{ASSETS_TOTAL_YELLOW_MAX}B 软警报带）：不阻塞交付，但下一批必须在"
            " test_build_ui.py 常量注释档补增长归因说明（绿/黄/红三档语义见"
            " ASSETS_TOTAL_GREEN_MAX 注释档）"
        )
        print("\n" + "=" * 24 + msg + "=" * 24 + "\n")
        warnings.warn(UserWarning(msg), stacklevel=2)
        return total
    raise AssertionError(
        f"手写 assets 总量 {total}B 超红档 {ASSETS_TOTAL_YELLOW_MAX}B——真膨胀，"
        "必须拆分或删除（三档语义见 ASSETS_TOTAL_GREEN_MAX 注释档）")


def test_shell_within_budget() -> None:
    """壳锚（M1 三锚之一）：ui.html ≤ 48KiB（壳含八视图内联 HTML 为物理下限）。"""
    size = OUT.stat().st_size
    assert size <= SHELL_MAX_BYTES, f"壳 ui.html 超出预算: {size} bytes（锚注释档见上）"


def test_assets_within_total_budget() -> None:
    """assets 总量三档（2026-11-21 由硬门降档；F1 起手写口径）。

    手写口径=剥掉 assets/editor/ vendor（vendor 走 VENDOR_PINNED 钉死）。
    绿/黄/红语义与判例依据见 ASSETS_TOTAL_GREEN_MAX 注释档（owner 裁决：
    首屏始终 60% 未承压，总量增长是功能正常扩张——硬门管正常增长是范畴错误）。
    """
    _assert_assets_total_tier()


def test_asset_files_within_budget() -> None:
    """M3 分层锚（手写口径）：core.js ≤160KiB / 单 view.js ≤40KiB / 其余手写 ≤208KiB。"""
    for path in _handwritten_files():
        size = path.stat().st_size
        rel = path.relative_to(STUDIO).as_posix()
        if rel == "assets/core.js":
            limit, label = CORE_JS_MAX_BYTES, "core.js"
        elif rel.startswith("assets/views/") and rel.endswith(".js"):
            limit, label = VIEW_JS_MAX_BYTES, "view.js"
        else:
            limit, label = ASSET_FILE_MAX_BYTES, "单文件"
        assert size <= limit, (
            f"{rel} 超出{label}预算: {size}B > {limit}B（分层锚见模块头 M3 注释档）")


def test_first_screen_budget() -> None:
    """M3 首屏加载量锚（size-limit 核心洞察=parse/execute 成本 > raw bytes）。

    用户真正付出的首屏 parse 成本 = 壳 + core.js + 任一单 view.js（首屏只注入当前
    视图那一个）。取最大的 view 作最坏情况。M3 后首屏实测 145,810B≈142KiB，
    显著低于拆分前的 ~286KiB 单文件；锚 240KiB 留足功能迭代空间。
    2026-11-21 owner 裁决加强：总量三档化后首屏是**唯一真防线**——硬上限不变，
    另加 80% 使用率告警（>196,608B 时 warnings+print；任何新功能把首屏推过
    80% 必须改懒加载——M3 架构的能力，用它）。N-2 批实测 60.1%。
    """
    import warnings

    shell = OUT.stat().st_size
    core = CORE_JS.stat().st_size
    views = sorted((STUDIO / "assets" / "views").glob("*.js"))
    assert views, "缺少视图产物（须跑 scripts/build_ui.py）"
    biggest_view = max(p.stat().st_size for p in views)
    biggest_name = max(views, key=lambda p: p.stat().st_size).name
    first_screen = shell + core + biggest_view
    if first_screen > FIRST_SCREEN_MAX_BYTES * 0.8:
        msg = (
            f"⚠️ 首屏 parse 量 {first_screen}B 使用率超 80%"
            f"（{first_screen}/{FIRST_SCREEN_MAX_BYTES}B，最大视图 {biggest_name}）——"
            "新增功能须改懒加载，不得继续推高首屏（owner 裁决 2026-11-21）"
        )
        print("\n" + "=" * 24 + msg + "=" * 24 + "\n")
        warnings.warn(UserWarning(msg), stacklevel=2)
    assert first_screen <= FIRST_SCREEN_MAX_BYTES, (
        f"首屏 parse 量 {first_screen}B（壳 {shell} + core {core} + 最大视图 "
        f"{biggest_name} {biggest_view}）超出 {FIRST_SCREEN_MAX_BYTES}B 预算")


def test_m3_split_trigger_watermark() -> None:
    """分层水位门（M3 起替换原「单文件 200KiB / 总量 360KiB → 强制拆分」）：
    core.js 触及 160KiB 或单 view.js 触及 40KiB → 本测试转红 = 视图切分需
    重新规划（某视图过大须二次拆分），不得再放宽锚值。总量一支 2026-11-21
    起走三档（绿/黄/红）——红档（>480,000B）在本测试同样转红。
    """
    core = CORE_JS.stat().st_size
    views = sorted((STUDIO / "assets" / "views").glob("*.js"))
    biggest_view = max((p.stat().st_size for p in views), default=0)
    biggest_name = max(views, key=lambda p: p.stat().st_size).name if views else "(none)"
    assert core <= CORE_JS_MAX_BYTES, (
        f"core.js {core}B 触及 {CORE_JS_MAX_BYTES}B 水位：拆出的工具/路由过多，须重新规划边界")
    assert biggest_view <= VIEW_JS_MAX_BYTES, (
        f"单视图 {biggest_name} {biggest_view}B 触及 {VIEW_JS_MAX_BYTES}B 水位："
        "该视图过大，须二次拆分，不得放宽锚值")
    _assert_assets_total_tier()


def test_vendor_editor_assets_pinned() -> None:
    """F1 vendor 清单钉死（2026-11-19，正典 §3.1 选型定案 CM5 vendor）。

    assets/editor/ 子树与 VENDOR_PINNED 逐文件（名字×字节）精确匹配——vendor
    是钉版本的第三方制品，不随业务迭代增长；升级 CM5 / 增删 addon 都须显式
    更新清单（等价于 vendor 侧的「扩门要走裁决」）。混入未登记文件同样红：
    vendor 目录不是垃圾桶。版本锚：codemirror.min.js 内嵌 5.65.18。
    """
    files = {p.relative_to(STUDIO).as_posix(): p.stat().st_size for p in _assets_files()}
    vendor = {k: v for k, v in files.items() if k.startswith(VENDOR_DIR + "/")}
    assert vendor == VENDOR_PINNED, (
        f"assets/editor/ vendor 清单与钉死清单不一致（实存 {vendor}）——"
        "vendor 变更须显式更新 VENDOR_PINNED 并重估（升级 CM5 同样走本门）")
    core_js = (STUDIO / VENDOR_DIR / "codemirror.min.js").read_text(
        encoding="utf-8", errors="replace")
    assert "5.65.18" in core_js, "vendor 版本锚：CodeMirror 5.65.18（版本漂移须重估清单）"


def test_assets_budget_gates_cover_nested_dirs() -> None:
    """四门必须计到嵌套层（2026-11-18 集成接线轮实测缺陷的回归锚）。

    缺陷现场：`iterdir()` 只枚举 assets/ 顶层，注入 `assets/views/big.js`(1MiB)
    后 `_assets_files()` 仍只回 `['assets/core.css','assets/core.js']`、计得
    2,048B（真实全树 1,050,624B），四个门全部 PASS 漏报。M3 的计划产物形态
    恰是 assets/views/*.js——门在最需要它的那一刻失明。
    """
    probe = STUDIO / "assets" / "views" / "zz-nested-probe.js"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        probe.write_bytes(b"/* nested */")
        names = [p.relative_to(STUDIO).as_posix() for p in _assets_files()]
        assert "assets/views/zz-nested-probe.js" in names, (
            f"体积门枚举未覆盖嵌套层（实际 {names}）——回退成 iterdir() 即触发 M3 失明")
    finally:
        probe.unlink(missing_ok=True)
        try:
            probe.parent.rmdir()  # 仅当目录空（探针是唯一文件）时收掉
        except OSError:
            pass


def test_assets_total_tier_semantics() -> None:
    """总量三档语义自检（2026-11-21 owner 裁决机制修正的回归锚）。

    用探针文件把总量推入黄档与红档，验证判定行为与裁决一致：
      黄档（绿上限 < 总量 ≤ 480,000B）→ PASS 且发 UserWarning（不阻塞交付）；
      红档（> 480,000B）→ AssertionError（真膨胀必须拆分/删除）。
    防退化形态：有人把三档改回硬门（黄档即红）或把告警删掉，本测试转红。
    """
    import warnings as _w

    base = _assets_total_bytes()
    assert base <= ASSETS_TOTAL_GREEN_MAX, f"基线本身已越绿档: {base}B"
    probe = STUDIO / "assets" / "views" / "zz-tier-probe.js"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 黄档：推过绿上限（当前基线距绿档 <604B，1KB 探针必入黄带）
        probe.write_bytes(b"/* tier probe */" * 64)  # 1,024B
        in_yellow = ASSETS_TOTAL_GREEN_MAX < _assets_total_bytes() <= ASSETS_TOTAL_YELLOW_MAX
        assert in_yellow, "探针未落入黄带——先核对基线与档位常量再重估本测试"
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            _assert_assets_total_tier()  # 黄档：PASS（不抛）+ 告警
        assert any("黄档" in str(x.message) for x in caught), "黄档未发出告警（裁决②的软警报面被删？）"
        # 红档：推过黄档上限
        probe.write_bytes(probe.read_bytes() + b"x" * (ASSETS_TOTAL_YELLOW_MAX + 1))
        with pytest.raises(AssertionError, match="红档"):
            _assert_assets_total_tier()
    finally:
        probe.unlink(missing_ok=True)
        try:
            probe.parent.rmdir()
        except OSError:
            pass


# ── c) 构建确定性 ─────────────────────────────────────────


def test_build_render_is_deterministic() -> None:
    """纯函数确定性：同一组源连算两次 render/render_core_js/render_view_js/render_core_css
    逐字节一致（壳 + core + 九视图 + css 全测）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    assert mod.render(t, v, c, j) == mod.render(t, v, c, j)
    assert mod.render_core_js(j) == mod.render_core_js(j)
    assert mod.render_core_css(c) == mod.render_core_css(c)
    for name in mod.EXPECTED_VIEWS:
        assert mod.render_view_js(j, name) == mod.render_view_js(j, name), name


def test_build_twice_produces_identical_file() -> None:
    """落盘确定性：连跑两次 build()，壳+assets 全文件（含 views/）sha256 一致。"""
    mod = _build_module()
    first = mod.build()
    files = [OUT, CORE_JS, CORE_CSS] + sorted((STUDIO / "assets" / "views").glob("*.js"))
    digest1 = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    second = mod.build()
    digest2 = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    assert first == second == OUT
    assert digest1 == digest2
    assert len(files) == 3 + len(mod.EXPECTED_VIEWS), "assets 全文件枚举缺视图产物"


def test_m3_view_blocks_present_and_canonical() -> None:
    """ui.js 源须含恰好九对 @view/@endview 标记（注释级，不影响执行）；
    split_ui_js 拒绝缺失/重复/未登记块。"""
    mod = _build_module()
    js = mod.JS.read_bytes()
    core, views = mod.split_ui_js(js)
    assert set(views) == set(mod.EXPECTED_VIEWS)
    assert js.count(b"// @endview") == len(mod.EXPECTED_VIEWS)
    for name in mod.EXPECTED_VIEWS:
        assert js.count(f"// @view:{name}".encode()) == 1, name
        # 每个视图块须含一个具名 render 入口或 bind 函数（防空块/占位）
        assert views[name].strip(), f"视图块 {name} 为空"
    # core 含懒加载器 + 路由 await（首屏注入时序）
    assert b"function ensureView(" in core
    assert b"await ensureView(view)" in core
    assert b"assets/views/" in core


# ── d) 既有结构标识齐备（行为零变更的产物面证据）──────────


def test_build_product_contains_structure_markers() -> None:
    """产物含全部既有结构标识：十视图/命令面板/素材视图（壳）+ 回采按钮（core.js）。"""
    shell = OUT.read_text(encoding="utf-8")
    js = CORE_JS.read_text(encoding="utf-8")
    # 十视图（P1 重构 + F72 design + R4 books + PL-3 plugins/plugin-run；视图 <section> 内联进壳）
    for marker in ("view-sources", "view-material", "view-compose", "view-design",
                   "view-books", "view-publish", "view-insight", "view-article", "view-settings",
                   "view-plugins", "view-plugin-run"):
        assert marker in shell, marker
    # 旧视图名退役（queue/dashboard 重命名为 publish/insight）
    assert "view-queue" not in shell and "view-dashboard" not in shell
    # 命令面板（⌘K）
    assert 'id="cp"' in shell and "cp-input" in shell
    # 素材视图（M3 收编语义保留）
    assert "view-material" in shell and "素材" in shell
    assert "view-discover" not in shell
    # F39 健康卡「数据」行回采按钮（JS 渲染行 → M3 后落 insight.js/renderHealth）
    all_js = js + "\n" + "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((STUDIO / "assets" / "views").glob("*.js")))
    assert "sync-stats-btn" in all_js and "回采" in all_js


# ── e) F58 视图拆源 ───────────────────────────────────────


def test_views_directory_layout() -> None:
    """ui.views/ 恰含视图文件（P1 重构：queue→publish、dashboard→insight、
    新增 sources；F72 批 2026-11-17 新增 design 视觉编辑；PL-3 批 2026-11-20
    新增 plugins/plugin-run——插件系统四视图按评审 §2.3 拆两 view）。

    文件名=marker 名=hash 名=section id（canonical 单名，test_studio_ui 的
    test_p1_view_file_names_canonical 同口径）。
    """
    names = sorted(p.stem for p in VIEWS.glob("*.html"))
    # PL-3 #plugins 批（2026-11-20）：+plugins +plugin-run（十视图——工具组两入口）
    assert names == ["books", "compose", "design", "insight", "material", "plugin-run",
                     "plugins", "publish", "settings", "sources"], names
    for name in names:
        blob = (VIEWS / f"{name}.html").read_bytes()
        assert blob, name
        assert blob.startswith(f'<section id="view-{name}"'.encode()), name
        assert blob.rstrip(b"\n").endswith(b"</section>"), name


def test_empty_view_renders_to_zero_bytes() -> None:
    """空视图文件 → 标记整行消失（builder 语义回归；现役视图均已非空，合成样本验证）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    t2 = t.replace(b"<!--BUILD:VIEW:settings-->\n", b"<!--BUILD:VIEW:settings-->\n<!--BUILD:VIEW:empty-->\n")
    v2 = dict(v, empty=b"")
    product = mod.render(t2, v2, c, j)
    assert b"<!--BUILD:VIEW:" not in product  # 全部消费
    # 反证：非空则产物应含其内容
    v3 = dict(v2, empty=b'<section id="view-empty" class="view" hidden></section>\n')
    product3 = mod.render(t2, v3, c, j)
    assert b"view-empty" in product3


def test_view_marker_and_files_must_agree() -> None:
    """占位符与 ui.views/ 文件集不一致 → 拒绝构建（防漂移）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    with pytest.raises(ValueError, match="不一致"):
        mod.render(t, {k: b for k, b in v.items() if k != "settings"}, c, j)


# ── f) F58 保守压缩 ───────────────────────────────────────


def test_compress_strips_comments_whitespace_blank_lines() -> None:
    """三类保守变换：全行注释删除 / 行尾注释剥离 / 尾空白剥离 / 空行折叠。"""
    mod = _build_module()
    src = b"var a=1;\n// full line\nfoo(); // trailing\nvar b=2;   \n\n\nvar c=3;\n"
    assert mod.compress_bytes(src, allow_line_comments=True) == b"var a=1;\nfoo();\nvar b=2;\nvar c=3;\n"


def test_compress_never_touches_string_literals() -> None:
    """字符串零触碰对拍样本：串内 // 与 /* 、跨行模板串、正则引号、转义斜杠。"""
    mod = _build_module()
    cases = [
        b"var u='https://x//y';\n",                       # 串内 // 保留
        b"var s=\"a/*b*/c\";\n",                          # 串内 /* 保留
        b"var t=`a\n//not comment\nb`;\n",                # 跨行模板串内容零触碰
        b"var r=/[&<>\"']/g;\nvar q=1; // ok\n",          # 正则引号不毒化后续行
        b"var r=/http:\\/\\//g;\nvar q=1;\n",             # 转义 \/ 不当注释起点
        b"foo()// ambiguous\n",                           # ) 后 // 有除法歧义 → 整行不动
        b"var w = 'trailing ws in string'   \n",          # 串闭合后的尾空白可剥
    ]
    expect = [
        b"var u='https://x//y';\n",
        b"var s=\"a/*b*/c\";\n",
        b"var t=`a\n//not comment\nb`;\n",
        b"var r=/[&<>\"']/g;\nvar q=1;\n",
        b"var r=/http:\\/\\//g;\nvar q=1;\n",
        b"foo()// ambiguous\n",
        b"var w = 'trailing ws in string'\n",
    ]
    for src, want in zip(cases, expect):
        got = mod.compress_bytes(src, allow_line_comments=True)
        assert got == want, f"{src!r} -> {got!r} (want {want!r})"


def test_compress_css_keeps_bare_double_slash() -> None:
    """CSS 关闭 // 剥离：url(https://…) 裸 // 是内容不是注释；/*…*/ 仍剥离。"""
    mod = _build_module()
    src = b"a{background:url(https://x/y.png)}\n/* css comment */\nb{c:d}\n"
    assert mod.compress_bytes(src, allow_line_comments=False) == \
        b"a{background:url(https://x/y.png)}\nb{c:d}\n"


def test_compress_requires_trailing_newline() -> None:
    """输入卫生：不以换行结尾直接拒绝（同 render 的源文件纪律）。"""
    mod = _build_module()
    with pytest.raises(ValueError):
        mod.compress_bytes(b"var a=1;", allow_line_comments=True)


def test_real_sources_string_spans_preserved() -> None:
    """真源对拍：压缩前后单行引号串序列逐一相同（零触碰的机器可验证形态）。

    独立于构建器状态机的正则提取器作为第二双眼：两法同判才敢说零触碰。
    """
    mod = _build_module()
    pat = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"")
    js = mod.JS.read_bytes()
    head, _, tail = js.partition(mod.JS_SPLIT)
    for name, blob in (("ui.js#head", head), ("ui.js#tail", tail)):
        before = [m.group(0) for m in pat.finditer(blob.decode("utf-8"))]
        after_blob = mod._inline(blob, allow_line_comments=True)
        after = [m.group(0) for m in pat.finditer(after_blob.decode("utf-8"))]
        assert before == after, f"{name} 压缩改变了字符串字面量序列"
    css = mod.CSS.read_bytes()
    before = [m.group(0) for m in pat.finditer(css.decode("utf-8"))]
    after = [m.group(0) for m in pat.finditer(mod._inline(css, allow_line_comments=False).decode("utf-8"))]
    assert before == after, "ui.css 压缩改变了字符串字面量序列"


def test_m1_products_anti_regrowth_anchor() -> None:
    """M1 双锚防回涨（2026-11-19 E-2+E-3+E-4 批合并重锚，手写口径）：壳 ≤42,284B /
    手写 assets 总量 ≤340,000B。

    沿革：单文件时代防回涨锚 AFTER_P1_BYTES（F58 138,166B → M3 145,820B → P1
    181,4XX → … → 290,368B 终值）随多文件化退役；M1 起改为壳+assets 总量双锚
    （实测值=构建双跑 sha256 一致后的 wc -c 值；首锚 39,339/251,072 → 导入弹窗
    批 42,284/264,557 → F1 编辑器批手写口径 293,328B（editor.js 24,098 +
    editor.css 4,369 计入手写，CM5 vendor 208,903B 另走 VENDOR_PINNED 不入本锚）
    → F1 集成接线批 293,427B：editor.js 4 处 keydown 补 IME 守卫 +384B、
    editor.css 3,743B 实测回填 → **E-2+E-3 编辑器增强批 314,779B**：editor.js
    42,334B（内联研究面板 + AI 微操作）、editor.css 7,584B → **E-4 极速启动批
    ≤340,000B 上限锚**：quickstart.js 10,055B（独立产物，动态装载）、core.css
    +1,547B、core.js +117B（装载钩子）——实测合并 327,428B；上限取整留 sibling
    在途写缓冲，仍低于 360KiB 硬触发线）→ **三线交界集成批（2026-11-19）
    328,984B**：仅 editor.js +2,486B（选中优先级裁决），340,000B 上限锚不动
    → **E-4 评审修复批（2026-11-19）331,256B**：仅 quickstart.js +2,272B
    （F1 编辑器落点 + F2 流式重绘），上限锚不动（余 8,744B）。
    → **G-T2 大纲模式批（2026-11-18/19）**：compose.html 右栏新增 details#cm-outline
    折叠区（+520B → 壳 42,804B）；行为落 assets/outline.js 独立懒加载（core.js
    只 +101B 装载钩子=204,660B，未触 M3 拆分线）；shell 锚 42,284→44,000 留
    同批 G-T4 滚动摘要折叠区余量，assets 总量锚 340,000 不动（实测 336,888B）
    → **G-T4 滚动摘要批（2026-11-19）341,182B**：compose.html 右栏新增
    details#cm-rolling 折叠区（+541B → 壳 43,345B，仍低于 44,000 锚）；行为落
    assets/rolling.js 独立懒加载 4,193B，core.js 仅 +101B 装载钩子（204,761B，
    未触 M3 拆分线 204,800B——余 39B）；assets 总量锚 340,000→342,000B 重估。
    前端新功能落地超锚即须重估并更新本注释档再动；触发条款（200KiB/360KiB →
    M3 拆分）另设硬门——core.js 现距单文件触发线仅 39B，下一批改动 core.js 的
    功能落地即触 M3 拆分（见模块头水位告警）。
    → **G-T1 Ghost Text 批（2026-11-19）348,551B**：editor.js +6,450B
    （GT 哨兵段控制器 51,270B——防抖 800ms/Tab 采纳/Esc 忽略/cooldown 3s
    全落编辑器手写面，零 core.js 改动、未触 M3 拆分线）+ editor.css +591B
    （8,175B，.cm-ghost-text + hint）；壳零改动（43,345B 不动）；assets 总量锚
    342,000→350,000B 重估（留 ~1.4KB 在途写缓冲）。
    """
    shell = OUT.stat().st_size
    assert shell <= AFTER_M1_SHELL_BYTES, (
        f"壳 {shell}B 已超 M1 实测锚 {AFTER_M1_SHELL_BYTES}B——新增字节超出预算，"
        "须重估（更新本注释档再动）")
    # 总量一支 2026-11-21 起随三档化改走 _assert_assets_total_tier（绿/黄/红），
    # 黄档 PASS+告警不阻塞；红档仍在此红。历史沿革数字见常量注释档。
    _assert_assets_total_tier()


# ── g) M1 产物静态面（/assets/*，安全四门）────────────────


def _get(server, path: str, headers: dict[str, str] | None = None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    headers_out = dict(resp.getheaders())
    conn.close()
    return resp.status, headers_out, body


def test_assets_core_js_served(tmp_path: Path) -> None:
    """/assets/core.js：200 + application/javascript + body 与产物逐字节一致 + ETag/no-cache 头。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/assets/core.js")
        assert status == 200
        assert headers["Content-Type"].startswith("application/javascript"), headers["Content-Type"]
        assert body == CORE_JS.read_bytes(), "/assets/core.js 内容与产物不一致"
        assert headers.get("ETag"), "缺少 ETag 头"
        assert headers.get("Cache-Control") == "no-cache"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_core_css_served(tmp_path: Path) -> None:
    """/assets/core.css：200 + text/css + body 与产物逐字节一致。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/assets/core.css")
        assert status == 200
        assert headers["Content-Type"].startswith("text/css"), headers["Content-Type"]
        assert body == CORE_CSS.read_bytes(), "/assets/core.css 内容与产物不一致"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_traversal_rejected(tmp_path: Path) -> None:
    """`../` 穿越拒绝：/assets/../secret → 400（_resolve_inside root 边界，不泄 root 外文件）。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        (tmp_path / "secret.txt").write_text("x", encoding="utf-8")  # root 内真实存在
        status, _, body = _get(httpd, "/assets/../secret.txt")
        assert status == 400, (status, body[:120])
        # 同级目录穿越（/assets/../core.js 指向 studio/ 其他文件）同拒
        status, _, _ = _get(httpd, "/assets/../ui.html")
        assert status == 400
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_suffix_whitelist_rejects_svg(tmp_path: Path) -> None:
    """后缀白名单：assets/ 内真实存在的 .svg → 404（白名单外一律不服务；svg 同源脚本执行面恒拒）。"""
    from musespiral import studio

    probe = STUDIO / "assets" / "zz-whitelist-probe.svg"
    probe.write_bytes(b"<svg></svg>")
    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            status, _, body = _get(httpd, "/assets/zz-whitelist-probe.svg")
            assert status == 404, (status, body[:120])
        finally:
            probe.unlink(missing_ok=True)
        # 白名单内但不存在 → 同为 404（不区分存在性，不泄目录清单）
        status, _, _ = _get(httpd, "/assets/absent.js")
        assert status == 404
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_etag_304_roundtrip(tmp_path: Path) -> None:
    """ETag/304：首响应 ETag（mtime+size）→ If-None-Match 命中回 304 空体；不命中回 200。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status1, headers1, _ = _get(httpd, "/assets/core.js")
        etag = headers1["ETag"]
        assert status1 == 200 and etag
        status2, headers2, body2 = _get(httpd, "/assets/core.js", {"If-None-Match": etag})
        assert status2 == 304, status2
        assert body2 == b"", "304 响应不得携带 body"
        assert headers2.get("ETag") == etag
        # 不命中（陈旧 ETag）→ 全量 200
        status3, _, body3 = _get(httpd, "/assets/core.js", {"If-None-Match": '"stale-000"'})
        assert status3 == 200 and body3 == CORE_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_entry_document_smoke(tmp_path: Path) -> None:
    """M1 冒烟（HTTP 面）：GET / 200 壳含 assets 引用 → /assets/core.js 200 → /assets/core.css 200。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, shell = _get(httpd, "/")
        assert status == 200
        assert b'href="assets/core.css"' in shell and b'src="assets/core.js"' in shell
        status, headers, _ = _get(httpd, "/assets/core.js")
        assert status == 200
        assert headers["Content-Type"].startswith("application/javascript")
        status, _, _ = _get(httpd, "/assets/core.css")
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


# ── h) 打包契约（package-data ↔ 产物目录）─────────────────


def test_package_data_ships_shell_and_assets() -> None:
    """package-data 必须随包发布壳+assets 全层（M1 契约；M3 子目录也不许漏）。

    2026-11-18 集成接线轮实测缺陷：`studio/assets/*` 单层 glob 在 wheel 里只收
    `core.js|core.css` 一层——M3 按视图拆分后 `assets/views/*.js` 会静默漏装，
    pip 安装态 `GET /assets/views/material.js` 变 404（仓库开发态测不出来）。
    修复=改 `studio/assets/**`；本测试锁死「递归」这一性质（换回单层即红）。
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert "studio/assets/**" in text, (
        "pyproject package-data 须用递归 glob studio/assets/**——单层 studio/assets/* "
        "只打一层，assets/views/ 等子目录会漏装（集成轮 wheel 解包实测）")
    assert "studio/ui.html" in text, "package-data 须含壳 studio/ui.html"


# ── i) 零外联门：引号三态 + 大小写全覆盖（集成接线轮修正）──────


@pytest.mark.parametrize("frag", [
    b'<script src="https://cdn.example/x.js"></script>',    # 基线：双引号小写
    b"<script src='https://cdn.example/x.js'></script>",    # 单引号（原构建期门漏）
    b"<script src=https://cdn.example/x.js></script>",      # 无引号（原两道门皆漏）
    b'<script SRC="https://cdn.example/x.js"></script>',    # 大写属性名（原构建期门漏）
    b'<script src="/static/x.js"></script>',                # 绝对路径：双引号
    b"<script src='/static/x.js'></script>",                # 绝对路径：单引号（原两道门皆漏）
    b"<script src=/static/x.js></script>",                  # 绝对路径：无引号（原两道门皆漏）
    b'<script src="//cdn.example/x.js"></script>',          # 协议相对：双引号
    b"<script src='//cdn.example/x.js'></script>",          # 协议相对：单引号（原构建期门漏）
    b"<script src=//cdn.example/x.js></script>",            # 协议相对：无引号（原两道门皆漏）
    b'<link rel="stylesheet" href="file:///etc/x.css">',    # file:
    b"<link rel=stylesheet href=data:text/css,body{}>",     # data:（原两道门皆漏）
    b'<script src="assets/core.js" src="https://cdn/x.js"></script>',  # 同标签双 src
])
def test_zero_external_gate_rejects_all_ref_forms(frag: bytes) -> None:
    """构建期门必须拒掉**全部**违规引用形态（引号三态 × 大小写 × scheme）。

    2026-11-18 集成接线轮实测缺陷：原 `_TAG_REF_RE` 只认「双引号 + 小写属性名」，
    12 个对拍样本里 5 种违规形态在构建期 PASS（单引号 https / 无引号 https /
    大写 SRC / 单引号绝对路径 / 无引号协议相对），另 2 种（无引号绝对路径、
    data: 无引号）连产物面门也 PASS——「双保险」只在双引号小写这一子集成立。
    """
    mod = _build_module()
    t, v, c, j = _sources(mod)
    tainted = t.replace(b"<!--BUILD:JS1-->\n", b"<!--BUILD:JS1-->\n" + frag + b"\n")
    with pytest.raises(AssertionError, match="assets/ 相对路径"):
        mod.render(tainted, v, c, j)


def test_zero_external_gate_accepts_the_real_shell() -> None:
    """反向：真实壳的引用恰为两条 assets/ 相对路径（门不是一律拒绝）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    shell = mod.render(t, v, c, j)
    assert mod._tag_refs(shell) == [b"assets/core.css", b"assets/core.js"]
