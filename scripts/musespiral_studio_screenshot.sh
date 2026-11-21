#!/usr/bin/env bash
# mdpub Studio 三视图截图冒烟（设计 §16 F19-3）——人工/CI 视觉验收工具，
# 不进 pytest 默认集。
#
# 用法:
#   scripts/mdpub_studio_screenshot.sh [输出目录]    # 默认 /tmp/mdpub_studio_shots
#
# 行为:
#   1. 以 tests/fixtures/studio_demo 为 root 起 Studio（mdpub.studio.make_server，
#      仅绑 127.0.0.1、系统分配端口，MDPUB_HOME 隔离到临时目录）
#   2. Chrome headless 依次截 队列 / 文章详情 / 仪表盘 三视图
#      （--virtual-time-budget 等前端 fetch/render 完成后落图）
#   3. 结束清理服务进程与临时目录（trap EXIT）
#
# 环境变量覆盖:
#   MDPUB_PYTHON           已安装 mdpub 的 Python 解释器
#                          （默认依次试 /tmp/mdpub-venv/bin/python、python3）
#   CHROME_BIN             Chrome/Chromium 可执行文件（默认探测常见安装位置）
#   MDPUB_STUDIO_DEMO_ROOT demo 工作目录（默认 tests/fixtures/studio_demo）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEMO_ROOT="${MDPUB_STUDIO_DEMO_ROOT:-$MODULE_DIR/tests/fixtures/studio_demo}"
OUT_DIR="${1:-/tmp/mdpub_studio_shots}"

[ -d "$DEMO_ROOT" ] || { echo "demo fixture 不存在: $DEMO_ROOT" >&2; exit 1; }
mkdir -p "$OUT_DIR"

# ---- 解释器：需能 import mdpub（editable 安装的 venv 或系统 python）----
PYTHON="${MDPUB_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x /tmp/mdpub-venv/bin/python ]; then
    PYTHON=/tmp/mdpub-venv/bin/python
  else
    PYTHON=python3
  fi
fi
"$PYTHON" -c "import mdpub" >/dev/null 2>&1 || {
  echo "Python 无法 import mdpub（用 MDPUB_PYTHON 指定已安装 mdpub 的解释器）" >&2
  exit 1
}

# ---- Chrome 探测（macOS 常规安装位 → Linux 发行版包名）----
CHROME="${CHROME_BIN:-}"
if [ -z "$CHROME" ]; then
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "/usr/bin/google-chrome" \
    "/usr/bin/chromium" \
    "/usr/bin/chromium-browser"; do
    if [ -x "$candidate" ]; then
      CHROME="$candidate"
      break
    fi
  done
fi
[ -n "$CHROME" ] || { echo "未找到 Chrome/Chromium（用 CHROME_BIN 指定）" >&2; exit 1; }

# headless 模式探测：新 Chrome 用 --headless=new，旧版回退 --headless
HEADLESS_FLAGS="--headless=new"
if ! "$CHROME" --headless=new --disable-gpu --dump-dom about:blank >/dev/null 2>&1; then
  HEADLESS_FLAGS="--headless"
fi

# ---- 起 Studio：make_server(port=0)，实际端口写入临时文件 ----
RUN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mdpub-studio-shot.XXXXXX")"
PORT_FILE="$RUN_DIR/port"
MDPUB_HOME="$RUN_DIR/home" "$PYTHON" - "$DEMO_ROOT" "$PORT_FILE" <<'PY' &
import sys

from mdpub.studio import make_server

root, port_file = sys.argv[1], sys.argv[2]
httpd = make_server(root, port=0)  # 127.0.0.1 + 系统分配端口
with open(port_file, "w", encoding="utf-8") as fh:
    fh.write(str(httpd.server_address[1]))
httpd.serve_forever()
PY
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
  wait "$SERVER_PID" 2>/dev/null || true  # 收尸并抑制 bash 的 Terminated 作业通知
  rm -rf "$RUN_DIR"
}
trap cleanup EXIT

# 等服务就绪（端口文件 + /api/meta 探活，最多 ~10s）
PORT=""
for _ in $(seq 1 50); do
  if [ -s "$PORT_FILE" ]; then
    PORT="$(cat "$PORT_FILE")"
    curl -fsS "http://127.0.0.1:$PORT/api/meta" >/dev/null 2>&1 && break
  fi
  sleep 0.2
done
if [ -z "$PORT" ] || ! curl -fsS "http://127.0.0.1:$PORT/api/meta" >/dev/null 2>&1; then
  echo "Studio 服务未就绪（port=${PORT:-未分配}）" >&2
  exit 1
fi
BASE="http://127.0.0.1:$PORT"

# ---- 三视图截图（失败即退出非零，供 CI 判定）----
shot() {  # shot <hash 路由> <输出文件名>
  "$CHROME" $HEADLESS_FLAGS --disable-gpu --hide-scrollbars \
    --user-data-dir="$RUN_DIR/chrome-profile" \
    --window-size=1440,1100 --virtual-time-budget=6000 \
    --screenshot="$OUT_DIR/$2" "$BASE/#$1" >/dev/null 2>&1
  [ -s "$OUT_DIR/$2" ] || { echo "截图失败: $2（hash=$1）" >&2; exit 1; }
  echo "已生成: $OUT_DIR/$2"
}

shot "queue" "01-queue.png"
shot "article?path=01_quick_start.md&tab=preview" "02-article.png"
shot "dashboard" "03-dashboard.png"

echo "三视图截图完成: $OUT_DIR"
