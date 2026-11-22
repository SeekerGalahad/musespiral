# 安全策略

> musespiral 的安全模型一句话：**数据不出本机，出网只有一个咽喉**。
> 本文件说明漏洞怎么报、什么在范围内、以及你该如何安全地使用它。

---

## 支持的版本

| 版本 | 是否接收安全修复 |
|---|---|
| **0.5.x**（当前 Alpha） | ✅ 是 |
| 0.4.x 及更早 | ❌ 否（升级到 0.5.x） |

项目处于 Alpha 阶段，只维护最新 minor 线。安全补丁随正常版本发布。

---

## 如何报告漏洞

**请用 GitHub Security Advisories（私有漏洞报告），不要开公开 Issue。**

入口：仓库页 → **Security** → **Report a vulnerability**。

报告时请包含：

1. 受影响的版本（commit SHA 更佳）
2. 复现步骤（命令 + 输入）
3. 影响评估（你认为攻击者能做什么）
4. 如果涉及插件：附 `manifest.json` 全文

**不要做的事**：

- 不要在公开 Issue / PR / 讨论区贴漏洞细节
- 不要贴真实密钥、公众号凭证、个人数据——报告里也要脱敏
- 不要在未协调的情况下公开披露（我们默认 90 天后你可以自由披露，提前披露请先商量）

---

## 响应承诺

| 阶段 | 时限 |
|---|---|
| **确认收到** | 48 小时内 |
| **完成评估**（是否算漏洞 / 严重级别 / 修复计划） | 7 天内 |
| **修复发布** | 按严重级别：高危尽快，中低危随下一版本 |

这是个人维护的项目，时限按人力现实承诺——确认和评估是硬承诺，修复时长不设死线但会持续同步进展。

---

## 什么算安全问题

**算**（都在报告范围内）：

- **路径穿越**：Studio API 端点（`musespiral/studio/`）接受用户输入拼路径的地方逃出预期目录。现有防线如 `_RUN_ID_RE` 白名单（`musespiral/studio/plugin_actions/run_read.py`）失守即属此类
- **SSRF**：绕过 `musespiral/infra/fetcher.py` 的出网五段闸门（URL 形状 / egress IP 复检 / robots / 重定向逐跳重验 / 限额）访问私网、回环或本机其他服务
- **插件沙箱逃逸**：未声明 `exec:subprocess` 权限的插件启动了子进程；未声明 `net:http` 的插件产生了外联
- **本机服务被远程触达**：Studio 绑定突破 `127.0.0.1`（当前强制于 `musespiral/studio/server.py`，`ThreadingHTTPServer(("127.0.0.1", port), …)`）
- **前端注入**：素材内容（标题/正文/标签）未经转义进入 DOM 或 HTML 产物，形成存储型 XSS
- **凭证泄漏路径**：任何会把 `.env` / 配置文件里的密钥输出到日志、产物或 API 响应的路径
- **供应链**：依赖被引入 AGPL/GPL 许可（这是合规红线，也按安全问题处理——`tests/test_license_compliance.py` 是机器门）

**不算**（请走正常 Issue）：

- AI 产出质量差（切片不准、成稿不理想——这本来就是 draft 态要人工裁决的原因）
- 扫描版 PDF 无法提取（已知限制，OCR 在路线图上）
- 缺少可选依赖时的报错体验（`faster-whisper` / `pdfplumber` 等）
- 本机用户自己泄漏自己的数据（同权限本机进程能读你的素材库，这是本地优先设计的边界，不是漏洞）
- 功能请求、风格偏好

---

## 安全设计基线（贡献者必读）

改代码前先知道现有防线在哪，别顺手拆了：

1. **凭证只进 `.env`**——密钥 / Token 只放 gitignore 的 env 文件或环境变量，严禁写进被跟踪的代码与文档
2. **Studio 只绑 127.0.0.1**——`services` 与 `studio` 层零直接网络，出网必经 `infra/fetcher`（出网只有一个咽喉，便于审计与 SSRF 防护）
3. **fetcher 五段闸门不可绕过**——新增出网必须走 `url_fetch`，任何旁路（裸 `urllib.request` / `requests`）在 `tests/test_architecture.py` 分层门和评审里都会被拦
4. **插件权限显式声明**——`fs:read` / `fs:write:tmp` / `fs:write:output` / `net:none` / `net:http` / `exec:subprocess`（权限表见 `musespiral/core/plugins/manifest.py` 的 `PERMISSIONS`）；`exec` 插件缺 `exec:subprocess` 声明直接拒绝执行（`musespiral/services/pipeline/exec_runtime.py`）
5. **私有目录硬排除**——`local-scan` 插件对 `Library/Containers`、`Caches`、`node_modules`、`.git` 等绝对分量匹配排除，root 指向 `~/Library` 也绕不过；改扫描逻辑别动这条
6. **不受信插件不要装**——插件是代码，装了就会以你的用户权限运行。Python runtime 没有进程级沙箱；`exec` runtime 有权限声明门 + 进程组隔离（`shell=False` + 超时 killpg），但没有文件系统隔离。**只装你读过 manifest 和源码的插件**

---

## 机器门清单

安全相关的自动防线（改到这些区域，对应测试必须绿）：

| 门 | 守什么 |
|---|---|
| `tests/test_architecture.py` | 分层违规（core 不碰网络/子进程；出网唯一咽喉） |
| `tests/test_license_compliance.py` | AGPL/GPL 依赖混入 |
| `tests/test_fetcher.py` | 出网五段闸门（SSRF 防线） |
| `tests/test_plugins_contract.py` | 契约与权限声明（含 `_RUN_ID_RE` 级路径白名单语义） |

> 提示：`pytest -q tests/test_architecture.py tests/test_license_compliance.py tests/test_fetcher.py tests/test_plugins_contract.py` 可以一次跑齐。Studio 端点级的安全断言分散在各 `test_studio_*.py` 域测试里，无单列文件——本表滞后请提 Issue。
