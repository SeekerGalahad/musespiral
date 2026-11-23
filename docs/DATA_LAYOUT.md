# 数据面布局（DATA_LAYOUT）——`~/.musespiral/` 与文章库语义表

> 本表回答三个问题：**这是什么 / 能不能删 / 坏了怎么恢复**。2026-11-23 建制（W1-B2）。
> 迁移档案：旧家 `~/.mdpub/` 只读保留（改名迁移源），内容与新家等价或更旧，**勿删亦勿在新版依赖它**。

## 一、`~/.musespiral/`（配置与状态家）

| 路径 | 是什么 | 能删？ | 恢复/重建路径 |
|---|---|---|---|
| `credentials.json` | 公众号 AppID/Secret（发布链凭证） | ❌ | 删了需重新配置（`musespiral config`） |
| `token.json` | 公众号 access_token 缓存（有有效期） | ✅ 可删 | 下次发布自动重取 |
| `ai.json` | AI 档案制配置（default/profiles/routing，key 只进不出） | ❌ | 按模块 README 重配 |
| `imagehost.json` | 图床配置（github/local） | ❌ | 同上 |
| `materials/` | **素材库**：materials.jsonl 索引（1194 条）+ 变体文件（thumb/full） | ❌ | 周 backup 覆盖；无备份时不可重建 |
| `booksources/` | 书源库：2583 源规则包 + index.json 投影 + 订阅表 | ⚠ 可再导入 | 重新 import 订阅源即可（aoaostar 等） |
| `agent_runs/` | Phase D 智能体运行存证（run.json/采纳记录） | ⚠ 存证建议留 | 删了丢失血统链历史 |
| `jobs.jsonl` | 后台任务队列台账（Studio 动作记录） | ✅ 归档可删 | 历史用途，删不影响功能 |
| `cache/` | 提炼/语义搜索缓存（可再生的加速层） | ✅ | 删后自动重建（首次变慢） |
| `discovered.json` | 选题引擎候选台账 | ✅ | 重新 discover scan 生成 |
| `topic_radar/` | 选题雷达快照史（≤20 份） | ✅ | topic-radar --refresh 重算 |
| `kb_root.txt` | 知识库目录指针（选题引擎数据源） | ❌ | 指向 docs/ai-knowledge-base |
| `visual_presets.d/` | 视觉设计预设（用户资产） | ⚠ | 无自动备份，随周 backup 覆盖 |
| `ai_usage.jsonl` | AI 成本台账（累计） | ⚠ 统计面 | 删了成本历史归零 |
| `publish_log.jsonl` | 发布日志（周历/stats 数据源） | ❌ | 不可重建 |
| `runtime/` | **生产运行时 venv**（PyPI 安装，服务吃的版本） | ⚠ 重装即得 | `uv venv runtime && uv pip install musespiral` |
| `release-venv/` | 发版工具隔离 venv（twine 专用，防污染） | ✅ | release.sh 自动重建 |
| `logs/` | 服务日志（studio/topic-radar/backup，周轮转保 3） | ✅ 轮转托管 | — |
| `local/` | 私用控制台与手册（ctl.sh/weekly-ops.sh/手册） | ❌ | 手工建制，无远端副本 |

## 二、`~/mdpub-articles/`（文章工作库）

| 形态 | 是什么 | 能删？ |
|---|---|---|
| `<文章>.md` | 正稿（frontmatter 含 title/cover/publish_at/ai_model 归因） | ❌ 核心 |
| `<文章>.bak.*` | **版本池**（F43：restore 回滚依据，只增不减） | ❌ |
| `<文章>.quality_fix.md` | 质量循环修复预览稿（人工确认前不落正稿） | ✅ 确认后可删 |
| `<stem>.cards/` + `cover*.png` | 卡组与封面（pack 物料） | ⚠ 可再生成 |
| `manual_pack/` | 发布物料包（README/payload/checks/cover 四件套） | ⚠ 可再 pack |
| `*.compose/` | 创作会话自包含（session.json/rounds//draft.md——写作统计与轮次史的数据源） | ❌ |

## 三、备份与恢复

- **周备份**（launchd 周日 06:30）：`~/musespiral-backups/musespiral-backup-<ts>.tar.gz`（文章库+配置，F35 轮转）；恢复=`tar -xzf` 后核对 credentials 与素材索引。
- **单篇误改**：`musespiral restore <文章>`（版本池回滚，恢复前自动再备份）。
- **迁移判例**（2026-11-23）：改名/换机只搬凭证不搬状态=素材/书源/存证全丢面——迁移时按本表 §1 逐行核对，双方都有的以新家为准、旧家独有必补迁。
