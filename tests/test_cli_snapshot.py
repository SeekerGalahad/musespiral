"""CLI --help 快照测试（R1 行为保持基线，正典 §四-R1 / §五-轮次一组A）。

对 `musespiral --help` 与 24 个命令组/命令的 --help 输出做逐行固化断言。
基线于拆分前（cli.py 单文件 804 行，HEAD 1ff6d9f0）实测捕获；
cli.py → commands/ 八文件搬迁后输出必须逐字节一致；
轮次二新增 status/jobs、轮次三新增 imagehost/fetch 均为行为新增（非漂移），
基线按注册后实测扩展（只增不删；root 清单因最长命令名变 imagehost 列宽
10→11，既有行仅 padding 变化，命令名与描述文字不变）；
第十期新增 cards/poster（F22/F23）同为行为新增，基线按注册后实测扩展
（只增不删；两命令名均短于 imagehost，root 清单列宽不变、既有行零变化）。
第十二期新增 discover 命令组（F26-F29）同为行为新增，基线按注册后实测扩展
（只增不删；discover 短于 imagehost，root 清单列宽不变、既有行零变化）。
第十三期新增 cover（F31，Group A）同为行为新增，基线按注册后实测扩展
（只增不删；cover 短于 imagehost，root 清单列宽不变、既有行零变化）。
第十三期集成阶段：title（F32，Group B）注册入册新增条目；check/generate/
digest/adapt/pack 五命令因 F34 批量化参数形变 ARTICLE→ARTICLES...（check
另新增 --platform 预检项、pack --variant 帮助追加单文件限定）——五条目按
注册后实测捕获修正（title 短于 imagehost，root 清单列宽不变、既有行零变化；
check 的 --platform 选项行属设计内新增，选项列宽随之重排为预期——既有
--render 行仅 padding 变化，描述文字不变）。
第十四期 Group B 参数行修正（正典 §七「既有条目零变化（参数行除外）」）：
adapt/pack 帮助追加 --out/--outdir 单文件限定（C2）、pack 新增 --force
（F37 断点续传）、cover --platform 选项引 cover_service.platform_choices()
且帮助文本去字面尺寸（C6）——三条目按改后实测捕获修正，其余行零变化。
第十四期集成阶段注册 plan/log（F36/F36c，Group A）与 new（F38，Group B）
——行为新增非漂移，基线按注册后实测扩展（只增不删；三命令名均短于
imagehost，root 清单列宽不变、既有行零变化）。
第十六期集成阶段注册 restore（F43，Group A）——行为新增非漂移，基线按
注册后实测扩展（只增不删；restore 七字符短于 imagehost，root 清单列宽
不变、既有行零变化，实测核对 added=1/removed=0）；同期 F42 平台扩列
（掘金/B站，Group B）使 cover 条目 --platform 选项由 cover_service.
platform_choices() 动态生成多出 juejin|bilibili——该行按注册后实测
捕获修正，cover 其余行与既有全部条目零变化。
第十七期 Group B：ai 组新增 check 子命令（F45 供应商无关连通检查，注册于
ai_cmds 内未触 cli.py）——行为新增非漂移，基线按注册后实测扩展（只增不删；
check 短于 check-facts，组清单列宽不变、既有行零变化，实测核对 added=1/
removed=0）。
第十七期集成阶段注册 style（F44 个人风格资产，Group A）——行为新增非漂移，
基线按注册后实测扩展（只增不删；style 五字符短于 imagehost，root 清单列宽
不变、既有行零变化，实测核对 added=1/removed=0）。
第十八期 Group A 在 config 组内注册 kb 子组（F46 KB 源设置，注册于
config_cmds 未触 cli.py）——行为新增非漂移，基线按注册后实测扩展（只增
不删；kb 两字符短于 list，组清单列宽不变、既有行零变化，实测核对
added=1/removed=0）。
第十八期集成阶段注册 compose（F48-F52 创作台，Group B）——行为新增非漂移，
基线按注册后实测扩展（只增不删；compose 七字符短于 imagehost，root 清单
列宽不变、既有行零变化，实测核对 added=1/removed=0）；同期 P17 残留清偿：
title --apply 帮助措辞改版本池（旧版入 .md.bak.<时间戳> 滚动版本池，restore
可恢复）——该选项行按改后实测捕获修正（原单行随字数换行为两行，属参数行
修正，其余行零变化）。
Phase D M3 注册 topic-radar（F78 选题雷达，单命令）——行为新增非漂移，
基线按注册后实测扩展（只增不删；topic-radar 十一字符长于 imagehost，root
清单列宽 9→11、既有行仅 padding 变化——imagehost 判例同款，命令名与描述
文字零变化，新增条目一条）。
Phase D M4 在 ai 组内注册 cache/usage/budget 三运维子命令（F79.2，注册于
ai_cmds 未触 cli.py）——行为新增非漂移，基线按注册后实测扩展（只增不删；
三名均短于 check-facts，组清单列宽不变、既有行零变化，实测核对 added=3）。
插件系统集成轮（复核 P4 回收面）注册 plrun（插件流水线 run 目录 list/prune，
2026-11-20）——行为新增非漂移，基线按注册后实测扩展（只增不删；plrun 五字符
短于 imagehost，root 清单列宽不变、既有行零变化，实测核对 added=1/removed=0）。
click 8.5 CliRunner 固定排版宽度（COLUMNS 不影响输出），prog_name 显式
钉为 musespiral（与 pyproject console-script 入口渲染一致）。
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from musespiral.cli import cli

# 基线：拆分前实测（--help 全文，含首尾换行）
EXPECTED: dict[str, str] = {
    '<root>': 'Usage: musespiral [OPTIONS] COMMAND [ARGS]...\n\n  musespiral — 开源内容流水线：素材进来，成稿出去，每个 AI 判断你都能改。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  adapt         一稿多态：把文章改写成指定平台的文字变体（仅产出本地文件）。\n  agent         多步 AI 编排：计划清单 / 触发综合 / 运行记录 / 采纳（Phase D）。\n  ai            AI 辅助改写（OpenAI 兼容接口，仅生成本地文件，不自动发布）。\n  backup        打包文章库与 ~/.musespiral（除 cookies/）为 tar.gz 并轮转（F35）。\n  booksource    书源库：导入/订阅刷新/索引（引擎缺席时全功能可用，搜索靠 bookengine）。\n  cards         生成小红书图文卡组：封面卡 + 内容卡 N + 结尾卡（仅产出本地文件）。\n  check         运行发布前审核清单（--render 追加渲染级自动验收）。\n  compose       创作台：多素材分块 AI 起草 + 逐块三态裁决 + 采纳入稿。\n  config        管理公众号凭证配置。\n  cover         生成封面：五路线派生目标平台规格 PNG（仅产出本地文件）。\n  design        视觉设计稿：渲染/存稿/导出/信息图/预设/模板清单（纯本地产出）。\n  digest        生成备选摘要候选（AI 优先，未配置回退启发式两条）。\n  discover      知识库选题引擎：扫描候选、选题建议、生成文章骨架。\n  fetch         抓取公众号文章 URL 转 Markdown 草稿（失效链接给可读报错）。\n  generate      解析 Markdown 并生成本地 HTML（不调用任何 API）。\n  imagehost     管理图床配置（github/local；文章 frontmatter imagehost 可覆盖默认）。\n  jobs          查看与清理任务队列（~/.musespiral/jobs.jsonl，Studio 后台动作的记录）。\n  log           发布日志人工登记（F36c：mp 终发是人工后台点击，登记后周历归位）。\n  media         AI 文生图（G1：SiliconFlow 先行，FLUX.1-schnell 免费档起步）。\n  new           从模板新建文章（F38）：占位符替换 + frontmatter 补齐，进入 check 流水线。\n  notes         本地 Markdown 知识库：扫描笔记、生成待审核文章。\n  pack          生成 manual_pack 物料包（README/payload/checks/cover 四件套）。\n  pipeline      一键金路径：check→title→cover→adapt→pack 五步串行（E1，失败即停）。\n  plan          排期周历（F36）：计划/已交付/已发布三态文字视图，只读不改。\n  platform      多平台发布层：平台能力矩阵。\n  plrun         插件流水线 run 目录：list 清单 / prune 回收（LRU 保最近 N 次）。\n  poster        生成长图：文章 → 1080 宽竖版长图（HTML 模板 + 浏览器全页截图）。\n  preview       启动本地 HTTP 服务器，在浏览器预览渲染效果。\n  publish       上传图片并创建草稿（默认仅建草稿，不自动发表）。\n  quality-loop  质量循环：检查 → AI 修复 → 再检查。\n  restore       列出文章的版本池（F43）；--apply N 以第 N 版覆盖原文。\n  review        生成原笔记 ↔ 文章对照审阅 HTML（纯本地生成，不调 AI）。\n  rewrite       用 AI 把笔记改写成文章（产出本地文件，需人工审核后才发布）。\n  site          已发布文章 → 零 JS 纯静态站（HTML + index + RSS，内容自主权）。\n  stats         汇总发布日志（累计/本月草稿与发表数、最近条目）。\n  status        查看/迁移文章状态（draft/reviewed/published/archived）。\n  studio        启动 Studio 本地工作台（队列/详情/仪表盘三视图，仅回环地址）。\n  style         个人风格资产（定稿范文样例；纯本地，永不入 git、永不上云）。\n  submit        提交草稿发表（freepublish，需二次确认）。\n  tags          素材标签建议（Phase D F77）：AI 建议，人工采纳才写 triage.tags。\n  theme         主题管理。\n  title         生成备选标题候选（AI 优先，未配置回退启发式；--platform 按字限过滤）。\n  topic-radar   选题雷达：radar/KB/星标三源聚合的「本期值得写什么」快照（Phase D F78）。\n  tts           把文章正文朗读成 MP3（<stem>.mp3 落文章目录，零 API key）。\n  upload        浏览器上传器：填表交给机器、点发布留给人（需 musespiral[browser]）。\n',
    'generate': 'Usage: musespiral generate [OPTIONS] ARTICLES...\n\n  解析 Markdown 并生成本地 HTML（不调用任何 API）。\n\nOptions:\n  --theme TEXT       覆盖主题（lapis/default/phycat）\n  --code-theme TEXT  代码高亮主题（Pygments 样式）\n  --dark             使用暗色配色\n  --help             Show this message and exit.\n',
    'preview': 'Usage: musespiral preview [OPTIONS] ARTICLE\n\n  启动本地 HTTP 服务器，在浏览器预览渲染效果。\n\nOptions:\n  --port INTEGER  预览端口\n  --dark          暗色预览\n  --help          Show this message and exit.\n',
    'check': 'Usage: musespiral check [OPTIONS] ARTICLES...\n\n  运行发布前审核清单（--render 追加渲染级自动验收）。\n\nOptions:\n  --render         追加渲染级自动验收（外链残留/图片外域/封面比例/非法标签）\n  --platform TEXT  按平台规格预检（字限/图数/封面三项只对该平台生效）\n  --help           Show this message and exit.\n',
    'review': 'Usage: musespiral review [OPTIONS] ARTICLE\n\n  生成原笔记 ↔ 文章对照审阅 HTML（纯本地生成，不调 AI）。\n\nOptions:\n  --note FILE     原笔记路径（对照基准）  [required]\n  -o, --out PATH  输出 HTML 路径（默认 <article>.review.html）\n  --help          Show this message and exit.\n',
    'digest': 'Usage: musespiral digest [OPTIONS] ARTICLES...\n\n  生成备选摘要候选（AI 优先，未配置回退启发式两条）。\n\nOptions:\n  --apply INTEGER RANGE  采用第 N 条候选写回 frontmatter.digest  [1<=x<=3]\n  --help                 Show this message and exit.\n',
    'publish': 'Usage: musespiral publish [OPTIONS] ARTICLE\n\n  上传图片并创建草稿（默认仅建草稿，不自动发表）。\n\nOptions:\n  --account TEXT  公众号账号名（使用默认账号可不填）\n  --skip-check    跳过审核检查（不推荐）\n  --help          Show this message and exit.\n',
    'submit': 'Usage: musespiral submit [OPTIONS] ARTICLE\n\n  提交草稿发表（freepublish，需二次确认）。\n\nOptions:\n  --draft-id TEXT  草稿 media_id  [required]\n  --help           Show this message and exit.\n',
    'stats': 'Usage: musespiral stats [OPTIONS]\n\n  汇总发布日志（累计/本月草稿与发表数、最近条目）。\n\nOptions:\n  --sync  先调微信图文统计接口回填阅读/分享数（需认证服务号）\n  --help  Show this message and exit.\n',
    'platform': 'Usage: musespiral platform [OPTIONS] COMMAND [ARGS]...\n\n  多平台发布层：平台能力矩阵。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  list  列出已注册平台的能力矩阵（平台/代码/通道/能力/限制摘要）。\n',
    'adapt': 'Usage: musespiral adapt [OPTIONS] ARTICLES...\n\n  一稿多态：把文章改写成指定平台的文字变体（仅产出本地文件）。\n\nOptions:\n  --platform TEXT  目标平台代码（xhs/channels/douyin/zhihu）  [required]\n  -o, --out PATH   输出路径（默认 <stem>.<platform>.md，存在即拒绝覆盖；仅单文件调用合法）\n  --help           Show this message and exit.\n',
    'pack': 'Usage: musespiral pack [OPTIONS] ARTICLES...\n\n  生成 manual_pack 物料包（README/payload/checks/cover 四件套）。\n\nOptions:\n  --platform TEXT    目标平台代码（channels/xhs/zhihu/douyin）  [required]\n  --variant PATH     adapt 产物路径（payload 优先取其内容，缺失回退原文；仅单文件调用合法）\n  -o, --outdir PATH  物料包目录（默认 <stem>.<platform>.pack/，存在即拒绝覆盖；仅单文件调用合法）\n  --force            已交付平台也强制重做物料包（F37 断点续传前置检查的豁免闸）\n  --help             Show this message and exit.\n',
    'upload': 'Usage: musespiral upload [OPTIONS] COMMAND [ARGS]...\n\n  浏览器上传器：填表交给机器、点发布留给人（需 musespiral[browser]）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  login    打开浏览器扫码登录平台，保存登录态到 ~/.musespiral/cookies/。\n  publish  打开创作者平台自动填表，人工核对后自行点「发布」（永不代点）。\n',
    'notes': 'Usage: musespiral notes [OPTIONS] COMMAND [ARGS]...\n\n  本地 Markdown 知识库：扫描笔记、生成待审核文章。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  draft  把一篇笔记转成待审核文章（保留正文，补齐 frontmatter）。\n  list   列出知识库中可发布的候选笔记（目录扫描，或 --db 直连 SQLite 笔记库）。\n',
    'ai': 'Usage: musespiral ai [OPTIONS] COMMAND [ARGS]...\n\n  AI 辅助改写（OpenAI 兼容接口，仅生成本地文件，不自动发布）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  budget       AI 日预算（全局 + per_task 覆盖，--set 点路径调整；与 ai.json 分离）。\n  cache        AI 输出缓存概览（~/.musespiral/cache/ai；条目只存输出与元数据，无 prompt 正文）。\n  check        逐任务连通检查：最小真实请求「回复：OK」级，汇总 任务×档案×通/不通（F45）。\n  check-facts  AI 事实自检：列出文章中无原笔记依据的增量事实（只标记不否决）。\n  config       配置 AI 档案（多提供商，保存于 ~/.musespiral/ai.json；首个档案自动成为默认）。\n  learn        从底稿↔终稿 diff 学习改稿习惯（追加到 ~/.musespiral/habits.json）。\n  list         列出全部档案（key 掩码）、默认项与任务路由。\n  route        设置任务级路由（该任务固定用某档案，如 check_facts 走本地 Ollama）。\n  show         查看当前 AI 配置（隐藏 key）。\n  usage        AI 用量日汇总：行=任务×档案，列=调用/命中/命中率/字符（读侧现算不回写）。\n  use          切换默认 AI 档案。\n',
    'rewrite': 'Usage: musespiral rewrite [OPTIONS] NOTE\n\n  用 AI 把笔记改写成文章（产出本地文件，需人工审核后才发布）。\n\nOptions:\n  -o, --out PATH   输出的文章路径  [required]\n  --words INTEGER  目标字数\n  --theme TEXT     排版主题\n  --cover TEXT     封面图路径\n  --help           Show this message and exit.\n',
    'theme': 'Usage: musespiral theme [OPTIONS] COMMAND [ARGS]...\n\n  主题管理。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  learn  从公众号文章 HTML 学习配色/字号生成主题（频次归一 + extends: default）。\n  list   列出全部主题（内置 ∪ 用户目录，标注来源）。\n  new    生成用户主题骨架（~/.musespiral/themes/<name>.yaml，含 extends 继承）。\n',
    'config': 'Usage: musespiral config [OPTIONS] COMMAND [ARGS]...\n\n  管理公众号凭证配置。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  kb    KB 源设置（发现页筛选与选题扫描边界；存 ~/.musespiral/kb_sources.json）。\n  list  列出已配置的公众号账号。\n  set   设置 appid 或 secret。\n',
    'studio': 'Usage: musespiral studio [OPTIONS]\n\n  启动 Studio 本地工作台（队列/详情/仪表盘三视图，仅回环地址）。\n\nOptions:\n  --root DIRECTORY  工作目录（文章/产物/发布日志的边界）  [required]\n  --port INTEGER    工作台端口（仅绑定 127.0.0.1）\n  --db PATH         预留数据库路径（透传 studio.make_server）\n  --no-open         不自动打开浏览器（launchd 常驻场景用，见 deploy/ 模板）\n  --help            Show this message and exit.\n',
    # 轮次二新增命令（R3/R4，行为新增非漂移——基线按注册后实测捕获）
    'status': 'Usage: musespiral status [OPTIONS] ARTICLE\n\n  查看/迁移文章状态（draft/reviewed/published/archived）。\n\nOptions:\n  --set [reviewed|draft|archived]\n                                  人工迁移状态（published 只由 publish 成功自动置位）\n  --help                          Show this message and exit.\n',
    'jobs': 'Usage: musespiral jobs [OPTIONS] COMMAND [ARGS]...\n\n  查看与清理任务队列（~/.musespiral/jobs.jsonl，Studio 后台动作的记录）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  clean  清理已完成/失败任务行（done/failed；其余行字节级保留）。\n  list   最近 20 条任务（新在前；只读）。\n',
    # 轮次三新增命令（R5/R6，行为新增非漂移——基线按注册后实测捕获）
    'fetch': 'Usage: musespiral fetch [OPTIONS] URL\n\n  抓取公众号文章 URL 转 Markdown 草稿（失效链接给可读报错）。\n\nOptions:\n  --html FILE     本地 HTML 文件路径（离线解析优先，不联网；测试/冒烟用）\n  -o, --out PATH  输出的文章草稿路径  [required]\n  --help          Show this message and exit.\n',
    'imagehost': 'Usage: musespiral imagehost [OPTIONS] COMMAND [ARGS]...\n\n  管理图床配置（github/local；文章 frontmatter imagehost 可覆盖默认）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  config  写入图床配置 ~/.musespiral/imagehost.json（token 不回显）。\n  list    列出已配置图床与默认项（token 掩码显示）。\n',
    # 第十期新增命令（F22/F23，行为新增非漂移——基线按注册后实测捕获）
    'cards': 'Usage: musespiral cards [OPTIONS] ARTICLE\n\n  生成小红书图文卡组：封面卡 + 内容卡 N + 结尾卡（仅产出本地文件）。\n\nOptions:\n  --variant PATH     adapt xhs 变体路径（切卡走变体路径；不存在即报错，拒绝静默回退）\n  --ratio [3:4|4:5]  卡规格（默认 3:4 即 1242×1656）\n  --html-only        不截图，只产出卡组单文件 HTML（无需浏览器）\n  -o, --outdir PATH  输出目录（默认 <stem>.cards/，存在即拒绝覆盖）\n  --help             Show this message and exit.\n',
    'poster': 'Usage: musespiral poster [OPTIONS] ARTICLE\n\n  生成长图：文章 → 1080 宽竖版长图（HTML 模板 + 浏览器全页截图）。\n\nOptions:\n  --html-only     不截图，只产出长图 HTML（无需浏览器）\n  -o, --out PATH  输出路径（默认 <stem>.poster.png；--html-only 时 <stem>.poster.html）\n  --help          Show this message and exit.\n',
    # 第十二期新增命令组（F26-F29 知识库选题引擎，行为新增非漂移——基线按注册后实测捕获）
    'discover': 'Usage: musespiral discover [OPTIONS] COMMAND [ARGS]...\n\n  知识库选题引擎：扫描候选、选题建议、生成文章骨架。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  draft    从一篇知识库文档生成文章骨架（F29；同名已存在拒绝覆盖）。\n  scan     扫描知识库，列出可发布性评分达标的候选选题（F26）。\n  suggest  从候选中挑 Top-N，输出选题角度建议（F28，--ai 可选增强）。\n',
    # F35 文章库备份（独立先行切片，行为新增非漂移——基线按注册后实测捕获）
    'backup': 'Usage: musespiral backup [OPTIONS]\n\n  打包文章库与 ~/.musespiral（除 cookies/）为 tar.gz 并轮转（F35）。\n\nOptions:\n  --output DIRECTORY    备份目录（默认 ~/musespiral-backups/）\n  --keep INTEGER RANGE  轮转保留份数（最旧的超额份自动删除）  [default: 5; x>=1]\n  --help                Show this message and exit.\n',
    # 第十三期新增命令（F31 封面生成，Group A——行为新增非漂移，基线按注册后实测捕获）
    'cover': 'Usage: musespiral cover [OPTIONS] ARTICLE\n\n  生成封面：五路线派生目标平台规格 PNG（仅产出本地文件）。\n\nOptions:\n  --title-card                    标题字卡封面（默认；标题+一句钩子，无浏览器时 Pillow 兜底直绘）\n  --from-cards                    取 .cards/ 首卡中心裁目标规格（需先 musespiral cards）\n  --from-poster                   取 <stem>.poster.png 头区裁目标规格（需先 musespiral\n                                  poster）\n  --from-image FILE               用户源图 PIL 中心裁+缩放至目标规格（--from-image <path>\n                                  即选路线又给源图）\n  --from-design                   读 <stem>.design/cover.svg → B 路光栅 → 规格（F71\n                                  设计稿桥；需先 musespiral design save）\n  --platform [mp|xhs|channels|douyin|juejin|bilibili]\n                                  目标平台规格（默认 mp 落 cover.png；其余落\n                                  cover.<platform>.png，规格以 platform list 为准）\n  --no-write-back                 只落封面图，不写回 frontmatter（也不入 .md.bak.<时间戳> 滚动版本池）\n  --help                          Show this message and exit.\n',

    'design': 'Usage: musespiral design [OPTIONS] COMMAND [ARGS]...\n\n  视觉设计稿：渲染/存稿/导出/信息图/预设/模板清单（纯本地产出）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  export          导出 PNG（B 主 C 兜底；尺寸恒精确；kind=cover 时 mp 写回 frontmatter）。\n  infographic     信息图抽数预览（F73；打印有效/跳过行，产物经 design export --kind infographic）。\n  list-templates  列出内置模板（id/kind/适用规格）——F74 注册表数据面（CLI 版）。\n  preset          我的模板预设（~/.musespiral/visual_presets.d/*.json——纯 JSON，零代码）。\n  render          渲染设计稿为 SVG（校验+测量分行+白名单序列化）。\n  save            存稿：<stem>.design/design.json + 可再生 SVG（版本池先行+原子写）。\n',

    # 第十三期新增命令（F32 标题多候选优选，Group B——集成阶段注册，基线按注册后实测捕获）
    'title': 'Usage: musespiral title [OPTIONS] ARTICLE\n\n  生成备选标题候选（AI 优先，未配置回退启发式；--platform 按字限过滤）。\n\nOptions:\n  --platform TEXT  按平台字限过滤候选（mp/xhs/channels/douyin/zhihu）\n  --apply INTEGER  采用第 N 条候选写回 frontmatter.title（旧版入 .md.bak.<时间戳> 滚动版本池，restore\n                   可恢复）\n  --help           Show this message and exit.\n',
    # 第十四期新增命令（F36/F36c 排期周历与人工登记，Group A——集成阶段注册，基线按注册后实测捕获）
    'plan': 'Usage: musespiral plan [OPTIONS]\n\n  排期周历（F36）：计划/已交付/已发布三态文字视图，只读不改。\n\nOptions:\n  --week YYYY-Www  回看指定 ISO 周（如 2026-W47）；缺省本周\n  --help           Show this message and exit.\n',
    'log': 'Usage: musespiral log [OPTIONS] COMMAND [ARGS]...\n\n  发布日志人工登记（F36c：mp 终发是人工后台点击，登记后周历归位）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  add  人工登记一次发表：追加 manual:true 的 publish 行（ts=now）。\n',
    # 第十四期新增命令（F38 用户文章模板，Group B——集成阶段注册，基线按注册后实测捕获）
    'new': 'Usage: musespiral new [OPTIONS] SLUG\n\n  从模板新建文章（F38）：占位符替换 + frontmatter 补齐，进入 check 流水线。\n\nOptions:\n  --template TEXT   模板名（<root>/templates/<name>.md；缺省 default，缺失自动生成）\n  --series TEXT     系列名（替换 {{series}} 占位；缺省空串）\n  --root DIRECTORY  文章库根（默认 ~/musespiral-articles，与 backup/discover 同源）\n  --help            Show this message and exit.\n',
    # 第十六期新增命令（F43 编辑级版本回滚，Group A——集成阶段注册，基线按注册后实测捕获）
    'restore': 'Usage: musespiral restore [OPTIONS] ARTICLE\n\n  列出文章的版本池（F43）；--apply N 以第 N 版覆盖原文。\n\nOptions:\n  --apply INTEGER  恢复第 N 个版本（当前版先入版本池——恢复可再恢复）\n  --help           Show this message and exit.\n',
    # 第十七期新增命令组（F44 个人风格资产，Group A——集成阶段注册，基线按注册后实测捕获）
    'style': 'Usage: musespiral style [OPTIONS] COMMAND [ARGS]...\n\n  个人风格资产（定稿范文样例；纯本地，永不入 git、永不上云）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  init  从已发布/已审核定稿抽取范文段，生成 ~/.musespiral/style_samples.md（无定稿则生成模板）。\n',
    # 第十八期新增命令组（F48-F52 创作台，Group B——集成阶段注册，基线按注册后实测捕获）
    'compose': 'Usage: musespiral compose [OPTIONS] COMMAND [ARGS]...\n\n  创作台：多素材分块 AI 起草 + 逐块三态裁决 + 采纳入稿。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  add       素材入池（章节勾选校验复用发现页 scanner；重复添加=更新勾选）。\n  adopt     采纳入稿：三态合并 → draft.md（旧稿入版本池）→ 正稿入队列 + habits 学习。\n  decide    逐块三态裁决（keep 保留 / adopt_edited 改后采纳 / discard 丢弃+方向）。\n  generate  AI 按反馈再生成一轮（prompt 管线全插槽化；产出落 rounds/N.md 预览）。\n  new       新建创作会话（<slug>.compose/ 自包含：session.json + 空 draft.md）。\n  status    会话清单（无 slug）或单会话详情（素材/轮次/裁决进度）。\n',
    # Phase D 新增命令组（F77 标签建议，甲组——集成阶段注册，基线按注册后实测捕获；
    # tags 四字符短于 imagehost，root 清单列宽不变、既有行零变化）
    'tags': 'Usage: musespiral tags [OPTIONS] COMMAND [ARGS]...\n\n  素材标签建议（Phase D F77）：AI 建议，人工采纳才写 triage.tags。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  batch    批量建议：无标签素材逐个确认（y=采纳/n=拒绝/s=跳过；Ctrl-C 保留已确认）。\n  suggest  单篇建议：AI 出 ≤5 个标签，勾选编号采纳（直接回车=全拒零落盘）。\n',
    # Phase D 新增命令组（F75/F76 多步编排，乙组——集成阶段注册，基线按注册后实测捕获；
    # agent 五字符短于 imagehost，root 清单列宽不变、既有行零变化）
    'agent': 'Usage: musespiral agent [OPTIONS] COMMAND [ARGS]...\n\n  多步 AI 编排：计划清单 / 触发综合 / 运行记录 / 采纳（Phase D）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  adopt  采纳一次 done 的 synthesis run（唯一外部写面；同 run 二次采纳幂等）。\n  plans  列出内置计划（名/步数区间/预算/一句话用途）。\n  run    触发一次编排（构造期校验 → job → 步级进度 → 摘要与预览）。\n  runs   运行记录：清单（无参）/ 单 run 详情（含各步 out_text 预览）/ --prune 清理。\n',
    # Phase D 新增命令（F78 选题雷达，M3——集成阶段注册，基线按注册后实测捕获；
    # topic-radar 十一字符长于 imagehost，root 清单列宽 9→11、既有行仅 padding
    # 变化（imagehost 判例同款），命令名与描述文字零变化；新增行一条）
    'topic-radar': 'Usage: musespiral topic-radar [OPTIONS]\n\n  选题雷达：radar/KB/星标三源聚合的「本期值得写什么」快照（Phase D F78）。\n\n  无快照时自动重算；--refresh 强制重算入史；--no-ai 跳过 AI 增强。\n\nOptions:\n  --refresh  强制重算并覆盖 latest（新快照 append 入史，≤20 份超限删最旧）\n  --no-ai    跳过 AI 增强（top5 候选的 ↳ AI 角度行）\n  --help     Show this message and exit.\n',
    # G1 组合批新增命令组（2026-11-17 文生图，media 域档案独立命名空间——集成
    # 阶段注册，基线按注册后实测捕获；media 五字符短于 topic-radar，root 清单
    # 列宽不变、既有行零变化，实测核对 added=1）
    'media': 'Usage: musespiral media [OPTIONS] COMMAND [ARGS]...\n\n  AI 文生图（G1：SiliconFlow 先行，FLUX.1-schnell 免费档起步）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  config  配置 media 域档案并设为生效档案（不动 chat 域 default/routing）。\n  list    列出 media 档案（key 掩码）+ 健康快照。\n  verify  连接验证（health 三态：ok/down/unconfigured）。\n',
    # E1 组合批新增命令组（2026-11-17 一键金路径——集成阶段注册，基线按注册后
    # 实测捕获；pipeline 八字符短于 topic-radar，root 清单列宽不变、既有行零
    # 变化，实测核对 added=1：root 一行 pipeline + 本条目）
    'pipeline': 'Usage: musespiral pipeline [OPTIONS] COMMAND [ARGS]...\n\n  一键金路径：check→title→cover→adapt→pack 五步串行（E1，失败即停）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  run  执行五步金路径（提交 job → 步级进度 → 步表与产物清单）。\n',
    # R0 书源功能新增命令组（2026-11-18 架构设计 §四：import/list/refresh/subs——
    # 集成阶段注册，基线按注册后实测捕获；booksource 十字符长度介于
    # pipeline(8) 与 topic-radar(11) 之间，root 清单列宽不变、既有行零变化）
    'booksource': 'Usage: musespiral booksource [OPTIONS] COMMAND [ARGS]...\n\n  书源库：导入/订阅刷新/索引（引擎缺席时全功能可用，搜索靠 bookengine）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  import   导入书源（JSON 文本/订阅 URL；四分支识别）。\n  list     列出书源库索引（不载规则大字段）。\n  refresh  刷新订阅（省略 url=全部；手动刷新旁路间隔闸门）。\n  subs     订阅 CRUD（书源包地址 + 间隔 + 静默开关）。\n',
    'site': 'Usage: musespiral site [OPTIONS] COMMAND [ARGS]...\n\n  已发布文章 → 零 JS 纯静态站（HTML + index + RSS，内容自主权）。\n\nOptions:\n  --help  Show this message and exit.\n\nCommands:\n  build  扫描已发布正稿 → 纸墨 HTML 站点（草稿不导出，零外联 JS）。\n',
    # G-T5 TTS 朗读（2026-11-19，调研吸收 §六）注册 tts（单命令）——行为新增
    # 非漂移，基线按注册后实测捕获；tts 三字符短于 imagehost，root 清单列宽
    # 不变、既有行零变化，实测核对 added=1 / removed=0。
    'tts': 'Usage: musespiral tts [OPTIONS] ARTICLE\n\n  把文章正文朗读成 MP3（<stem>.mp3 落文章目录，零 API key）。\n\nOptions:\n  --voice [zh-CN-YunxiNeural|zh-CN-YunjianNeural|zh-CN-YunyangNeural|zh-CN-XiaoxiaoNeural|zh-CN-XiaoyiNeural|zh-TW-HsiaoChenNeural|zh-HK-HiuMaanNeural]\n                                  朗读声音（中文声音白名单，见 musespiral tts --help 下方说明）\n                                  [default: zh-CN-YunxiNeural]\n  --rate TEXT                     语速（如 +10% 加速校对 / -10% 慢速听细节；绝对值 ≤100%）\n                                  [default: +0%]\n  --help                          Show this message and exit.\n\n  声音: zh-CN-YunxiNeural（云希 · 男声自然） / zh-CN-YunjianNeural（云健 · 男声解说） / zh-CN-\n  YunyangNeural（云扬 · 男声新闻） / zh-CN-XiaoxiaoNeural（晓晓 · 女声温柔） / zh-CN-\n  XiaoyiNeural（晓伊 · 女声活泼） / zh-TW-HsiaoChenNeural（曉臻 · 女声台湾） / zh-HK-\n  HiuMaanNeural（曉曼 · 女声粤语）\n',
    # P1-A 质量循环（2026-11-23，契约 §3.26）注册 quality-loop（单命令）——行为新增
    # 非漂移，基线按注册后实测捕获；quality-loop 十二字符长于 topic-radar（11），
    # root 清单列宽 11→12、既有行仅 padding 变化（imagehost/topic-radar 判例同款，
    # 命令名与描述文字零变化），新增条目一条。
    'quality-loop': 'Usage: musespiral quality-loop [OPTIONS] ARTICLE\n\n  质量循环：检查 → AI 修复 → 再检查。\n\n  修复稿写 <stem>.quality_fix.md 预览文件（绝不覆盖原文）；人工确认后 手动替换原文。AI 未配置且有 FAIL 项时明确报「AI\n  不可用」（不静默假成功）。\n\nOptions:\n  --max-rounds INTEGER RANGE  最大轮数（全过或达到即停）  [default: 3; 1<=x<=10]\n  --no-fix                    只跑检查不 AI 修复（单轮报告）\n  --help                      Show this message and exit.\n',
}

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_cli_help_snapshot(name: str) -> None:
    """--help 输出与拆分前基线逐行一致。"""
    runner = CliRunner()
    args = ["--help"] if name == "<root>" else [name, "--help"]
    result = runner.invoke(cli, args, prog_name="musespiral")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == EXPECTED[name].splitlines(), (
        f"{name} --help 输出偏离基线"
    )
