#!/bin/bash
# mdpub 一键环境搭建：venv + 依赖 + editable 安装 + 自检
# 用法：
#   bash scripts/setup.sh                 # 标准安装（核心 + pytest）
#   bash scripts/setup.sh --with-browser  # 追加 Playwright + chromium（upload/e2e 用）
# 环境变量：
#   MDPUB_VENV  venv 目录（默认模块内 .venv；勿用 /tmp——macOS 重启清理）
# 兼容 macOS bash 3.2（无进程替换/数组）。
set -eu

cd "$(dirname "$0")/.."
VENV_DIR="${MDPUB_VENV:-.venv}"
PY="${MDPUB_PYTHON:-python3}"
MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"
WITH_BROWSER=0
for arg in "$@"; do
  case "$arg" in
    --with-browser) WITH_BROWSER=1 ;;
    *) echo "未知参数: $arg（可用: --with-browser）"; exit 1 ;;
  esac
done

echo "==> Python 版本检查（要求 >= 3.10）"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "❌ $PY 版本低于 3.10（launchd 默认 python3 为 3.9，交互终端请用 brew python）"; exit 1;
}

if [ ! -d "$VENV_DIR" ]; then
  echo "==> 创建 venv: $VENV_DIR"
  "$PY" -m venv "$VENV_DIR"
else
  echo "==> 复用既有 venv: $VENV_DIR"
fi

# 统一经 `python -m pip` 调用——不依赖 bin/pip 命名（uv venv 只装 pip3/pip3.13）
run_pip() { "$VENV_DIR/bin/python" -m pip "$@"; }

# uv 创建的 venv 默认不带 pip（pyvenv.cfg 含 uv = ...）——ensurepip 引导，
# 失败则提示重建（uv venv 的 site-packages 布局与 pip 安装混用易出隐性冲突）
if ! "$VENV_DIR/bin/python" -m pip --version > /dev/null 2>&1; then
  echo "==> venv 无 pip（uv venv 特征）：ensurepip 引导"
  "$VENV_DIR/bin/python" -m ensurepip --upgrade > /dev/null 2>&1 || {
    echo "❌ $VENV_DIR 无 pip 且 ensurepip 失败（uv venv 布局）；建议重建：rm -rf $VENV_DIR && bash $0"
    exit 1
  }
fi

echo "==> 安装依赖（editable + test extra，清华镜像优先、失败回退直连）"
run_pip install -q -e ".[test]" $MIRROR || run_pip install -q -e ".[test]"

if [ "$WITH_BROWSER" = "1" ]; then
  echo "==> 安装浏览器依赖（playwright extra）"
  run_pip install -q -e ".[browser]" $MIRROR || run_pip install -q -e ".[browser]"
  echo "==> 下载 chromium（失败时设置 PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ 重试）"
  "$VENV_DIR/bin/playwright" install chromium
fi

echo "==> 自检"
"$VENV_DIR/bin/python" -c "import mdpub; print('  版本:', mdpub.__version__)"
"$VENV_DIR/bin/mdpub" --help > /dev/null && echo "  CLI: OK"
"$VENV_DIR/bin/python" -m pytest tests/test_cli_snapshot.py -q > /dev/null && echo "  快照测试: OK"

echo ""
echo "✅ 环境就绪。常用命令："
echo "   $VENV_DIR/bin/mdpub studio --root <文章目录>      # 启动工作台"
echo "   $VENV_DIR/bin/python -m pytest tests/ -q          # 全量测试"
echo "   $VENV_DIR/bin/python -m pytest tests/ -m e2e -q   # E2E（需 --with-browser）"
