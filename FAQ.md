# musespiral FAQ

> 收录的是六天开发里**真实踩过的坑**（不是想象的）。每条给出症状 → 原因 → 可复制的排查/修复命令。
> 安装见 [INSTALL.md](INSTALL.md)，可运行脚本见 [examples/](examples/)。

---

### Q1：转写没有输出 / 转写出来是空段落？

**症状**：`transcribe` 节点跑完，segments 为空或全是低质量段，成稿没内容。

**原因**：最常见的是**视频根本没有音轨**，或音轨是纯静音。转写链路先用 ffmpeg 抽 16kHz 单声道音轨（`musespiral/plugins_builtin/transcribe/plugin.py` manifest 的 `requires: ["ffmpeg"]`），VAD 默认开启过滤静音（`config_schema.vad_filter=true`）；VAD 过滤后零段是**真实结果**，插件不会造假失败（源码 `plugin.py:219-222` 明确「VAD 过滤后零段是真实结果（纯音乐/静音素材）——不制造假失败」）。六天开发里的判例是：某素材均值音量 **-91dB = 纯静音**，自然没有可转写内容。

**排查**：

```bash
# 1) 看视频是否真的有音轨——重点看有没有 "Audio:" 流
ffmpeg -i lecture.mp4 2>&1 | grep -iE 'stream|audio|duration'

# 2) 测音量：volumedetect 的 mean_volume
ffmpeg -i lecture.mp4 -af volumedetect -f null - 2>&1 | grep -iE 'mean_volume|max_volume'
#   mean_volume: -91.0 dB  → 纯静音，不是工具坏了，换有声音的素材
```

**判定信号**：转写每段带 `avg_logprob` / `no_speech_prob`（行业口径：`avg_logprob < -1` 低置信、`no_speech_prob > 0.6` 疑似非语音，`musespiral/plugins_builtin/transcribe/plugin.py:6`、key-segment `plugin.py:76-79`）。若这些值整体很差，就是素材问题不是代码问题。

**修复**：确认素材有真实人声；若确实有无声音轨需求，用 `subtitle` 插件走字幕直出（见 Q3 / `examples/subtitle-fast.sh`）。

---

### Q2：markitdown 说未安装？

**症状**：文档提取日志出现 `doc-extract: markitdown 未安装（可选优先后端）——退回自研提取器`。

**原因**：markitdown 是 doc-extract 的**可选优先后端**，不在主包默认依赖里（`musespiral/plugins_builtin/doc-extract/plugin.py:392-395`）。没装时插件自动退回自研四路提取器（pdfplumber / python-docx / 纯标准库 EPUB / 文本），不会报错——日志只是提示你能解锁更多格式。六天评估里也指出 markitdown/mammoth 一度不在 extras 里，现已收编进 `[docs]` 组（`pyproject.toml:38`，见 `docs/项目方案/musespiral-五角色全面评估与改进路线-2026-11-21.md` 依赖管理条）。

**修复**（按需要选一种）：

```bash
pip install markitdown mammoth            # 单独装
pip install -e ".[docs]"                  # 或装 docs extras（含 markitdown + mammoth）
```

装完 markitdown 可解锁 PPTX/XLSX/图片/音频等更多格式（`doc-extract/plugin.py:384-386`）；mammoth 用于 DOCX。PDF/DOCX 自研兜底另需 `pip install pdfplumber python-docx`（`doc-extract/plugin.py:64-65`，import 名分别是 `pdfplumber` / `docx`）。

> 注意：doc-extract 的 markdown/text 输出走 markitdown 或自研后端；缺依赖时 `/check` 报 `MISSING_DEP`、运行给可读安装指引，不会让整包 import 失败（`doc-extract/plugin.py:60-102`）。

---

### Q3：语义搜索不工作 / 素材搜不出语义结果？

**症状**：`mode=semantic` 搜索没结果，或前端亮「降级」灯、`semantic_ready` 为 false。

**原因**：语义搜索依赖本地 Ollama + bge-m3 模型（`musespiral/infra/embedding.py:45-47`）。缺位时是**环境态不是请求错误**，程序自动降级为关键词搜索（`musespiral/studio/payload.py:418-421`）。常见两种：Ollama 没在跑，或没拉 bge-m3 模型。

**排查**：

```bash
# 1) Ollama 是否在跑、模型名单里有没有 bge-m3
curl http://127.0.0.1:11434/api/tags
#   HTTP 200 且 models 含 "bge-m3:latest" 才对

# 2) 用程序自己的探测（更准，就是实际用的门）
python -c "from musespiral.infra import embedding; print(embedding.embed_available())"
#   True = 就绪；False = Ollama 没跑/没拉模型
```

**修复**：

```bash
brew install ollama          # macOS；Linux 见 INSTALL §4
ollama serve                 # 起服务（桌面版通常已后台运行）
ollama pull bge-m3           # 拉模型
```

详见 [INSTALL.md §4](INSTALL.md#4-配置-ollama--bge-m3语义搜索可选)。若 Ollama 在非默认地址，设 `MUSESPIRAL_OLLAMA_HOST` / `MUSESPIRAL_EMBED_MODEL`（`embedding.py:55-60`）。

> 六天评估里语义检索是 P0 待办（AI-6，见 `docs/项目方案/musespiral-五角色全面评估与改进路线-2026-11-21.md`），设计正典 §ARCH-5（本地 bge-m3 + 向量索引）。

---

### Q4：微信发布报 IP 白名单错误？

**症状**：`publish`/`submit` 调公众号 API 返回 errcode **40164**（`invalid ip ... not in whitelist`）。

**原因**：微信公众号 API 要求调用方出口 IP 必须在公众号后台「IP 白名单」里。本机出口 IP 变了（换网络/VPN/池化出口）就不在名单内。

**修复**：

1. 查当前出口 IP（浏览器搜「我的 IP」或 `curl ifconfig.me`）。
2. 公众号后台 →「开发管理」→「IP 白名单」→ 把该 IP 加进去。
3. 重试发布。

六天开发里有个真实判例：**PolessVPN TUN 国内直连出口是池化逐连接轮换**（实测 117.28.97.22 / 59.61.71.190 交替）——白名单只卡 token 端点，token 到手后整条链路不再查 IP，所以 **40164 时直接重试即可命中已加白的 IP**（publish 重试首把即中）。长效解是把 `api.weixin.qq.com` 加入 VPN 代理规则让出口恒为已加白的固定 IP（判例见 `CHANGELOG.md` 2026-11-21「出口 IP 池判例」条）。

---

### Q5：微信发布报 author 超限？

**症状**：发布时微信 API 报 author 字段超长。

**原因**：微信公众号 API 对 author 按 **UTF-8 字节**限制（权威口径 ≤8 字节）——中文一字 3 字节，所以 author 写 3 个汉字就是 9 字节，直接超限。这与标题（≤64 字节）、摘要（≤120 字节）同为字节口径（`musespiral/config.py:216-217`、`musespiral/platforms/profiles.py:5,11`）。平台校验里标题/摘要已按 `len(x.encode("utf-8"))` 做检查（`musespiral/platforms/wechat.py:112-134`），author 走 `create_draft` 原样透传（`wechat.py:87`），因此 author 超限会在微信服务端暴露。

**修复**：把 frontmatter 里的 `author` 压到 ≤8 字节——
- 用纯 ASCII 署名（如 `wagic` = 5 字节）；
- 或中文最多 2 个字（6 字节）；
- 或留空（不传 author）。

```bash
# 自查字节数
python -c "print(len('你的署名'.encode('utf-8')))"   # > 8 就会超限
```

---

### Q6：AGPL/GPL 依赖被拦截？

**症状**：`pytest` 里 `test_license_compliance.py` 红灯，或装的某个包被许可门判 forbidden。

**原因**：**这是设计意图，不是 bug**。musespiral 走 MIT 商业开源路线，依赖树禁止 AGPL/GPL（LGPL 允许）。六天里的真实判例：`ebooklib` 是 AGPL-3.0-or-later，其第 13 条网络条款要求网络服务开源全部源码，堵死 SaaS/闭源商业版/私有部署销售；已改成纯标准库解析 EPUB 移除它（`tests/test_license_compliance.py:1-21`、对应 `CHANGELOG.md` 2026-11-21 条）。

**修复**：
- 换宽松许可（MIT / Apache-2.0 / BSD / ISC / MPL-2.0 / LGPL）的替代包；
- **不要**为放行真正的 copyleft 依赖而往 ALLOWLIST 加名字——它只用于「已确认合规但元数据缺失」的包（`test_license_compliance.py:20-21,43`）。

自查：
```bash
pytest tests/test_license_compliance.py -q
python -m pip show ebooklib            # 不该出现在依赖树里
```

---

### Q7：体积门爆红？

**症状**：前端产物体积测试报警/失败。

**原因**：体积门是**三档制**，首屏和总量口径不同，别混为一谈（`tests/test_build_ui.py`）：
- **首屏 = 硬门**：`FIRST_SCREEN_MAX_BYTES = 240 * 1024`（约 240KiB / 245,760B，`test_build_ui.py:203`）——超了硬失败。
- **单产物上限**：core ≤160KiB / 单 view ≤40KiB 等分层锚。
- **总量 = 软警报三档**：绿 ≤530,000B / 黄 530,000–570,000B（软警报，不阻塞，但须下批归因）/ 红 >600,000B（硬失败，`test_build_ui.py:164-166`）。

> 注意：提问里说的「首屏 ≤150KB 硬门」与当前代码不符——实际硬门是 **240KiB**（`test_build_ui.py:203`），仓库历史里也搜不到 150KB 常量。以代码为准。三档化是 2026-11-21 的机制修正：懒加载视图不进首屏，总量门拦的是仓库膨胀而非用户体验（`test_build_ui.py:239-243,519-543`）。

**修复**：
- 首屏爆红：把增量挪进懒加载视图（`assets/views/*.js`），不要塞进 `core.js` / 壳；历史多次就是这么把首屏从 ~286KiB 降到 ~137KiB 的（`test_build_ui.py:127` 沿革）。
- 总量进黄档：在下一批交付报告里归因（哪个视图涨了多少）；进红档：必须拆分或删功能。

自查：
```bash
pytest tests/test_build_ui.py -q
```

---

### Q8：如何写自己的插件？

**最短路径**：放一个目录到 `~/.musespiral/plugins/<id>/`，含 `manifest.json` + `plugin.py`。`manifest 即 UI`——插件详情页自动从清单渲染，不用写一行 HTML（`README.md:152`）。

**照抄现成的**：`musespiral/plugins_builtin/doc-extract/` 是最完整的参考（manifest.json 定义 4 输入/3 输出 + config_schema，plugin.py 实现多后端 + 可读缺依赖提示）。9 个内置插件都在 `musespiral/plugins_builtin/`（README「内置 9 个插件」表）。

**manifest 长这样**（README 插件系统节 `README.md:122-131`）：
```json
{
  "id": "my-plugin",
  "kind": "process",
  "inputs":  { "text": {"type": "text/markdown", "required": true} },
  "outputs": { "result": {"type": "text/markdown"} },
  "runtime": { "type": "python", "entry": "plugin.py:run" },
  "permissions": ["fs:read"]
}
```

契约纪律（README「三个核心差异」之②、`README.md:120-136`）：插件间只通过 **10 种类型化契约**通信，不共享内存、不互相 import；保存流水线时会静态校验相邻节点类型接不接得上。

**关于开发指南文档**：ask 指向的 `docs/plugins.md`（DOC-3 产出）**目前尚不存在**——仓库里没有 `docs/` 目录，`docs/plugins.md` 只在商业化计划里列为 P2 待办（`docs/项目方案/musespiral-商业化文档与专业评审计划-2026-11-22.md:40,72`）。在它产出前，以 `plugins_builtin/doc-extract/` 为模板、以各内置 plugin.py + manifest.json 为准。

自查插件是否被加载：
```bash
musespiral studio --root tests/fixtures/studio_demo
# Studio 界面「插件清单」视图，或 GET /api/plugins
```
