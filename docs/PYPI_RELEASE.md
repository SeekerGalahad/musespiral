# PyPI 发布步骤

> **需用户操作**：PyPI 账号 + API token。本文只准备产物与命令，**不代上传**。

## 0. 前置

- 包名 `musespiral` 需在 PyPI 上可用（`pip index versions musespiral` 查空即可用）。
- 一次性准备 token：<https://pypi.org/manage/account/token/> → 存为环境变量或 `~/.pypirc`：

```bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-AgENdGVzdC1vcmctYWN0aW9uYTox...
```

## 1. 构建（本地已验过：`musespiral-0.5.0-py3-none-any.whl` + `musespiral-0.5.0.tar.gz`）

```bash
cd modules/musespiral
find musespiral -name __pycache__ -type d -exec rm -rf {} +   # 必须先清：见下方「坑」
rm -rf dist/
.venv/bin/python -m build            # 需先 uv pip install build
.venv/bin/python -m twine check dist/*   # 元数据渲染自检（需 uv pip install twine）
```

产物检查：`dist/*.whl`（约 1.3 MB，含 `studio/ui.html` + `assets/views/` 33 个 + `plugins_builtin/` 24 个）与 `dist/*.tar.gz`（约 1.8 MB）必须同时在。

**坑（实测）**：`plugins_builtin/**` 是递归 package-data glob，会把本地跑过测试留下的 `__pycache__/*.pyc` 一起装进 wheel（12 个 `.pyc`，构建机 CPython 3.13 的字节码）。`exclude-package-data` 对它无效（`plugins_builtin` 不是 Python 包）。构建前的 `find … rm -rf` 是唯一可靠修法。验证：`unzip -l dist/*.whl | grep -c __pycache__` 应为 0。


## 2. TestPyPI 预演（强烈建议先走一遍）

```bash
.venv/bin/python -m twine upload --repository testpypi dist/*
# 另建干净 venv 验证：
python3 -m venv /tmp/ms-check && /tmp/ms-check/bin/pip install -i https://test.pypi.org/simple/ musespiral
/tmp/ms-check/bin/musespiral --help
```

TestPyPI 缺主包之外的依赖时会报解析失败——加 `--extra-index-url https://pypi.org/simple/` 再试。

## 3. 正式上传

```bash
.venv/bin/python -m twine upload dist/*
```

上传后检查 <https://pypi.org/project/musespiral/>：README 渲染、关键词、分类器、项目链接（Homepage / Documentation / Issues / Changelog）齐全。

## 4. 同时发 GitHub Release（附产物）

```bash
gh release create v0.5.0 dist/*.whl dist/*.tar.gz \
  --repo SeekerGalahad/musespiral \
  --title "v0.5.0" --notes-file CHANGELOG.md
```

## 已知渲染差异

- README 的演示图写的是仓库相对路径 `docs/demo.gif`：**GitHub 正常显示，PyPI 项目页会显示为断链**（sdist 不含 `docs/`）。如需 PyPI 页也显示，把该行改成 `https://raw.githubusercontent.com/SeekerGalahad/musespiral/main/docs/demo.gif` 再发布。
- `Development Status :: 4 - Beta` 与 README「Alpha」文案口径不同，发正式版前统一（要么改 README，要么改分类器）。

## 版本号

`pyproject.toml` 的 `version` 是唯一真源；打 tag 前同步 `CHANGELOG.md`。已上传的版本号不可覆盖，重发必须升版本。
