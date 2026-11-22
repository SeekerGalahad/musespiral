# 架构

> 本文写给要改核心代码的贡献者。五个部分：分层、插件系统、AI 裁决、语义搜索、前端，最后是端到端数据流。
> 所有分层规则不是文档口号——`tests/test_architecture.py` 用 AST 扫描强制执行，违例直接红。

---

## 一、四层分层

```
表示层    commands/ (CLI，click)   ·   studio/ (本地 Web 工作台)   ·   preview.py
   ↓ 只准向下
服务层    services/  (编排：pipeline / compose / materials / refine / search …)
   ↓
领域层    core/  (纯逻辑：plugins / materials / composition / version / tts …)
          platforms/
   ↓
基础设施  infra/  (AI 网关 / fetcher / media_gen / plugins_loader / storage …)
          jobs/  ·  browser/
```

三条硬规则（正典 §2.2，机器门=`tests/test_architecture.py`，AST 扫描全部 import，绝对+相对导入解析为绝对包名）：

1. **只准向下**：Presentation → Service → Domain → Infrastructure，禁止反向或跨层跳跃
2. **领域层零 I/O**：`core/` 与 `platforms/` 不得 import `requests` / `urllib.request` / `sqlite3` / `subprocess`
3. **infra 是最低层**：不得 import 任何上层模块——所以 manifest 的 entry 存在性核验（I/O 半边）放在 `infra/plugins_loader.py`，契约格式半边（纯校验）留在 `core/plugins/manifest.py`

**出网只有一个咽喉**：`services/` 与 `studio/` 零直接网络，一切 HTTP GET 必经 `infra/fetcher.py`（五段闸门：URL 形状 / egress IP 复检 / robots / 重定向逐跳重验 / 限额）。这条纪律让 SSRF 防护和出网审计各只有一个地方可看。

目录树（实拍，`ls musespiral/`）：

```
musespiral/
├── commands/      # CLI 入口，31 个 *_cmds.py 按域拆分
├── studio/        # stdlib HTTP server + 零依赖前端（server.py / actions / plugin_actions / assets）
├── services/      # 编排层，33 个 *_service.py + pipeline/ 等子包
├── core/          # 纯逻辑，14 个子包 + article.py 等纯逻辑模块
├── infra/         # 外部交互（ai/ 网关、fetcher、embedding、wechat、storage）
├── platforms/     # 发布平台适配
├── jobs/          # 后台任务
└── plugins_builtin/  # 9 个内置插件（目录即插件）
```

---

## 二、插件系统：契约优先于实现

插件之间**只通过类型化契约通信，不共享内存、不互相 import**。这一条纪律同时换来可替换（faster-whisper → FunASR 只换一个插件）、可静态校验（保存流水线时检查相邻端口类型）、可跨语言（exec runtime）。

### 2.1 契约类型（恰好 10 个）

`core/plugins/types.py` 的 `CONTRACT_TYPES`：

```
file/path, url/http, media/av, segments, ranges,
images, clips, text/markdown, article, material
```

**兼容判定 = 完全相等**，全系统唯一权威入口 `types_compatible()`，禁止散落 `==`。扩展类型走 `ext/<name>` 命名空间（插件自带 schema），只与自身兼容——它不被任何内置类型吸收，这条红线也收在同一函数里。标量基元（`string` / `number` / `string[]` …）是运行级辅助端口，不进契约 10 型。

### 2.2 manifest 即 UI

`manifest.json` 是插件唯一自描述面，pydantic 结构校验 + `extra="forbid"`（拼错键宁可在安装期红，不在运行时哑）：

- **必需六键**：`id` / `version` / `kind` / `inputs` / `outputs` / `runtime`
- **可选六键**：`name` / `description` / `requires` / `permissions` / `config_schema` / `uses_ai`
- **kind 四枚举**：`source` / `process` / `sink` / `control`（control ⇒ outputs 必含 `route`，引擎据此选分支）

权限词表（`PERMISSIONS`，安装时审批）：`fs:read`、`fs:write:tmp`、`fs:write:output`、`net:none`、`net:http`、`exec:subprocess`。

解析红绿双出口 `(Plugin | None, errors)`——单个坏 manifest 隔离，绝不炸掉整批加载。

### 2.3 三种执行路径（两种 runtime + 一条原生控制流）

| 路径 | 载体 | 说明 |
|---|---|---|
| `python` runtime | 进程内 | entry 须 `<file>.py:<func>` 形态，`run(inputs, config, ctx) -> dict` |
| `exec` runtime | 子进程 | JSON 走 stdin/stdout；`shell=False`（shlex 拆词，无命令注入）；**缺 `exec:subprocess` 权限声明直接拒绝执行**；超时 `killpg` 杀整个进程组，孙进程不留 |
| 控制流节点 | 引擎原生 | `switch`（按 control 插件的 `route` 输出选分支，其余分支零执行）与 `loop`（`$item` 接线逐项，`max_iters` 默认 100 防死循环）——不经插件，无 manifest 形态 |

引擎在 `services/pipeline/`：`engine.py`（拓扑执行+收集+清理，失败不清理 workspace 保留现场）、`workspace.py`（产物暂存，引用/值分流，`INLINE_MAX_BYTES`=1 MiB，大产物落盘走引用）、`adjudicate.py` / `regenerate.py`（见下节）、`prune.py`（run LRU 回收）。

加载链：`infra/plugins_loader.py` 目录扫描（内置 `plugins_builtin/` + 用户 `~/.musespiral/plugins/`）→ core 格式校验 → infra entry 存在性核验 → `services/plugins_service.py` 逐个隔离装配。写新插件抄 `musespiral/plugins_builtin/doc-extract/`。

---

## 三、AI 裁决机制：draft → adopted / discarded

项目第一原则「AI 给建议，人做决定」的机制化。AI 端口产物（key-segment 的 `ranges`、article-writer 的 `article`）带三态：

```
AI 产出 → [draft 建议] → 人工裁决 → [adopted 正式]
              ↓                        ↓
         [discarded 弃用]          可发布
```

- **默认保守**：状态默认 `draft`，前端琥珀徽章「AI 建议」；`config.auto_adopt: true` 可切全自动（信任 AI 的用户不被强制审查）
- **裁决端点**：`POST /api/pipelines/runs/<id>/adopt|discard {step, indices?}`，非法参数一律 HTTP 400 可读中文，零 5xx
- **重生成**：`POST /api/pipelines/runs/<id>/regenerate {step, feedback?}`——**前序节点零重跑**（转写产物确定），旧产物归档 `outputs.v{N}.json`（md 归档 `{stem}.v{N}.md`），新产物重置 draft；重生成旁路 AI 缓存（否则命中缓存=假重生成）
- **读面**：`GET /api/pipelines/runs/<id>` 返回 `ai_verdicts={node_id: {port: status}}`（`studio/plugin_actions/run_read.py` 的 `_ai_verdicts`）——**只放标量态零正文**；`ranges` 取整组态（全同→该态 / 异态→`"mixed"`），且**编辑版优先**（裁决端点写 `ranges.edited.json`，只读 outputs.json 会把已裁决的 run 读回 draft）；老 run 无 status 字段则该键缺席，前端不渲染徽章（零回归）

素材侧同构：`material-sink` 上游 draft → 素材 `meta.ai_status='draft'`；素材打磨循环（refine → adopt → iteration 版本链）复用 `core/version/diff`，任意两版可 diff、可回退，回退也进链。

**贡献守则**：加 AI 能力必须加裁决——产物默认 draft + 采纳/重生成/弃用三件套（CONTRIBUTING.md 硬规则 ②）。

---

## 四、语义搜索：bge-m3 + 向量索引 + 优雅降级

三层分工互不越界（`services/search_service.py` 模块头即正典）：

```
core/materials/vector.py      纯数学：余弦 / top-k / 归一化（零 I/O）
infra/embedding.py            外部推理：Ollama REST /api/embed + sha256 缓存
services/search_service.py    编排：向量索引落盘 + 三路合并 + 增量钩子
```

- **模型与索引**：Ollama `bge-m3`（默认 `127.0.0.1:11434`，`MUSESPIRAL_OLLAMA_HOST` / `MUSESPIRAL_EMBED_MODEL` 可覆写），dim=1024 float32。embedding 缓存 `~/.musespiral/embed_cache.sqlite`（key=`sha256(model+text)`，换模型天然隔离）；素材向量索引 `~/.musespiral/material_vectors.sqlite`（`text_hash` 使索引幂等，同内容重复 add 零重嵌）。素材 ≤10000 暴力扫描 <100ms——不引入向量数据库
- **三路合并**：`hybrid_search` = 关键词 + 标签 + 语义，RRF 名次融合（k=60）；语义路取宽候选 50、入路阈值余弦 0.35（bge-m3 中文相似度谱系压缩，宁漏勿滥）
- **降级承诺**：Ollama 未运行 / 未拉模型 / 索引空 → hybrid **静默收编关键词+标签两路继续出结果**，绝不抛错；显式要语义时（`semantic_search`）透传可读中文错误（含安装引导 `brew install ollama && ollama pull bge-m3`）；**增量钩子对一切异常零抛出**——索引失败绝不影响素材主写路径（派生索引永远可 rebuild，immutable 素材库红线优先）

---

## 五、前端：壳 + 懒加载 + 三步链

**零外部依赖**：无 CDN、无构建链（构建是纯 Python）、无 npm。产物由 `scripts/build_ui.py` 从源文件纯函数生成（连跑两次字节一致），零外联在构建期机器断言（壳中 `<script src>` / `<link href>` 必须以 `assets/` 开头，http(s) 引用直接拒——防被改成 CDN）。

```
开发态源（musespiral/studio/）        构建产物（入库）
  ui.tmpl.html   HTML 骨架      →   ui.html            壳 + 首屏视图内联 + 其余视图空占位
  ui.css         core 样式      →   assets/core.css    全局 token + 通用组件 + 首屏样式
  ui.js          全部脚本       →   assets/core.js     core 段（路由/状态/工具/命令面板）
  ui.views/<v>.html/.css        →   assets/views/<v>.{html.js,css,js}  按视图拆源
```

**懒加载三步链**（`core.js` 的 `ensureView(view)`，进入视图时按序注入，CSS 先于骨架防闪）：

1. `assets/views/<view>.css` —— `<link>` 注入样式
2. `assets/views/<view>.html.js` —— `innerHTML` 填充壳里的空占位 `<section>` + `__onViewHtml` 补装钩子
3. `assets/views/<view>.js` —— 该视图的 `render*` / `bind<View>()`

首屏视图 `publish` 的 HTML 与样式直接内联进壳，不触发懒加载。11 个视图，体积有锚（`tests/test_build_ui.py`：一级硬门=首屏、二级=单产物上限、三级观测=总量增长率）。脚本全部扁平全局零 import，视图块用 `// @view:<name> … // @endview` 注释圈出，构建器按标记拆分。

---

## 六、端到端数据流

```
外部源 ──→ 素材池 ──→ 创作 ──→ 发布 ──→ 反哺 ─┐
   │          │        │        │              │
   └──────────┴────────┴────────┴──────────────┘
              （已发布内容回流素材库，飞轮闭环）
```

以一条真实流水线为例（8.5 小时讲座视频 → 图文稿）：

```
local-file        source  校验本地媒体（ffprobe 时长探测）
   │ media/av
transcribe        process faster-whisper 转写 → segments（每段带 avg_logprob/no_speech_prob 质量信号）
   │ segments          （有字幕就换 subtitle 插件——同一位置的互换生产者，跳过 ASR）
key-segment       process AI 识别关键段落 → ranges（AI+规则降级双路径；产物= draft 态，见 §三）
   │ ranges
frame-grab        process ffmpeg 抽关键帧 / 裁片段（避转场，窗口感知锚）→ images
   │ images
article-writer    process 组织成带时间戳锚的图文稿 → article（AI+模板双路径，两路径都保时间戳锚）
   │ article
material-sink     sink    落库为素材（preview 态 + provenance.chain 血统链）→ 素材池
```

运行时各节点产物落 `~/.musespiral/pipeline_runs/<run_id>/workspace/<node>/outputs.json`，引擎按 manifest 的 inputs/outputs 端口类型接线（保存时静态校验相邻端口，运行时 `check_input` 再验一次）；超过 `INLINE_MAX_BYTES`（1 MiB）的产物落盘走引用。AI 端口产物全程 draft 态，裁决动作（adopt/discard/regenerate）见 §三。

成稿进素材池后：`compose` 创作工坊组织成稿（article / novel 双模式，小说带前情提要+人物卡）→ `platforms/` 多平台适配发布 → 已发布文章可反哺回素材库（全文归档 / 段落摘取 / AI 提炼论点，`provenance.chain` 记录血统）。任意视图 `⌘⇧I` 灵感速记一句话进素材库。

反向查找：素材侧 `search_service`（§四）三路合并；作品侧版本链 `core/version/`（diff + 回退）。

---

## 测试与机器门

| 门 | 守什么 |
|---|---|
| `tests/test_architecture.py` | 分层规则（AST 扫 import）+ 契约锁 |
| `tests/test_license_compliance.py` | 零 AGPL/GPL 依赖 |
| `tests/test_build_ui.py` | 前端体积锚（三层级）+ 零外联 |
| `tests/test_assertion_strength.py` | 弱断言（假门） |
| `tests/test_plugins_contract.py` | 契约 10×10 矩阵 + 权限表 + 坏 manifest 隔离 |

提交前跑 `pytest -q`（应全绿），改前端后必跑 `python scripts/build_ui.py`。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。
