# musespiral 详细安装指南

> 这份是 README「快速开始」的详细版。README 只给了三行命令；这里逐个平台、每个可选依赖、每类凭证都讲清楚，目标是**复制粘贴就能跑通**。
> 面向版本：v0.5.1（`musespiral/__init__.py:7`，`pyproject.toml:3`）。

---

## 0. 先确认你装的是什么

musespiral 是一个 Markdown 驱动的内容流水线：素材进来，成稿出去。核心（生成/预览/Studio）零外部 AI 依赖；转写、语义搜索、文档提取等能力是**可选**的，按需装。

- 包名 / CLI 命令：`musespiral`（`pyproject.toml:45` → `musespiral.cli:cli`）
- Python 要求：`>=3.10`（`pyproject.toml:7`）
- 状态保存在仓库外：`~/.musespiral/`，可用 `MUSESPIRAL_HOME` 环境变量整体改道（`musespiral/config.py:13-18`）

> ⚠️ 仓库里的 `scripts/setup.sh` 是改名前的旧产物（第 2 行注释仍写 `mdpub`，第 59/65 行自检仍调 `mdpub.__version__` / `bin/mdpub`）。**不要用那个脚本安装**——按本文件的命令走。

---

## 1. 前提条件

| 条件 | 是否必需 | 用途 | 说明 |
|---|---|---|---|
| Python 3.10+ | **必需** | 运行核心 | 3.10 / 3.11 / 3.12 / 3.13 均可；本机 venv 实测 3.13.13 通过 |
| pip | **必需** | 装依赖 | uv 建的 venv 可能没 pip，见 §7「uv venv 无 pip」 |
| ffmpeg / ffprobe | 可选 | 音视频转写、抽帧、时长探测 | `transcribe`/`frame-grab`/`local-file`/`local-scan` 插件需要；只做纯文本/公众号发布不需要。本机实测 ffmpeg 8.1 |
| Ollama + bge-m3 | 可选 | 语义/混合搜索、素材语义上下文 | 缺位时自动降级为关键词搜索，不阻塞核心功能（§4） |
| 微信公众号凭证 | 可选 | 建草稿/发布 | 只在 `publish`/`submit` 时需要（§5） |

核心依赖（`pyproject.toml:8-31`）会自动装：requests / Pillow / Pygments / mistune / python-frontmatter / PyYAML / click / pydantic / feedparser / readability-lxml / edge-tts / beautifulsoup4。

---

## 2. 三平台安装

### 2.1 macOS

```bash
# 1) Python 3.10+（系统自带 python3 可能偏旧，推荐 brew）
brew install python@3.12
python3 --version                       # 确认 >= 3.10

# 2) 可选：音视频工具（转写/抽帧需要）
brew install ffmpeg

# 3) 建虚拟环境并装核心 + 测试依赖
cd <musespiral 目录>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"

# 4) 自检
python -c "import musespiral; print(musespiral.__version__)"   # 0.5.1
musespiral --help
pytest -q
```

### 2.2 Linux（Debian/Ubuntu）

```bash
# 1) Python + venv 支持
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

# 2) 可选：音视频工具
sudo apt install -y ffmpeg

# 3) 建虚拟环境并装核心 + 测试依赖
cd <musespiral 目录>
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"

# 4) 自检
python -c "import musespiral; print(musespiral.__version__)"
musespiral --help
pytest -q
```

> 若 `apt` 的 python3 < 3.10（如老版 Ubuntu 20.04），用 deadsnakes PPA 或 pyenv 装 3.10+ 再建 venv。

### 2.3 Windows（PowerShell）

```powershell
# 1) Python 3.10+：从 python.org 安装，勾选 "Add Python to PATH"
py -3.12 --version                      # 确认 >= 3.10

# 2) 可选：ffmpeg —— 用 winget 或 scoop
winget install Gyan.FFmpeg
#   或 scoop install ffmpeg

# 3) 建虚拟环境并装核心 + 测试依赖
cd <musespiral 目录>
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"

# 4) 自检
python -c "import musespiral; print(musespiral.__version__)"
musespiral --help
pytest -q
```

> 若 PowerShell 禁止运行激活脚本：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。
> 路径含中文/空格时始终给路径加引号；`MUSESPIRAL_HOME` 用 Windows 绝对路径（如 `C:\Users\<你>\.musespiral`）。

---

## 3. 可选依赖矩阵（extras 对应功能）

来源：`pyproject.toml:33-42` 的 `[project.optional-dependencies]`。

| extras | 安装命令 | 包含的包 | 解锁功能 |
|---|---|---|---|
| `test` | `pip install -e ".[test]"` | pytest, pytest-cov | 跑测试（`pytest -q`，README 记 2875 passed） |
| `docs` | `pip install -e ".[docs]"` | markitdown, mammoth | doc-extract 的 markitdown 优先后端（PPTX/XLSX/图片/音频等更多格式；DOCX 走 mammoth） |
| `media` | `pip install -e ".[media]"` | faster-whisper | transcribe 插件的语音识别后端（约 85MB；首次用需下模型 1.6GB） |
| `browser` | `pip install -e ".[browser]"` | playwright>=1.40 | 视觉截图 / `upload` 浏览器上传器；另需 `playwright install chromium` |
| `dev` | `pip install -e ".[dev]"` | pytest, pytest-cov, mypy, black, isort, flake8, pre-commit | 开发全家桶（类型检查/格式化/hooks） |

**一条命令装齐所有可选能力**：

```bash
pip install -e ".[test,docs,media,browser,dev]"
playwright install chromium          # browser 组的浏览器二进制要单独下
```

**不通过 extras、单独按需装**也可（插件缺席依赖时会给可读提示而非崩溃，`musespiral/plugins_builtin/doc-extract/plugin.py:60-102`）：

```bash
pip install pdfplumber python-docx    # doc-extract 的 PDF/DOCX 自研兜底
pip install markitdown mammoth        # doc-extract 的 markitdown 优先后端
pip install faster-whisper            # transcribe
```

> 许可证红线：依赖树禁止 AGPL/GPL（LGPL 允许），有机器门 `tests/test_license_compliance.py` 守护；这也是 doc-extract 用纯标准库解析 EPUB、不引入 ebooklib(AGPL) 的原因（`CHANGELOG.md` 2026-11-21 条）。装包时若被许可门拦下，见 FAQ。

---

## 4. 配置 Ollama + bge-m3（语义搜索，可选）

素材库的语义/混合搜索走本地 Ollama 的 bge-m3 模型（`musespiral/infra/embedding.py:1-47`）。**不装也能用**——缺位时自动降级为关键词搜索，并在响应里带 `semantic_ready=false` 让前端亮降级灯（`musespiral/studio/payload.py:414-461`）。

### 4.1 安装 Ollama

macOS:
```bash
brew install ollama
# 或官网下载：https://ollama.com/download
```

Linux:
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Windows: 从 https://ollama.com/download 下载安装器（Ollama 原生支持 Windows）。

### 4.2 启动服务并拉模型

```bash
ollama serve                           # 起服务（macOS/Windows 桌面版通常已在后台跑）
ollama pull bge-m3                     # 拉嵌入模型（本机实测 bge-m3:latest 约 1104 MiB）
```

### 4.3 验证就位

```bash
# 1) 服务在跑、模型在名单里
curl http://127.0.0.1:11434/api/tags
#   应看到 "name": "bge-m3:latest"（裸名 bge-m3 或带 :latest 都算就位，
#   见 musespiral/infra/embedding.py:91-106）

# 2) 用 musespiral 自己的探测确认（更准——就是程序实际用的那个门）
python -c "from musespiral.infra import embedding; print(embedding.embed_available())"
#   True = 就绪
```

> 本机实测：`curl /api/tags` 返回含 `bge-m3:latest`，`embed_available()` 返回 `True`。

### 4.4 端点与模型名可改（默认无需改）

| 环境变量 | 默认值 | 作用 |
|---|---|---|
| `MUSESPIRAL_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `MUSESPIRAL_EMBED_MODEL` | `bge-m3` | 嵌入模型名 |
| `MUSESPIRAL_HOME` | `~/.musespiral` | 状态目录（含 `embed_cache.sqlite` 向量缓存、`search_index.sqlite` 索引） |

来源：`musespiral/infra/embedding.py:20-21,45-46,55-60`。

例：Ollama 跑在别的机器/端口——
```bash
export MUSESPIRAL_OLLAMA_HOST=http://192.168.1.50:11434
export MUSESPIRAL_EMBED_MODEL=bge-m3
```

### 4.5 关闭语义上下文（不需要时）

AI 加工默认附带 top-3 语义相关素材摘要；关掉它：
```bash
export MUSESPIRAL_SEMANTIC_CONTEXT=false   # 只认 false/0/no（大小写不敏感）
```
来源：`musespiral/config.py:33-45`。

---

## 5. 配置微信公众号凭证（发布功能，可选）

凭证存在 `~/.musespiral/credentials.json`（`musespiral/config.py:27`），由 `config` 命令维护（`musespiral/commands/config_cmds.py:23-39`）。**这个文件在仓库外，别提交、别分享。**

```bash
# 设置默认账号的 appid 与 secret
musespiral config set appid <你的AppID>
musespiral config set secret <你的AppSecret>

# 多账号：用 --account 区分
musespiral config set appid <AppID> --account work
musespiral config set secret <AppSecret> --account work

# 查看已配置账号
musespiral config list
```

生成的 `~/.musespiral/credentials.json` 形如：
```json
{
  "accounts": {
    "default": {"appid": "wx...", "secret": "..."},
    "work":    {"appid": "wx...", "secret": "..."}
  },
  "default": "work"
}
```

发布链路要点：
- `musespiral publish` 只**上传图片并创建草稿**，不自动发表（`musespiral/cli.py:46`、命令 help）。
- `musespiral submit` 才提交发表（freepublish，需二次确认）。
- 公众号 API 有 IP 白名单：调用前需把本机出口 IP 加到公众号后台白名单，否则报 40164（见 FAQ）。
- 平台限制：标题 ≤64 字节、摘要 ≤120 字节（UTF-8 口径，`musespiral/config.py:216-217`）。

---

## 6. 启动与验收

```bash
# Studio 本地工作台（只绑 127.0.0.1，数据不出本机）
musespiral studio --root tests/fixtures/studio_demo
# → http://127.0.0.1:8766
# 参数：--port 改端口 / --no-open 不自动开浏览器（launchd 常驻用）
# 来源：musespiral/commands/studio_cmd.py:14-39

# 跑测试确认安装完整
pytest -q

# 跑一个可运行示例（见 examples/）
bash examples/doc-extract.sh --help
```

---

## 7. 常见安装问题

**Q：编译失败 / `pip install` 报 wheel 构建错误。**
A：先升级构建工具链，再用国内镜像重试（Linux 上 `Pillow`/`readability-lxml` 等缺编译头时会走源码构建）：
```bash
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[test]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```
`pyproject.toml:47-49` 要求 `setuptools>=68.0`——老 setuptools 会直接失败。

**Q：权限错误 / `Permission denied`。**
A：务必在虚拟环境里装（不要 `sudo pip`）。macOS/Linux 用 `source .venv/bin/activate`，Windows 用 `.\.venv\Scripts\Activate.ps1`。确认 `which python` / `where python` 指向 `.venv` 内的解释器。状态目录 `~/.musespiral/` 需可写——若在受限环境，用 `MUSESPIRAL_HOME` 指到你有权限的目录。

**Q：路径问题 / 找不到文件。**
A：
- Python 3.10+，`python3 -m venv` 成功再激活。
- Windows 路径含空格/中文时加引号；用绝对路径传 `--root`、`--input` 等。
- `ffprobe`/`ffmpeg` 找不到时，用 `which ffmpeg` 确认，或装好后重开终端刷新 PATH。
- `ModuleNotFoundError: click` 之类说明没激活 venv 或没装依赖——重跑 `python -m pip install -e ".[test]"`。

**Q：`uv` 建的 venv 没有 pip。**
A：`uv venv` 默认不带 pip。`python -m ensurepip --upgrade` 引导；还不行就删掉重建：`rm -rf .venv && python3 -m venv .venv`（本机 `.venv` 就是 3.13.13 的常规 venv，`pip` 可用）。

**Q：拉模型 / 装依赖时网络超时（境内环境）。**
A：pip 用清华镜像（见上）；Ollama 拉模型若慢，配置代理或换网络重试。ffmpeg 用系统包管理器装最稳。

**Q：`playwright install chromium` 下载失败。**
A：设镜像再重试：
```bash
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ playwright install chromium
```

**Q：许可门拦截（AGPL/GPL 依赖）。**
A：这是设计意图，见 FAQ 与 `tests/test_license_compliance.py`——换宽松许可的替代包，不要往 ALLOWLIST 里塞真正的 copyleft 依赖。

---

## 8. 下一步

- 跑示例：`examples/` 下四个可运行脚本（video-to-article / subtitle-fast / doc-extract / local-scan）。
- 常见问题：`FAQ.md`。
- 架构与插件契约：`ARCHITECTURE.md`、`README.md` 插件系统节。
