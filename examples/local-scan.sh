#!/usr/bin/env bash
# 例 4：本地媒体搜索 —— 扫描目录找音视频，产出 Markdown 清单
#
# 节点链（单节点即可，local-scan 是 source 插件）：
#   local-scan  在显式给的 roots 白名单目录里发现音视频，产出
#               markdown 清单（路径|大小|时长|格式，按大小降序）
# 安全边界：拒空 roots 与 / 根；硬排除 .git/node_modules/Library/Caches/
#           Library Containers（微信/QQ 隐私）；符号链接不跟随；5 万条目限额防卡死
#           （local-scan/manifest.json description）
#
# 用法： bash examples/local-scan.sh <要扫描的目录绝对路径> [最小MB，默认1]
# 前置： ffprobe 可用（对发现的文件并发 ≤4 探时长，单文件 3s 超时，失败留空不失败）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if [ $# -lt 1 ]; then
  echo "用法: bash $0 <目录绝对路径> [最小MB]" >&2
  echo "例:   bash $0 /Users/me/Lectures 10" >&2
  exit 2
fi
ROOTDIR="$1"; MIN_MB="${2:-1}"
[ -d "$ROOTDIR" ] || { echo "❌ 目录不存在: $ROOTDIR" >&2; exit 2; }
command -v ffprobe >/dev/null || echo "⚠️ 缺 ffprobe——时长列会留空（清单照出）" >&2

# roots 必须是绝对路径；拒绝 / 根（插件自身也拦，这里先挡一层给友好提示）
case "$ROOTDIR" in
  /) echo "❌ 不允许扫根目录 /（local-scan 安全边界）" >&2; exit 2 ;;
esac
ABS="$(cd "$ROOTDIR" && pwd)"

NODES="$(mktemp).json"
trap 'rm -f "$NODES"' EXIT
"$PYTHON" - "$ABS" "$MIN_MB" "$NODES" <<'PY'
import json, sys
root, min_mb, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
node = {
    "id": "scan", "plugin": "local-scan",
    "inputs": {"roots": root, "max_depth": 3, "min_size_mb": min_mb,
               "pattern": "mp4,mov,mkv,avi,webm,wav,mp3,m4a,flac"},
}
open(out, "w", encoding="utf-8").write(json.dumps([node], ensure_ascii=False))
PY

echo "==> 扫描目录: ${ABS}（min_size=${MIN_MB}MB）"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$HERE/_run_pipeline.py" "$NODES"
echo
echo "提示：清单在该 run 的 workspace/scan/outputs.json 的 inline.files / inline.manifest；"
echo "      从清单挑一个文件，再用 video-to-article.sh 跑完整链路。"
