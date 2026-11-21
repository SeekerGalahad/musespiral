# 参与贡献

感谢你的兴趣。这个项目的核心价值是「**AI 给建议，人做决定**」——贡献时请守住这条。

---

## 两条不可协商的规则

### ① 不要引入 AGPL/GPL 依赖

`tests/test_license_compliance.py` 会拦住你。原因：

AGPL 第 13 条要求「任何以网络提供服务的衍生作品必须开源全部源码」——直接堵死项目的商业可持续路径（SaaS 化 / 闭源商业版）。**LGPL 是允许的**（动态链接不传染）。

如果你的功能确实需要某个 copyleft 库，优先考虑：
1. 换一个宽松许可的替代实现
2. 自己实现（参考 `doc-extract` 的 EPUB 分支：用 `zipfile` + `ElementTree` 30 行替代了 AGPL 的 ebooklib）

**不要用 `ALLOWLIST` 绕过**——那只是把风险藏起来。

### ② 加了 AI 能力就要加裁决

任何 AI 产出都必须带状态，默认 `draft`：

```python
{
  "ranges": [
    {"start": 0.0, "end": 4.7, "label": "…", "status": "draft"}  # ← 必须有
  ]
}
```

并提供三件套动作：**采纳** / **重新生成** / **弃用**。

**为什么**：AI 判断「什么重要」这件事，是创作者最不愿被替代的领域。工具可以建议，不能替人决定。

---

## 开发环境

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest -q                    # 应全绿（2875 passed）
```

可选能力：

```bash
pip install faster-whisper          # 音视频转写
pip install pdfplumber python-docx  # 文档提取
pip install -e ".[browser]"         # Playwright（视觉截图）
```

---

## 代码结构

```
musespiral/
├── commands/      CLI 入口（click）
├── studio/        本地 Web 工作台（stdlib HTTP + 零依赖前端）
├── services/      编排层（跨模块逻辑）
├── core/          纯逻辑（零 I/O，可单测）
├── infra/         外部交互（AI 网关 / 抓取 / 媒体）
└── jobs/          后台任务
```

**分层纪律（有测试强制）**：
- `core/` 不得 import `infra/` 或 `services/`
- `services/` 与 `studio/` 零直接网络——必经 `infra/fetcher`
- 出网只有一个咽喉，便于审计与 SSRF 防护

---

## 提交前检查清单

```bash
pytest -q                              # 全绿
python scripts/build_ui.py             # 前端产物同步（改 ui.js/ui.css 后必跑）
pytest tests/test_build_ui.py -q       # 体积锚
pytest tests/test_license_compliance.py -q  # 许可证
pytest tests/test_architecture.py -q   # 分层与契约锁
```

---

## 提交信息格式

```
feat(musespiral): 简短描述

为什么这样改（不是改了什么——diff 已经说明了）
判例：如果踩了坑，写清楚坑是什么、怎么发现的
```

示例：

```
fix(musespiral): transcribe 模型探测覆盖实际下载仓库

真素材端到端测试发现的真 bug：用户按插件指引执行
WhisperModel('large-v3-turbo') 预下载成功（1.5GB 落盘），但插件仍报
「模型未在本地就位」——因为探测只查 Systran/faster-whisper-*，而
faster-whisper 实际下载的是 mobiuslabsgmbh/faster-whisper-large-v3-turbo。
```

---

## 写插件的推荐路径

1. 读 `musespiral/plugins_builtin/doc-extract/`（最简单完整的例子）
2. 抄 `manifest.json` 结构，改 `id` / `kind` / `inputs` / `outputs`
3. `plugin.py` 实现 `run(inputs, config, ctx) -> dict`
4. 放 `~/.musespiral/plugins/<your-id>/`
5. `musespiral plugins list` 应立即看到它

**注意**：
- 可选依赖用 `_import_xxx()` 延迟导入 + 可读错误（不要让整包 import 失败）
- 失败要可读（`PluginError("时间轴格式不符合 SRT 规范")` 而不是 `ValueError`）
- 有耗时操作要查 `ctx.cancelled`

---

## 测试风格

**机器门优先于人工审查**。这个项目踩过的坑都是靠测试兜住的：

| 门 | 守什么 |
|---|---|
| `test_license_compliance.py` | 许可证污染 |
| `test_architecture.py` | 分层违规 + 契约漂移 |
| `test_build_ui.py` | 体积失控 |
| `test_assertion_strength.py` | 弱断言（假门） |

**写测试时问自己**：「把实现改错，这个断言会红吗？」如果不会，就是假门。

---

## 报告问题

请包含：
1. 复现步骤（命令 + 输入）
2. 期望 vs 实际
3. 如果是 AI 产物问题，附上 `draft`/`adopted` 状态

**不要贴真实密钥、公众号凭证、个人隐私数据。** 提交前请脱敏。
