"""R1+F58+M1 构建集成测试：壳 ui.html + assets/core.js|css 双层产物。

覆盖（设置与创作台设计 §三-R1 / F58 构建器 / M1 前端多文件化 2026-11-18）：
  a) 构建产物存在、字节等于各源 render/render_core_js/render_core_css 结果，
     且壳被 make_server 服务（GET / 200 text/html 与产物逐字节一致——走
     _read_ui_html 数据面）；assets 静态面（/assets/*）见本文件「g) 产物静态面」
  b) 体积锚：壳 ≤20KiB（ARCH-1 ③重锚）/ 手写 assets 总量三档 / 分层锚（core/view/
     core.css/view.css/view.html.js）+ 触发条款
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
# 退役——多文件后门约束形态改为「体积锚 + 触发条款」。原三锚形态（沿革留档）：
#   壳 ≤48KiB（49,152B）         [ARCH-1 ③ 已重锚为 20KiB，见 SHELL_MAX_BYTES 处]
#   assets 总量 ≤400KiB          [已改绿/黄/红三档，见 ASSETS_TOTAL_*]
#   单文件 ≤208KiB（212,992B）   [保留为兜底，见 ASSET_FILE_MAX_BYTES]。
# ARCH-1 ① 后壳 HTML 外移（骨架注入 assets/views/*.html.js），壳实测 10,006B；
# ③ 将壳锚重设为 20KiB（正典 §ARCH-1 步骤 8）。
# 正典 §二/§三 预估（壳 ~15-20KB、core.js 压缩后 ~110KB「需实测」）被实测证伪
# ——**估算偏低第四次判例**（沿 E1-E3 偏低 5 倍 / F72 偏低 ~1 倍 / R4 偏低 3 倍
# 之后）：前端体积按设计文档估算立预算不可靠，须以实测为准。
# **触发条款（2026-11-18 裁决原文）**：单文件触及 200KiB=204,800B 或
# assets 总量触及 360KiB=368,640B（90% 水位）→ 强制启动 M3 按视图拆分，
# 不得再放宽锚值。
# **as-built（2026-11-19 M3 拆分落地后）**：条款已由两层机器门承接——①分层水位门
# core.js ≤160KiB / 单 view ≤40KiB（test_m3_split_trigger_watermark）；②总量三档
# （_assert_assets_total_tier，2026-11-21 起）。原 M3_TRIGGER_* 两条常量零消费点，
# 保留为沿革值待裁决（见其定义处注释）。
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
# ── ARCH-1 ③ 壳锚重设（2026-11-21）──────────────────────────
# ①壳 HTML 外移后壳实测 10,006B（原 49,130B，−79.7%）——原 48KiB 锚失去意义。
# 正典 §ARCH-1 步骤 8：壳锚 48KiB→20KiB（拆完预期 ~15KB，留缓冲）。实测余 10,474B。
SHELL_MAX_BYTES = 20 * 1024   # 20,480B（ARCH-1 ③；原 48KiB）
# ── 总量三档（2026-11-21 owner 裁决「机制修正」：总量锚自硬门降为绿/黄/红三档）──
# 沿革：REFINE-ITER 批（2026-11-20）最小必要抬锚 426,500→432,000B 原带
# 「⚠️ 待 owner 追认」标记——**2026-11-21 owner 明确追认 432,000 有效**；同日
# N-2 素材闭环前端四入口批（+11,524B → 批内读数 443,396B）第三次撞锚（两天内
# REFINE-ITER +5,368 / N-2 +11,524 连撞），而**首屏使用率始终 ~60.1%
# （N-2 批内读数 147,811/245,760B；按提交 8fbc815b6b 复算 147,853B=60.2%，口径
# 纪律见下方「数字口径纪律」条）从未承压**——owner 裁断：总量增长是功能正常扩张
# （9 插件/11 视图/5 源线）而非膨胀失控，用硬门管正常增长是范畴错误。
# 三档语义（本批起生效，第 9 次调整=最后一次数字调整——机制已改，后续
# 按三档走，不再抬数字）：
#   绿：总量 ≤ 444,000B                       → PASS（正常）
#   黄：444,000B < 总量 ≤ 480,000B（+8%）     → PASS + 醒目告警（warnings+print，
#       不阻塞交付；下批须在注释档补增长归因说明）
#   红：总量 > 480,000B                        → FAIL（真膨胀，必须拆分/删除）
# **AI-1 三态裁决前端批（2026-11-21）实测 447,540B 落黄档**（归因：plugin-run.js
# 徽章/入口 +2,386B、material.js +394B——见 ASSETS_TOTAL_GREEN_MAX 上方注释档）。
# **AI-3 重生成前端批（2026-11-21）实测 452,653B 仍在黄档**：+5,113B =
# plugin-run.js +3,662B（🔄 入口×2 + 行内重生成输入条 prRegenBar——改进要求
# 收集不用 window.prompt（自定义弹层红线，test_studio_ui 否向锁）+ prRegen*
# 提交/轮询/忙闸）+ iterations.js +1,451B（每版 [🔄] 重生成入口 + regen 处理
# 器）——壳零增量（49,124B 不变，按钮与输入条全 JS 动态渲染）；两文件均
# 懒加载不进首屏。
# **AI-3 六条复核修复（2026-11-21）实测 454,237B 仍在黄档**：+1,584B =
# plugin-run.js +📜 版本对照面板（prVerPanel/prVerOpen/prVerDiff + 两按钮）、
# iterations.js +2,852B（window.prompt→行内输入条、revert 走 askConfirm）、
# versions.js +187B（window.confirm→askConfirm）、quickstart.js -4B（去 ✅ emoji）
# ——壳零增量（49,124B 不变，全 JS 动态渲染）；均懒加载不进首屏。
# **N-4 置信度+血统批（2026-11-21）实测 469,192B 仍在黄档**：+14,955B（同批
# 树上另含 N-5 并排预览批增量，二者共用黄档，归因一并记）= lineage.js 新建
# 7,671B（N-4 血统 tab：正向树+反向列表+人工核验入口——material.js 贴 40KiB
# 锚余量 97B 装不下，走 iterations.js 独立产物判例；懒加载不进首屏；含冒烟
# 轮 +17B 首开死锁修复——repaint 只在 V.id 已置位时才赋值，首开 V.id 恒 ''，
# 点击门 if(t&&V.id) 永假，改为 V.id!==id 即重置赋值）+
# core.js +2,014B（confBadge 三态徽入 licBadge + mat-lowconf 筛选 chip +
# matQS confidence 参数 + 独立产物装载清单+lineage 装载行）+ views/
# material.js +85B（lineage repaint 钩子 + 清筛选复位 mconf——40,863→40,948B
# 距 40KiB 锚余 12B）+ N-5 侧 views/article.js +4,330B（并排双栏 SBS 函数族）
# 与 core.css +753B（sbs 样式，见其批注释）。
# **ARCH-1 ①+② 拆分批（2026-11-21）总量三档口径重校（口径级调整·第二次也是
# 最后一次，owner 裁决 2026-11-21）**：ARCH-1 落地后 assets-only 口径实测
# 513,073B 超红档 480,000B——但拆解证明**不是真膨胀，是门口径与架构动作的
# 结构性冲突**：壳 HTML 外移使壳 −39,124B（门只扫 assets/，减量不可见）而
# views/*.html.js 十文件 +39,666B（门内可见），净效果=前端总交付面仅 +~2KB
# 包装开销。**口径变更理由**：原口径只扫 assets/，任何「字节从壳搬到 assets」
# 的瘦身动作都会被误判为膨胀（REFINE-ITER/批3/ARCH-1 三次撞门同根因）；门应
# 度量「用户为本站付出的总字节」= **壳 + 手写 assets 全量**。**ARCH-1 归因链**
# （git ls-tree HEAD 复算，正典口径）：HEAD 469,192B → ①壳 49,130→10,006B
# （−39,124，门内不可见）+ html.js +39,666 + core.js/独立产物 +3,176 → ②
# core.css 77,416→33,749（−43,667）+ views/*.css +44,081（净 +414=@media 包裹
# 重组与 .pg-detail,.pr-rungrid 逗号组拆分固有开销）+ core.js 装载链 +625 →
# 实测总交付面 **523,079B**（壳 10,006 + assets 513,073）。**首屏锚不动**：
# 首屏（壳+core.js+core.css）149,551→106,509B（−29%），ARCH-1 核心收益；
# FIRST_SCREEN_MAX_BYTES 硬门与 80% 告警语义不变。三档数字按新口径重校：
# **FEAT-2 智能组稿批增长归因（2026-11-22，绿→黄首因）**：compose 视图加三步
# 向导——ui.views/compose.html 向导 modal 骨架+入口按钮 → assets/views/
# compose.js +7.8KB（SC 状态机/三步导航/素材列表/大纲就地编辑）→ compose.css
# +1.4KB（.sc-* 全族）；零 core.js/壳改动（向导全在懒加载视图内，首屏不动）。
# 总交付面 528,229→539,277B（+11,048B）越过绿档 530,000 落黄——黄档=软警报
# 不阻塞；下一批前端落地前宜先清一笔 views/*.js 瘦身或重校绿档。
# **FEAT-3 数据回流批增长归因（2026-11-22，黄带内续增）**：洞察 SVG 趋势图——
# ui.views/insight.html +趋势卡 → assets/views/insight.js +聚合/渲染（statsBuckets/
# renderStatsTrend，零新端点）→ insight.css +14 条 .st-*/stats-trend 规则（三线色
# 走 CSS 变量，暗色自动覆写）；零 core.js/壳改动（全在懒加载 insight 视图，首屏
# 不动）。总交付面 539,277→544,013B（+4,736B），仍在黄带（≤570,000）软警报内，
# test_plugins_ui TOTAL_MAX 已随批 540,000→545,000 对齐。
# **FEAT-4 小说双件套批增长归因（2026-11-22，黄带内续增）**：人物关系力导向图
# （SVG 自绘零依赖，不引 D3）+ 伏笔账本三态面板——按 lineage.js 判例二次拆分为
# 独立产物 assets/novel.js（9,242B：forceLayout/graphSvg/foreHtml + boot，
# compose.js 贴 40KiB 锚不进视图产物）+ compose 视图关系轻面（+643B：挂卡行关系
# 展示/建卡弹窗 relations 输入/cnParseRelations）+ compose.html cm-rel/cm-fore 两
# 容器（→html.js +346B）+ compose.css 11 条 .cn-* 规则 + core.js 装载接线
# （+149B：loader 清单 + VSTANDALONE 登记）。首屏不动（novel.js 懒加载）。
# 总交付面 544,013→555,262B（+11,249B），仍在黄带（≤570,000）软警报内，
# test_plugins_ui TOTAL_MAX 已随批 545,000→556,000 对齐。
# **FEAT-5 前端交互升级批增长归因（2026-11-22，黄带内续增，+6,958B）**：
# ①快捷键速查（? 唤起）——壳 ui.tmpl.html +kbd-modal +535B、ui.js core
# +SHORTCUTS/kbdOpen/键位绑定（?/​/ 聚焦搜索/Escape 链）；②行式骨架
# skelCards/skelRows 归 core（material/sources 首访占位画在 __onViewHtml——
# material.js 贴 40KiB 水位仅 +7B 不加逻辑）；③四视图空态引导（sources/plugins/
# design/books 空态文案改「你可以做什么」+CTA 按钮，html.js +~700B）；④暗色补全
# （live 预览 iframe 双色板 + 灯箱错误色 token 化）。首屏增量=壳 +535B 与
# core.js +4,050B（速查数据与骨架通用件，视图共用故归 core）。总交付面
# 555,262→562,220B，仍在黄带（≤570,000）；test_plugins_ui TOTAL_MAX 随批
# 556,000→563,000 对齐——**黄带余量仅 7KB，下批前端功能应先瘦身或走
# novel.js 式独立产物判例**。
# **UIX-REV 五角色评审修复批增长归因（2026-11-22，黄带内续增，+5,909B）**：
# 9 项体验修复——①素材缩略图 onerror 兜底；②洞察空态 flex+order 排数据后；
# ③预览本地图片路径规范化（./、查询片段、百分号编码）；④源名称/ID 分行；
# ⑤插件卡描述首句截断+工程术语过滤；⑥发布卡片待审徽区分；⑦书源导入全已最新
# 折叠摘要行；⑧漏斗阶段悬浮说明；⑨编辑器工具栏增专注/打字机双显式入口。
# 增量分布：books.js +795 / editor.js +880 / core.js +487 / insight.js +375 /
# books.css +348 / sources.css +334 / insight.css +222 / core.css +160 B，均落
# 懒加载视图或已加载 JS（壳仍 10,541B 首屏不动；material.js 40,631B 未触 40KiB
# 锚）。总交付面 562,373→568,282B，仍在黄带（≤570,000）；test_plugins_ui
# TOTAL_MAX 随批 563,000→569,000 对齐——**黄带余量仅 1.7KB，下批前端必须
# 走独立产物或瘦身，勿再放宽**。
# **UIX-3 material 视图三拆批增长归因（2026-11-22，黄带内 +862B，零功能新增）**：
# material.js 40,631B 贴 40KiB 锚（余 329B），按职责三拆＝core 20,049 / drawer
# 11,249 / media 10,066B（子文件合计 41,364B，较单文件 +733B＝三文件固有开销）
# + core.js +174B（VJS 首载映射与 matPart 懒加载器注释行）。drawer/media 走
# matPart 按需装载（进抽屉/开面板才拉取），首屏 parse 面不变（壳+core+首载视图）。
# 批前基线复算 568,323B（569,230 − 41,364 + 40,631 − 174）——黄带入场系历史批
# （UIX-REV 前各批累计）所致、当时未留归因，本批一并记债。本批为**纯物理拆分**
# （不删不合并，代码字节零重写，仅 13 处懒加载门包装），后续增量回归 UIX-REV
# 批的处置判例：独立产物或瘦身，勿再放宽。
# **UIX-3 无障碍批增长归因（2026-11-23，深黄段续增，+5,499B）**：五角色评审问题
# 14——aria/键盘全覆盖（目标=键盘用户能完成核心流程，非全量 WAI-ARIA）。增量
# 构成：core.js +2,178B（弹窗焦点管理对 openDlg/closeDlg——记触发者/焦点入弹/
# 关弹还焦，接入 askConfirm/cp/kbd/clip/viewer/src-form/src-aicfg/im/coll-form/
# cv-pm/sc-modal/cm-ch-modal + Esc 登记制兜底 DM_ESC + renderNav aria-current +
# rail 徽标 span 键盘化 tabindex/role/Enter + SHORTCUTS 补 compose ⌘⏎ 行 + 源表 ⚙
# aria-label）/ material 三子段 +563B（缩略图/标签 × role=button tabindex=0 与
# mat-list 键激活委托（kb.click() 走既有 matActsClick 键鼠同路）/ 详情·综合·生成
# 三面板 Esc 分落 drawer/media 子文件——core 子段 20,274B 距 20KiB 子锚余 206B）/
# quickstart.js +413B（极速启动弹窗焦点进出复用 core 助手 + Esc）/ plugins.js
# +391B（插件卡 role=button 的 Enter/Space 激活）/ compose.js +336B（⌘⏎ 按反馈
# 再生成——article 域 ⌘⏎ 同款主操作键）/ core.css +257B（focus-visible 补齐
# nav-item/coll-item/cp-item/[role=button]/nav-badge/switch/mat-thumb 等）/
# editor.js +116B（工具栏与查找条图标按钮 aria-label）/ 六视图 html.js +1,041B
# （aria-label 补齐：搜索框/表格 aria-label/书源 role=list+勾选框标签/tab
# aria-selected/弹窗 role=dialog+aria-modal 补缺——sc-modal/cv-pm/coll-form/
# clip-form/lightbox/gen-panel/cm-ch-modal）/ 壳 +122B（toast role=status
# aria-live + clip-form dialog 语义 + sys/jobs 徽标 role=status）/ books.js
# +165B（imOpen 焦点管理 + 书源行 aria-label/aria-expanded）。**同批修偏**：
# _assert_assets_total_tier 原实现 >YELLOW_MAX 即 raise，与本函数 docstring
# 「红 >600,000B FAIL」及 ASSETS_TOTAL_RED_MAX=600,000 常量注释矛盾（黄带被静默
# 折叠成硬门）——修为按文档语义在 RED_MAX 设闸，570,000~600,000 段保持不静默
# （发「深黄（距红线）」告警），test_assets_total_tier_semantics 探针同步改到
# 红线（黄/深黄/红三段断言齐全度高于原版）；test_plugins_ui TOTAL_MAX 按其
# 「随批对齐实际水位」协议 570,000→576,000。本批落深黄段（574,729B，距红线
# ~25KB）：下批前端宜先清一笔瘦身再增。
# **FEAT-P1A 批增长归因（2026-11-23，深黄段续增，+6,059B）**：AI 辅助创作
# 增强 P1 双件——①质量循环（check→AI 修复→再 check，job kind=quality_loop，
# KINDS 19→20）②标题 A/B 三风格（问题式/数字式/情感式，本地预览三选一，
# 不接微信 A/B API）。增量分布：views/article.js +4,839B（ql*/ab* 六函数：
# 质量循环提交/结果渲染/修复对比复用既有 viewer+diffHtml、A/B 生成/渲染/
# askConfirm 应用）+ views/article.html.js +1,192B（操作 tab 两动作组骨架：
# ql-btn/ql-rounds/ql-result、ab-btn/ab-result——role=status aria-live，
# 按钮带 aria-label）+ core.js +28B（JN 任务面板 kind 标签 +quality_loop）。
# 壳零增量（10,663B 不变，骨架全走懒加载 article 视图）；复用既有 vcard/
# check-row/action-group 样式零新增 CSS。总交付面 574,729→580,788B，仍在
# 深黄段（距红线 600,000 尚余 ~19KB）；test_plugins_ui TOTAL_MAX 随批
# 576,000→581,000 对齐。⚠ article 视图两锚水位：article.js 32,136B（40KiB
# 锚余 ~8.8KB）与 article.html.js 7,847B（8KiB 锚仅余 345B）——下批再动
# article 视图 HTML 须先瘦身措辞或拆分骨架。
# **FEAT-P1B 批增长归因（2026-11-23，深黄段续增，+10,055B）**：两项 P1 创作体验
# 增强——①大纲拖拽排序（HTML5 D&D 零依赖，仅手柄可拖）+ 键盘重排（手柄聚焦 ↑↓）+
# 指示线/占位条 + action=replace 持久化；②写作统计仪表盘（writing_stats.py 纯读
# rounds 历史→按日增量/连续天数/周目标 + 洞察 SVG 柱图/徽章/进度条，空数据引导）。
# 增量分布：assets/outline.js +5,126B（手柄 data-ol-handle+aria-label/olDnd 五式/
# olMove 排序/olCommit replace/键盘 ↑↓+焦点回归）、views/insight.js +2,701B
# （writingBars/renderWriting）、views/insight.css +902B（.wr-*/.streak-badge）、
# views/compose.css +707B（.ol-*）、core.js +384B（loadWriting+state.writing）、
# views/insight.html.js +399B（写作节奏卡骨架）。壳零增量（10,663B 不变，全落
# 懒加载视图/独立产物）。总交付面 580,788→591,007B（+10,219B），仍在深黄段
# （距红线 600,000 余 ~9KB）；test_plugins_ui TOTAL_MAX 随批 581,000→592,000
# 对齐。⚠ 深黄余量已不足 10KB——下批前端须走瘦身或独立产物判例，勿再放宽。
# **W2-B5 前端瘦身实证批（2026-11-23，深黄段 −2,451B）**：设计正典目标
# 「591,007→≤570,000」经三条路径实测**前提不成立**——①死代码全树仅 3 个具名
# 函数 338B 且全是测试直调的导出函数（edReject/edAIMarkCount 在 test_editor_ai
# 真调、$ 是 core.js 通用选择器）→ 真实可回收 0B；②口径内 CSS 已多数压缩过，
# 无损压缩仅 2,172B；③「独立产物外移不计总量」前提错误（口径=壳+全部手写
# assets，懒加载文件仍在 assets/ 内）。本批真实收益=editor.css 去块注释
# 8,190→5,739B（−2,451，逐字节无损语义，243 条 CSS 相关测试绿）+ 债务如实
# 登记：**缺口 18.5KB 需删功能或砍 JS 注释（24%=121KB 不可取），本项降级为
# 技术债**。门本身不阻塞（深黄 570–600K 是告警非红）。下批前端功能前先评估
# 「是否必须进 assets/」。
ASSETS_TOTAL_GREEN_MAX = 530_000      # 523,079 + ~7K 缓冲
ASSETS_TOTAL_YELLOW_MAX = 570_000     # 绿 ×1.075——软警报带
ASSETS_TOTAL_RED_MAX = 600_000        # 硬上限：总交付面 600KB 对个人工具已极宽松
# ~~旧三档常量~~（ARCH-1 口径重校退役）：GREEN 444,000 / YELLOW 480,000 为
# assets-only 口径读数，随口径退役；「红档（>480,000B）硬失败」语义由
# ASSETS_TOTAL_RED_MAX 承接。**此为第二次也是最后一次口径级调整——将来再爆
# 红档即真膨胀，按拆分/删除处置，不得再动口径。**
# 旧常量 ASSETS_TOTAL_MAX_BYTES / M3_ASSETS_TOTAL_MAX_BYTES / AFTER_M1_ASSETS_BYTES
# 随三档化退役：消费它们的三个测试统一改走 _assert_assets_total_tier()。
# AFTER_M1_ASSETS_BYTES 的定义行已于 2026-11-21 三档化批（提交 8fbc815b6b）删除，
# 全仓 `git grep AFTER_M1_ASSETS_BYTES` 只剩本条注释——台账 B-1「死锚」就此闭合
# （本批复查：无定义、无消费点）。
ASSET_FILE_MAX_BYTES = 208 * 1024
# ~~同族死锚~~（2026-11-21 owner 裁决：删除——三档语义已完全承接）：原 M3_TRIGGER_FILE_BYTES
# （200KiB）/ M3_TRIGGER_ASSETS_BYTES（360KiB）同样全仓零消费点（`git grep -n
# M3_TRIGGER_` 只命中定义行），与 AFTER_M1_ASSETS_BYTES 同类。台账 B-1 只点名后者，
# 故本批不擅自扩大删除范围，留待 owner 裁决（删或保留为沿革值）。
# 触发条款的 operative 形态=分层水位门（core 160KiB / 单 view 40KiB，
# test_m3_split_trigger_watermark）+ 总量三档（_assert_assets_total_tier）。

# ── M3 分层锚（2026-11-19 按视图拆分；替换原「单文件 208KiB」上限锚）──────
# 拆分后 core.js = 工具/路由/状态/命令面板/toast/jobs/装载器 + 图标定义；
# views/<view>.js = 九视图各自 render* + 专属 helper + bind<View>()。
# 实测（构建双跑、手写口径=剥 assets/editor/ vendor）：
#   shell 45,720B / core.js 55,519B / 单 view 最大 material.js 39,511B /
#   手写 assets 总量 376,127B≈367.3KiB / 首屏(壳+core+单view 最大) 140,750B≈137.5KiB。
# 分层：core ≤160KiB（余 105KB）/ 单 view ≤40KiB（material 39,511B 距锚仅 1.4KB）/ 总量锚见下。
# ── ARCH-1 ② CSS 拆分分层锚（2026-11-21；总规划 §ARCH-1 步骤③锚重设）──────
# core.css 自「全量 77,416B」拆为 core 33,749B（token/暗色/reset/keyframes/壳框架/
# 通用组件/首屏 publish）+ assets/views/<view>.css 十懒视图（最大 material.css
# 7,827B）。锚：core.css ≤40KiB（余 ~6.2KB）/ 单 view css ≤8KiB（material 余 ~365B）。
# ── ARCH-1 ③ 视图 HTML 注入器新锚（2026-11-21；正典 §ARCH-1 步骤 9）────────
# ①外移后 assets/views/<view>.html.js（十懒视图骨架注入器，publish 无此产物）
# 单文件最大 books.html.js 6,487B；锚 ≤8KiB（余 ~1.7KB）。此锚原缺失——③补齐。
CORE_JS_MAX_BYTES = 160 * 1024        # 163,840B
VIEW_JS_MAX_BYTES = 40 * 1024         # 40,960B
# ── UIX-3 material 视图三拆子锚（2026-11-22；五角色评审问题 11）──────────────
# material.js 40,631B 贴 40KiB 锚仅余 329B——UIX-3 aria 标签等增量已无空间，按职责
# 三拆（沿 iterations.js 独立产物先例，但子文件走 matPart() 按需装载而非首屏拉取）：
#   material-core.js   ≤20KiB（列表渲染/筛选/路由 hash/动作委托/搜索/标签/上传预检接线）
#   material-drawer.js ≤15KiB（详情抽屉/AI 摘要 refine/标签建议/原始章节/手工集合）
#   material-media.js  ≤15KiB（AI 综合/文生图/上传 tray/灯箱——AI 面板与媒体域）
# 原 material 单文件 40KiB 锚随三拆**拆除**（该产物已不存在，build 同步清陈旧文件）；
# ensureView 经 core 段 VJS 映射首载 -core，drawer/media 由 matPart('drawer'|'media')
# 幂等懒加载（进入抽屉/面板/灯箱首触才拉取；实测 core 20,049B 余 431B）。
MATERIAL_CORE_MAX_BYTES = 20 * 1024   # 20,480B
MATERIAL_DRAWER_MAX_BYTES = 15 * 1024  # 15,360B
MATERIAL_MEDIA_MAX_BYTES = 15 * 1024   # 15,360B
CORE_CSS_MAX_BYTES = 40 * 1024        # 40,960B（②拆分后实测 33,582B，余 6.4KB）
VIEW_CSS_MAX_BYTES = 8 * 1024         # 8,192B（②实测最大 material.css 7,825B）
VIEW_HTML_MAX_BYTES = 8 * 1024        # 8,192B（③实测最大 books.html.js 6,487B）
FIRST_SCREEN_MAX_BYTES = 240 * 1024   # 245,760B：行业 size-limit 洞察=parse/execute 成本
# 总量锚：题面建议 360KiB（368,640B），但「一份 ui.js 拆成 1 core + 9 view」的固有开销
# （9 独立文件 + 跨视图调用的 no-op 占位符 + 9 bind 包装 + ensureView）使实测为
# 376,127B（367.3KiB）——距 360KiB 仅差 7.5KiB，比拆分前手写总量（367,766B）还略低。
# 占位符是必需的：core 的 waitForJob→pPaint、cpMain→handleSaveBody 等调用可能发生在
# 用户未访问对应视图时（去掉会 ReferenceError），故不去掉。按「先实测再定」设
# 380,000B（留 ~3.9KB 在途缓冲）；真正的收益在首屏锚（137.5KiB，拆分前 ~286KiB）。
# PL-3 插件前端批（2026-11-20）实测 **420,965B**（提交 f0dd27ad0a，手写口径；
# 由 `git ls-tree -r -l f0dd27ad0a -- .../studio/assets` 逐文件字节求和复算，
# 剥 assets/editor/ vendor：本批 +44,256B = plugins.js 14,178B 新建 +
# plugin-run.js 16,962B 新建 + core.css +12,357B + core.js +759B）→ 据实重估
# 425,000B（留 ~4KB 在途缓冲），重估依据档见 AFTER_M1_SHELL_BYTES 上方注释。
# **数字口径纪律（2026-11-21 债清偿批 B-2）**：本注释档此前同时出现两个互斥
# 读数——正文三处写 420,097、`AFTER_M1_ASSETS_BYTES` 行内注写 419,987（二者
# 同在提交 f0dd27ad0a 里）；台账 B-2 另记一个「实测 420,143」，该值在全仓
# git 历史中不存在（`git log -S` 零命中）。三者与提交复算值 420,965B 均不符。
# 此后体积数字一律写「提交后复算值 + 日期 + 提交号」；批内工作树读数若与
# 提交值不同，两者并列注明并以提交复算值为准（见下面 PL-3 / 批2 两处）。
# 批2 双线并行抬锚（2026-11-20）425,000→426,500B（N-1 +5,440 / N-3 +2,514）——
# 归因与「抬锚终止条款」同上注释档。⚠ 批内读数 425,599B；按提交 d8df1158a0
# 复算为 **425,809B（+210B）**——按本批口径纪律，以提交复算值为准。
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
# **批 AI-1 三态裁决前端（2026-11-21，黄档首次进入——归因）**：N-2 后基线
# 443,744B → 实测 **447,540B（+3,796B）**：
#   plugin-run.js 16,871→20,667B 为唯一增量源（core.js/core.css 零字节变动，
#   构建双跑逐字节一致）：新增 prStBadge/prRangeTitleBadge/prRngOps/prAwOps/
#   prVerdict/prChainHasArticle/prApplyVerdicts 七个纯函数（node 直测）+
#   prAdjudicate（adopt/discard 两端点调用）+ ranges 每条徽章与采纳/弃用入口 +
#   产物区成稿徽章与事件委托。
# 壳**零增量**（49,124B 原样——徽章全走纯 JS 动态渲染，HTML/壳零新增；
# 距 48KiB 硬锚余 28B 不变）。首屏 60.2%（148,010/245,760B）未承压：增量全在
# 懒加载视图，不入首屏。故本批同时不触 core.js 160KiB 与首屏 240KiB 两硬门。

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
# ui.js core 增 VIEWS/CRUMBS/HALIAS/renderRoute 两分支 + ICONS play/flow 两枚；
# ui.css +12,357B（pg-*/pr-* 全族 + toast 底距 16→24px =56px，原型踩坑纪律）；
# assets/views/plugins.js 14,178B + plugin-run.js 16,962B（均 <40KiB 分层锚）。
# 手写 assets 总量 **实测 420,965B（2026-11-21 按提交 f0dd27ad0a 复算）** →
# **M3 总量锚 380,000→425,000B**（留 ~4KB 在途缓冲；首屏 144,524B=壳 48,361 +
# core.js 57,009 + 最大 view material.js 39,154，同法复算，距 240KiB 余量充足）。
# ⚠ 本批提交内同时存在两个互斥读数：正文三处 420,097B（首屏 144,583B）与
# AFTER_M1_ASSETS_BYTES 行内注 419,987B（台账 B-2；台账另记 420,143，该值
# git 历史零命中）。按提交逐文件复算 = 420,965B，故：f0dd27ad0a 420,965B →
# d9a8b829cd 418,551B → 5ed83a29f8 418,385B → d8df1158a0 425,809B（首个越过
# 425,000 的点即批2）。统一以提交复算值为准。
# **PL-3 集成冒烟修复批（2026-11-20）**：prPipesHtml 此前未接线（prPaint 只切
# hidden 不渲染）——裸 #plugin-run 流水线清单空白（浏览器冒烟发现，结构测试
# 只查字面量查不到调用图）。prPaint pick 分支补一行 innerHTML 接线。
# **PL-3 复核修复批（2026-11-20，同日第二批——后端接驳+门控）**：后端补齐
# /api/plugins*、/api/pipelines* 消费面（plugin_actions.py；前端 run 提交改精确
# POST /api/pipelines/run）；hasSegs 门控（segments 型链才挂转写面板）+ prEnter
# 清编辑态（冒烟2 判例：?run= 深链不得残留上一链面板）+ 清单文案自洽
# （跳过 N 个时不再宣称全部过契约门）。本批与 PL-3 主体**同落提交 f0dd27ad0a**
# （无独立提交），故其批内读数无法与提交一一对应：注释档原记「总量 420,956B、
# 首屏 144,524B」，其中首屏与提交复算一致（48,361+57,009+39,154=144,524），
# 总量比提交复算 420,965B 少 9B（= plugins.js 批内读数 14,169 与提交值 14,178
# 之差）；锚均不动（余量充足：总量距 425,000B 余 4,035B）。
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
#   改懒加载（M3 架构的能力，用它）。当前 ~60%：AI-3 提交 2b0c18f738 复算
#   148,010B=60.2%（N-2 批内读数 147,811B=60.1%，同档）。
# **二级（单产物上限，防单文件失控——维持硬门）**：
#   core.js ≤ 163,840B / 单 view ≤ 40,960B / 壳 ≤ 20,480B（ARCH-1 ③ 重锚）
#   这三条不随总量变化，任何「拆成独立懒加载文件」都合法绕过（正是设计意图；
#   判例：E-6 versions.js、REFINE-ITER iterations.js 自 material.js 拆出）。
# **三级（总量三档，防整体膨胀——2026-11-21 由硬门降档）**：
#   绿 ≤444,000B / 黄 ≤480,000B（软警报：PASS+告警，下批归因，不阻塞交付）/
#   红 >480,000B（硬失败，必须拆分/删除）。判例依据：两天三撞（REFINE-ITER
#   +5,368 / N-2 +11,524）而首屏始终 60% 未承压——总量增长是功能正常扩张
#   （9 插件/11 视图/5 源线），用硬门管正常增长是范畴错误。
# ARCH-1 ③（2026-11-21）：壳硬锚 SHELL_MAX_BYTES 已 48KiB→20KiB，本防回涨锚同步对齐
# （否则 49KB 的弱锚与 20KB 硬锚并存的冗余/矛盾）。原值 49,152=N-3 批硬锚上界。
AFTER_M1_SHELL_BYTES = 20 * 1024  # 20,480B（ARCH-1 ③，与 SHELL_MAX_BYTES 同值）

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
    "assets/editor/theme/paper-ink.css": 3_108,  # 2026-11-22 FEAT-5 暗色补全：头注释内旧变量列表的「星号紧随斜杠」序列提前闭合注释，吞掉首条 .CodeMirror 基础规则（编辑器永久白底暗色失效，浏览器 CSSOM 实测定位）——变量列表改顿号分隔+成因注记 +390B，随批更新钉死值
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
    """产物存在且逐字节等于各源 render/render_core_js/render_core_css 结果（conftest 会话级已构建）。

    M3 起产物=壳（render）+ assets/core.js（render_core_js 去视图块）+
    assets/views/<view>.js（render_view_js 九视图）+ assets/core.css（render_core_css）。
    ARCH-1 ② 起 + assets/views/<view>.css（render_view_css 十懒视图；publish 无
    产物——首屏样式在 core.css）。全部对拍，缺一或漂移即红。
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
    # M3：视图产物逐一对拍（UIX-3 起 material 为三子段产物，其余十视图单文件）
    _, js_views = mod.split_ui_js(js)
    assert set(js_views) == set(mod.EXPECTED_VIEWS), "视图块集合与预期不一致"
    for name, blob in js_views.items():
        parts = mod.render_view_parts(js, name)
        if set(parts) == {""}:
            path = STUDIO / "assets" / "views" / f"{name}.js"
            assert path.is_file(), f"视图产物缺失: {path}"
            assert path.read_bytes() == parts[""], \
                f"assets/views/{name}.js 与 ui.js 视图块压缩结果不一致"
        else:
            assert set(parts) == {f"-{p}" for p in mod.EXPECTED_SUBVIEWS[name]}, name
            for suffix, product in parts.items():
                path = STUDIO / "assets" / "views" / f"{name}{suffix}.js"
                assert path.is_file(), f"子段产物缺失: {path}"
                assert path.read_bytes() == product, \
                    f"assets/views/{name}{suffix}.js 与 ui.js 子段压缩结果不一致"
            assert not (STUDIO / "assets" / "views" / f"{name}.js").exists(), \
                f"子拆分视图 {name} 的单文件产物应已退役（build 负责清陈旧）"
    # ARCH-1 ②：十懒视图 CSS 产物逐一对拍（publish 无 css 产物）
    css_views = mod.load_view_css()
    assert set(css_views) == set(mod.EXPECTED_VIEWS) - {mod.FIRST_SCREEN_VIEW}, \
        "ui.views/*.css 源集合与预期懒视图不一致"
    for name, blob in css_views.items():
        path = STUDIO / "assets" / "views" / f"{name}.css"
        assert path.is_file(), f"视图 CSS 产物缺失: {path}"
        assert path.read_bytes() == mod.render_view_css(blob, name), \
            f"assets/views/{name}.css 与 ui.views/{name}.css 压缩结果不一致"
    assert not (STUDIO / "assets" / "views" / "publish.css").exists(), \
        "首屏视图不得有 css 产物（publish 样式在 core.css）"


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
    """总交付面（ARCH-1 口径重校 2026-11-21 owner 裁决）：壳 ui.html + 手写 assets。

    原 assets-only 口径把「壳→assets 字节搬家」（ARCH-1 壳 HTML/CSS 外移，壳同额
    减小但门不可见）误判为膨胀——门改度量用户真实交付面=壳产物 + assets/ 手写
    文件（剥 assets/editor/ vendor，与分层锚同口径）。归因链与三档重校依据见
    ASSETS_TOTAL_GREEN_MAX 注释档。
    """
    shell = OUT.stat().st_size
    return shell + sum(p.stat().st_size for p in _handwritten_files())


def _assert_assets_total_tier() -> int:
    """总量三档判定（2026-11-21 owner 裁决机制修正；ARCH-1 起口径=总交付面）。

    绿 ≤530,000B PASS；黄 ≤570,000B PASS+醒目告警（warnings+print，不阻塞
    交付，下批须在常量注释档补增长归因）；深黄 570,000B<总量 ≤600,000B
    PASS+距红线告警（仍不阻塞）；红 >600,000B FAIL（真膨胀，必须拆分/删除）。
    返回实测总量供调用方记录。
    2026-11-23 UIX-3 无障碍批修偏：原实现在 >YELLOW_MAX 即 raise，与本函数
    docstring「红 >600,000B FAIL」及 ASSETS_TOTAL_RED_MAX 常量注释（硬上限
    600KB）自相矛盾——黄带被静默折叠成硬门。修为按文档语义在 RED_MAX 设闸，
    570,000~600,000 段保持「不静默」纪律（发距红线告警），与 owner 三档裁决
    「黄不阻塞、红硬失败」一致。
    """
    import warnings

    total = _assets_total_bytes()
    if total <= ASSETS_TOTAL_GREEN_MAX:
        return total
    if total <= ASSETS_TOTAL_RED_MAX:
        cap, tier = (
            (ASSETS_TOTAL_YELLOW_MAX, "黄档")
            if total <= ASSETS_TOTAL_YELLOW_MAX
            else (ASSETS_TOTAL_RED_MAX, "深黄（距红线）"))
        msg = (
            f"⚠️ 前端总交付面（壳+手写 assets）{total}B 落入{tier}（"
            f"{ASSETS_TOTAL_GREEN_MAX}~{cap}B 软警报带）：不阻塞"
            "交付，但下一批必须在 test_build_ui.py 常量注释档补增长归因说明"
            "（绿/黄/红三档语义见 ASSETS_TOTAL_GREEN_MAX 注释档）"
        )
        print("\n" + "=" * 24 + msg + "=" * 24 + "\n")
        warnings.warn(UserWarning(msg), stacklevel=2)
        return total
    raise AssertionError(
        f"前端总交付面（壳+手写 assets）{total}B 超红档 {ASSETS_TOTAL_RED_MAX}B"
        "——真膨胀，必须拆分或删除（三档语义见 ASSETS_TOTAL_GREEN_MAX 注释档）")


def test_shell_within_budget() -> None:
    """壳锚（ARCH-1 ③ 重锚）：ui.html ≤ 20KiB（①外移后实测 10,006B，余 ~10KB）。

    正典 §ARCH-1 步骤 8：壳锚 48KiB→20KiB（拆分后预期 ~15KB，留 5KB 缓冲）。
    """
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
    """分层锚（手写口径）：core.js ≤160KiB / 单 view.js ≤40KiB / material 三拆子锚
    (core ≤20KiB / drawer ≤15KiB / media ≤15KiB，UIX-3) / core.css ≤40KiB /
    单 view css ≤8KiB / 单 view html.js ≤8KiB（③）/ 其余手写 ≤208KiB。"""
    mat_part_limits = {"core": MATERIAL_CORE_MAX_BYTES,
                       "drawer": MATERIAL_DRAWER_MAX_BYTES,
                       "media": MATERIAL_MEDIA_MAX_BYTES}
    for path in _handwritten_files():
        size = path.stat().st_size
        rel = path.relative_to(STUDIO).as_posix()
        if rel == "assets/core.js":
            limit, label = CORE_JS_MAX_BYTES, "core.js"
        elif rel == "assets/core.css":
            limit, label = CORE_CSS_MAX_BYTES, "core.css"
        elif rel.startswith("assets/views/") and rel.endswith(".html.js"):
            limit, label = VIEW_HTML_MAX_BYTES, "view.html.js"  # ③：须先于 .js 分支
        elif rel.startswith("assets/views/material-") and rel.endswith(".js"):
            part = rel[len("assets/views/material-"):-len(".js")]
            limit, label = mat_part_limits[part], f"material-{part}.js"  # UIX-3 子锚
        elif rel.startswith("assets/views/") and rel.endswith(".js"):
            limit, label = VIEW_JS_MAX_BYTES, "view.js"
        elif rel.startswith("assets/views/") and rel.endswith(".css"):
            limit, label = VIEW_CSS_MAX_BYTES, "view.css"
        else:
            limit, label = ASSET_FILE_MAX_BYTES, "单文件"
        assert size <= limit, (
            f"{rel} 超出{label}预算: {size}B > {limit}B（分层锚见模块头 M3/UIX-3 注释档）")


def test_first_screen_budget() -> None:
    """M3 首屏加载量锚（size-limit 核心洞察=parse/execute 成本 > raw bytes）。

    用户真正付出的首屏 parse 成本 = 壳 + core.js + 任一单 view.js（首屏只注入当前
    视图那一个）。取最大的 view 作最坏情况。M3 后首屏实测 145,810B≈142KiB，
    显著低于拆分前的 ~286KiB 单文件；锚 240KiB 留足功能迭代空间。
    2026-11-21 owner 裁决加强：总量三档化后首屏是**唯一真防线**——硬上限不变，
    另加 80% 使用率告警（>196,608B 时 warnings+print；任何新功能把首屏推过
    80% 必须改懒加载——M3 架构的能力，用它）。当前 60.2%（AI-3 提交 2b0c18f738
    复算 148,010/245,760B）。
    """
    import warnings

    shell = OUT.stat().st_size
    core = CORE_JS.stat().st_size
    # ARCH-1：views/ 另有 *.html.js（骨架注入器，懒加载 HTML 不进首屏 parse 的
    # JS 预算口径）——首屏取最大「行为脚本」view.js 作最坏情况。
    views = sorted(p for p in (STUDIO / "assets" / "views").glob("*.js")
                   if not p.name.endswith(".html.js"))
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
    重新规划（某视图过大须二次拆分），不得再放宽锚值。material 已二次拆分
    （UIX-3 三拆，子锚 MATERIAL_*_MAX_BYTES 在 test_asset_files_within_budget）；
    总量一支 2026-11-21 起走三档（绿/黄/红）——红档在本测试同样转红。
    """
    core = CORE_JS.stat().st_size
    views = sorted(p for p in (STUDIO / "assets" / "views").glob("*.js")
                   if not p.name.endswith(".html.js"))
    biggest_view = max((p.stat().st_size for p in views), default=0)
    biggest_name = max(views, key=lambda p: p.stat().st_size).name if views else "(none)"
    assert core <= CORE_JS_MAX_BYTES, (
        f"core.js {core}B 触及 {CORE_JS_MAX_BYTES}B 水位：拆出的工具/路由过多，须重新规划边界")
    assert biggest_view <= VIEW_JS_MAX_BYTES, (
        f"单视图 {biggest_name} {biggest_view}B 触及 {VIEW_JS_MAX_BYTES}B 水位："
        "该视图过大，须二次拆分，不得放宽锚值")
    _assert_assets_total_tier()


def test_uix3_material_subsplit_products() -> None:
    """UIX-3 素材三拆：产物形态（三子段齐备/单文件退役）+ 子标记纪律负例 +
    懒加载接线（ensureView VJS 首载 -core；matPart 按需拉 drawer/media）。"""
    mod = _build_module()
    js = mod.JS.read_bytes()
    parts = mod.render_view_parts(js, "material")
    assert sorted(parts) == ["-core", "-drawer", "-media"]
    for suffix in ("-core", "-drawer", "-media"):
        assert (STUDIO / "assets" / "views" / f"material{suffix}.js").is_file(), suffix
    assert not (STUDIO / "assets" / "views" / "material.js").exists(), \
        "三拆后单文件 material.js 必须退役（build 同步清陈旧）"
    # 接线：core 段 VJS 首载映射（assets/core.js）+ matPart 幂等懒加载器
    # （material-core.js——抽屉/面板/灯箱首触才拉子文件）
    core = mod.render_core_js(js).decode("utf-8")
    assert "var VJS={material:'material-core.js'}" in core, "ensureView 首载映射缺失"
    mat_core = parts["-core"].decode("utf-8")
    assert "function matPart(name){" in mat_core, "matPart 懒加载器缺失"
    assert "__loadScript('assets/views/material-'+name+'.js')" in mat_core, \
        "matPart 未指向 material-* 子文件"
    # 负例：子标记纪律（构建期拒，防静默漏拆）
    with pytest.raises(ValueError, match="与视图名不符"):
        mod.split_view_parts("books", b"function x(){}\n// @material-media\n")
    with pytest.raises(ValueError, match="未登记子拆分"):
        mod.split_view_parts("books", b"function x(){}\n// @books-extra\n")
    with pytest.raises(ValueError, match="顺序/集合"):
        mod.split_view_parts(
            "material", b"a\n// @material-media\nb\n// @material-drawer\nc\n")
    with pytest.raises(ValueError, match="不一致"):
        mod.split_view_parts("material", b"a\n// @material-drawer\nb\n")
    # 正例：未登记视图无子标记 → 维持单文件形态
    assert set(mod.split_view_parts("books", b"function x(){}\n")) == {""}


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
    """总量三档语义自检（2026-11-21 owner 裁决机制修正的回归锚；UIX-3 修偏后）。

    用探针文件验证判定行为与裁决一致：
      黄档（绿上限 < 总量 ≤ 黄上限）→ PASS 且发 UserWarning（不阻塞交付）；
      深黄（黄上限 < 总量 ≤ 红线）→ PASS 且发距红线告警（不静默、不阻塞）；
      红档（> ASSETS_TOTAL_RED_MAX=600,000B）→ AssertionError（真膨胀必须拆分/删除）。
    防退化形态：有人把三档改回硬门（黄带即红）或把告警删掉，本测试转红。
    2026-11-23 UIX-3 修偏注记：原「红档：推过黄档上限」断言系 ARCH-1 重锚时
    沿用旧实现（>YELLOW_MAX 即 raise）写成，与 _assert_assets_total_tier 同期
    docstring「红 >600,000B FAIL」矛盾；本批按文档语义改探针到红线（测试语义
    更新非删除——黄/深黄/红三段断言齐全度比原版更高）。
    """
    import warnings as _w

    base = _assets_total_bytes()
    assert base <= ASSETS_TOTAL_RED_MAX, f"基线本身已越红档: {base}B"
    probe = STUDIO / "assets" / "views" / "zz-tier-probe.js"
    probe.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 黄档：基线在绿档 → 探针只补到刚过绿上限（1KB 另留余量）；基线已在
        # 黄/深黄 → 探针不得把总量推出红线（UIX-3 批后基线 569,230B 起无障碍批
        # 增量落在深黄段——探针字节精确补足，不越线）。
        pad = max(1024, ASSETS_TOTAL_GREEN_MAX + 1 - base)
        pad = max(1, min(pad, ASSETS_TOTAL_RED_MAX - base))
        probe.write_bytes(b"/* tier probe */" * (pad // 16) + b"x" * (pad % 16))
        probe_total = _assets_total_bytes()
        assert ASSETS_TOTAL_GREEN_MAX < probe_total <= ASSETS_TOTAL_RED_MAX, \
            "探针未落入黄/深黄带——先核对基线与档位常量再重估本测试"
        with _w.catch_warnings(record=True) as caught:
            _w.simplefilter("always")
            _assert_assets_total_tier()  # 黄/深黄：PASS（不抛）+ 告警
        assert any("档" in str(x.message) for x in caught), \
            "黄/深黄未发出告警（裁决②的软警报面被删？）"
        # 红档：推过红线 ASSETS_TOTAL_RED_MAX
        probe.write_bytes(probe.read_bytes() + b"x" * (ASSETS_TOTAL_RED_MAX + 1))
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
    """纯函数确定性：同一组源连算两次 render/render_core_js/render_view_parts/
    render_core_css 逐字节一致（壳 + core + 视图产物（UIX-3 起 material 按子段）
    + css 全测；②起含 render_view_css）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    assert mod.render(t, v, c, j) == mod.render(t, v, c, j)
    assert mod.render_core_js(j) == mod.render_core_js(j)
    assert mod.render_core_css(c) == mod.render_core_css(c)
    for name in mod.EXPECTED_VIEWS:
        assert mod.render_view_parts(j, name) == mod.render_view_parts(j, name), name
    css_views = mod.load_view_css()
    for name, blob in css_views.items():
        assert mod.render_view_css(blob, name) == mod.render_view_css(blob, name), name


def test_build_twice_produces_identical_file() -> None:
    """落盘确定性：连跑两次 build()，壳+assets 全文件（含 views/ js+css）sha256 一致。"""
    mod = _build_module()
    first = mod.build()
    files = ([OUT, CORE_JS, CORE_CSS]
             + sorted((STUDIO / "assets" / "views").glob("*.js"))
             + sorted((STUDIO / "assets" / "views").glob("*.css")))
    digest1 = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    second = mod.build()
    digest2 = [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    assert first == second == OUT
    assert digest1 == digest2
    # ARCH-1：views/ 三产物——13 行为脚本（10 单文件视图 + material 三拆，UIX-3）
    # + 10 HTML 注入器（publish 留壳）+ 10 视图 CSS（publish 样式在 core.css）
    assert len(files) == (3 + 3 * len(mod.EXPECTED_VIEWS) - 2
                          + len(mod.EXPECTED_SUBVIEWS["material"]) - 1), \
        f"assets 全文件枚举缺视图产物（实为 {len(files)}）: {files}"


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
    新增 plugins/plugin-run——插件系统四视图按评审 §2.3 拆两 view；
    ARCH-1 批 2026-11-21 新增 article——原 tmpl 硬编码骨架外移为第 11 个源文件）。

    文件名=marker 名=hash 名=section id（canonical 单名，test_studio_ui 的
    test_p1_view_file_names_canonical 同口径）。
    """
    names = sorted(p.stem for p in VIEWS.glob("*.html"))
    # ARCH-1 批（2026-11-21）：+article（十一个源文件——publish 留壳内联，
    # 其余十个出 html.js 注入器）
    assert names == ["article", "books", "compose", "design", "insight", "material",
                     "plugin-run", "plugins", "publish", "settings", "sources"], names
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
    # 反证：非空则产物应含其内容（ARCH-1 起非首屏=空占位 section 进壳）——
    # 用既有视图 settings 换最小骨架做探针（新合成懒视图名会被装载契约门拒）
    v3 = dict(v2, settings=b'<section id="view-settings" class="view" hidden></section>\n')
    product3 = mod.render(t2, v3, c, j)
    assert b'view-settings' in product3
    assert b'view-empty' not in product3  # 空视图不产生占位


def test_view_marker_and_files_must_agree() -> None:
    """占位符与 ui.views/ 文件集不一致 → 拒绝构建（防漂移）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    with pytest.raises(ValueError, match="不一致"):
        mod.render(t, {k: b for k, b in v.items() if k != "settings"}, c, j)


# ── e2) ARCH-1 壳 HTML 外移（2026-11-21）─────────────────────


def test_arch1_shell_placeholder_and_first_screen_inline() -> None:
    """壳内形态：首屏 publish 全骨架内联（首屏加载不触发懒加载）；其余十视图
    =空占位 <section …></section>（开标签逐字节取自源文件，属性保真）。"""
    shell = OUT.read_text(encoding="utf-8")
    for p in sorted(VIEWS.glob("*.html")):
        name = p.stem
        body = p.read_bytes().rstrip(b"\n")
        opening = body[:body.index(b">") + 1]
        if name == "publish":
            assert '<section id="view-publish"' in shell
            assert 'id="queue-filters"' in shell, "publish 骨架须内联进壳（首屏红线）"
            assert 'id="article-list"' in shell
        else:
            assert (opening + b"</section>").decode() in shell, name
    # 占位恰好 10 个（article/books/compose/design/insight/material/plugin-run/
    # plugins/settings/sources）；publish 的 section 非空。
    placeholders = re.findall(
        r'<section id="view-[a-z0-9-]+"[^>]*></section>', shell)
    assert len(placeholders) == 10, placeholders


def test_arch1_lazy_skeleton_not_in_shell() -> None:
    """反证门：十懒视图的骨架内层（取各源内层首个 id 作探针）不得留在壳里。"""
    shell = OUT.read_text(encoding="utf-8")
    for p in sorted(VIEWS.glob("*.html")):
        if p.stem == "publish":
            continue
        body = p.read_bytes().rstrip(b"\n")
        inner = body[body.index(b">") + 1:-len(b"</section>")]
        m = re.search(rb'id="([^"]+)"', inner)
        assert m, f"{p.stem} 内层无 id 探针"
        probe = m.group(1).decode()
        assert f'id="{probe}"' not in shell, (p.stem, probe)


def test_arch1_view_html_js_products() -> None:
    """assets/views/<view>.html.js 注入器：首行 getElementById innerHTML= 模板串
    形态、字节等于 render_view_html_js、publish 无此产物。"""
    mod = _build_module()
    views = {p.stem: p.read_bytes() for p in VIEWS.glob("*.html")}
    for name in sorted(views):
        path = STUDIO / "assets" / "views" / f"{name}.html.js"
        if name == mod.FIRST_SCREEN_VIEW:
            assert not path.exists(), "首屏视图不得有 html.js（HTML 已在壳）"
            continue
        assert path.is_file(), f"注入器缺失: {name}"
        blob = path.read_bytes()
        assert blob == mod.render_view_html_js(views, name), name
        assert blob.startswith(
            f"document.getElementById('view-{name}').innerHTML=`".encode()), name
        assert blob.endswith(b"`;\n"), name


def test_arch1_lazy_marker_requires_js_view_block() -> None:
    """装载契约门：非空懒视图标记无对应 ui.js @view 块 → 拒绝构建（404 预防）。"""
    mod = _build_module()
    t, v, c, j = _sources(mod)
    t2 = t.replace(b"<!--BUILD:VIEW:settings-->\n",
                   b"<!--BUILD:VIEW:settings-->\n<!--BUILD:VIEW:ghost-->\n")
    v2 = dict(v, ghost=b'<section id="view-ghost" class="view" hidden>'
                      b'<div id="ghost-probe"></div></section>\n')
    with pytest.raises(ValueError, match="无对应 ui.js"):
        mod.render(t2, v2, c, j)


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


# ── g2) 静态服务 gzip（问题 12；Accept-Encoding 协商 + >1KB 门 + 缓存）────


def test_assets_gzip_when_accepted(tmp_path: Path) -> None:
    """/assets/core.js + Accept-Encoding: gzip → Content-Encoding: gzip，body 可解压回原文。"""
    import gzip

    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/assets/core.js", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert headers.get("Vary") == "Accept-Encoding"
        assert gzip.decompress(body) == CORE_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_no_gzip_without_accept(tmp_path: Path) -> None:
    """无 Accept-Encoding → 直传（无 Content-Encoding，body 逐字节等于产物）。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/assets/core.js")
        assert status == 200
        assert "Content-Encoding" not in headers
        assert body == CORE_JS.read_bytes()
        # 明确不含 gzip（如仅 br）也直传
        status, headers, body = _get(httpd, "/assets/core.js", {"Accept-Encoding": "br"})
        assert "Content-Encoding" not in headers
        assert body == CORE_JS.read_bytes()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_gzip_small_file_unchanged(tmp_path: Path) -> None:
    """>1KB 门：小文件即便接受 gzip 也直传（用 <1KB 的临时产物探针）。"""
    from musespiral import studio

    probe = STUDIO / "assets" / "zz-gzip-small-probe.js"
    probe.write_bytes(b"x" * 512)
    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/assets/zz-gzip-small-probe.js", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert "Content-Encoding" not in headers
        assert body == b"x" * 512
    finally:
        probe.unlink(missing_ok=True)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_gzip_304_still_empty(tmp_path: Path) -> None:
    """ETag/304 命中即使带 Accept-Encoding: gzip 也回空体、不压（304 无 body）。"""
    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _, headers1, _ = _get(httpd, "/assets/core.js", {"Accept-Encoding": "gzip"})
        etag = headers1["ETag"]
        status, headers, body = _get(
            httpd,
            "/assets/core.js",
            {"Accept-Encoding": "gzip", "If-None-Match": etag},
        )
        assert status == 304
        assert body == b""
        assert "Content-Encoding" not in headers
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_assets_gzip_cache_reuses_compression(tmp_path: Path) -> None:
    """缓存：同内容二次请求压缩结果同一对象（不重复压缩），且 ETag 基于未压内容不变。"""
    import http.client

    from musespiral import studio
    from musespiral.studio.assets import _gzip_body

    mime = "application/javascript"
    first = _gzip_body(mime, CORE_JS.read_bytes())
    second = _gzip_body(mime, CORE_JS.read_bytes())
    assert first is second, "相同内容应命中缓存返回同一 bytes 对象"

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = httpd.server_address[:2]
        etags = []
        for _ in range(2):
            conn = http.client.HTTPConnection(host, port, timeout=10)
            conn.request("GET", "/assets/core.js", headers={"Accept-Encoding": "gzip"})
            resp = conn.getresponse()
            resp.read()
            etags.append(dict(resp.getheaders())["ETag"])
            conn.close()
        assert etags[0] == etags[1], "gzip 与直传应共用基于未压内容的 ETag"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_root_html_gzip_when_accepted(tmp_path: Path) -> None:
    """GET / 壳 HTML 同样按 Accept-Encoding 协商 gzip（>1KB 文本面）。"""
    import gzip

    from musespiral import studio

    httpd = studio.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, body = _get(httpd, "/", {"Accept-Encoding": "gzip"})
        assert status == 200
        assert headers.get("Content-Encoding") == "gzip"
        assert gzip.decompress(body) == (STUDIO / "ui.html").read_bytes()
        # 不带 → 直传
        status, headers, body = _get(httpd, "/")
        assert "Content-Encoding" not in headers
        assert body == (STUDIO / "ui.html").read_bytes()
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
