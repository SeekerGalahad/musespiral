#!/usr/bin/env bash
# musespiral 发布脚本（2026-11-23 建制，判例固化）：
#   判例1：__pycache__/*.pyc 会经 package-data glob 混进 wheel（0.5.0 真缺陷）→ 构建前强制清+产物断言
#   判例2：twine 装进项目 venv 会连带 docutils 触发许可证门误红 → twine 只装隔离 venv（~/.musespiral/release-venv）
#   判例3：README/classifier 烘焙进 sdist，发布后再改文案=重发版本 → 上传前跑文案一致性门
# 用法：
#   bash scripts/release.sh              # 干跑：清缓存+构建+三道校验（不上传不打 tag）
#   bash scripts/release.sh --upload     # 正式：干跑全过后 twine 上传 PyPI + git tag + gh release
#   bash scripts/release.sh --upload --test   # TestPyPI 预演（独立 token）
# token 来源（优先级）：$MUSESPIRAL_PYPI_TOKEN > --token-file 指定文件（文件内 grep pypi- 前缀串）
# 前置：模块 .venv 存在（build 用）；gh 已登录（tag/release 用）
set -euo pipefail

MS="$(cd "$(dirname "$0")/.." && pwd)"
cd "$MS"
PY=".venv/bin/python"
UPLOAD=0; TESTPYPI=0; TOKEN_FILE=""
for arg in "$@"; do
  case "$arg" in
    --upload) UPLOAD=1 ;;
    --test) TESTPYPI=1 ;;
    --token-file) shift_next=1 ;;
    *) [ "${shift_next:-0}" = 1 ] && { TOKEN_FILE="$arg"; shift_next=0; } ;;
  esac
done

echo "── [1/6] 清 __pycache__（0.5.0 缺陷防线）"
find musespiral -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "── [2/6] 文案一致性门（发布前口径锁）"
$PY -m pytest tests/test_narrative_consistency.py -q || { echo "✗ 文案门红——先修再发"; exit 1; }

echo "── [3/6] 构建产物"
rm -rf dist/
$PY -m build 2>&1 | tail -1
WHL=$(ls dist/*.whl); SDIST=$(ls dist/*.tar.gz)
PYC=$($PY -c "import zipfile,sys; z=zipfile.ZipFile('$WHL'); print(sum(1 for n in z.namelist() if '__pycache__' in n))")
[ "$PYC" = "0" ] || { echo "✗ wheel 含 $PYC 个 __pycache__ 条目——回查构建环境"; exit 1; }
echo "✓ ${WHL} + ${SDIST}（pycache=0）"

echo "── [4/6] 干净 venv 安装冒烟"
SMOKE=$(mktemp -d)
uv venv -q "$SMOKE/venv"
VIRTUAL_ENV="$SMOKE/venv" uv pip install -q "$WHL"
"$SMOKE/venv/bin/python" -c "from musespiral.services import plugins_service as P; v,_=P.scan_plugins(); print(f'✓ 安装冒烟：{len(v)} 插件可见')"

if [ "$UPLOAD" = "0" ]; then
  echo "── 干跑完成（产物在 dist/，未上传）。正式发布：--upload"
  rm -rf "$SMOKE"; exit 0
fi

echo "── [5/6] twine 上传（隔离 venv）"
REL_VENV="$HOME/.musespiral/release-venv"
[ -x "$REL_VENV/bin/twine" ] || { uv venv -q "$REL_VENV"; VIRTUAL_ENV="$REL_VENV" uv pip install -q twine; }
TOKEN="${MUSESPIRAL_PYPI_TOKEN:-}"
if [ -z "$TOKEN" ] && [ -n "$TOKEN_FILE" ] && [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(grep -o 'pypi-[A-Za-z0-9_-]\+' "$TOKEN_FILE" | head -1)
fi
[ -n "$TOKEN" ] || { echo "✗ 缺 token：设 MUSESPIRAL_PYPI_TOKEN 或 --token-file <文件>"; exit 1; }
REPO_URL="https://upload.pypi.org/legacy/"
[ "$TESTPYPI" = "1" ] && REPO_URL="https://test.pypi.org/legacy/"
TWINE_PASSWORD="$TOKEN" "$REL_VENV/bin/twine" upload "$WHL" "$SDIST" -u __token__ --repository-url "$REPO_URL" --non-interactive
rm -rf "$SMOKE"

echo "── [6/6] git tag + gh release"
VERSION=$($PY -c "import re; print(re.search(r'version\s*=\s*\"([^\"]+)\"', open('pyproject.toml').read()).group(1))")
git tag -f "v$VERSION" 2>/dev/null || true
git push origin "v$VERSION" 2>/dev/null || echo "（tag 推送跳过或失败——开源仓另行处理）"
echo "✓ 发布完成：v$VERSION → $REPO_URL"
