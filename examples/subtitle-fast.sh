#!/usr/bin/env bash
# 例 2：字幕直出 —— 跳过 ASR，比转写快约 100 倍、准确率更高（人工字幕无同音字误识）
#
# 节点链：
#   1. subtitle      解析 .srt/.vtt → segments（需给 media_path 供下游抽帧）
#   2. key-segment   从字幕段里挑关键片段 ranges
#   3. frame-grab    按 ranges 从视频抽帧（需 ffmpeg）
#   4. article-writer 组织成带时间戳锚的图文稿
#   5. material-sink 落成素材库 preview 态
# 输出结构、端口与 transcribe 完全同构，下游零改动（subtitle/plugin.py:6）。
#
# 用法： bash examples/subtitle-fast.sh <视频绝对路径> <字幕绝对路径>
# 前置： ffmpeg/ffprobe 可用（抽帧）；不需要 faster-whisper
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if [ $# -lt 2 ]; then
  echo "用法: bash $0 <视频绝对路径> <字幕 .srt/.vtt 绝对路径>" >&2
  exit 2
fi
VIDEO="$1"; SUB="$2"
[ -f "$VIDEO" ] || { echo "❌ 视频不存在: $VIDEO" >&2; exit 2; }
[ -f "$SUB"   ] || { echo "❌ 字幕不存在: $SUB"   >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "❌ 缺 ffmpeg（抽帧用，见 INSTALL.md §1）" >&2; exit 2; }

# 拼节点清单：subtitle 的 media_path 必给——否则它不产 media_path 输出端口，
# frame-grab 会缺必需输入（实测判例，见 subtitle/plugin.py manifest outputs）
NODES="$(mktemp).json"
trap 'rm -f "$NODES"' EXIT
"$PYTHON" - "$VIDEO" "$SUB" "$NODES" <<'PY'
import json, sys
video, sub, out = sys.argv[1], sys.argv[2], sys.argv[3]
nodes = [
    {"id": "sub",  "plugin": "subtitle",      "inputs": {"subtitle_path": sub, "media_path": video}},
    {"id": "ks",   "plugin": "key-segment"},
    {"id": "fg",   "plugin": "frame-grab",    "config": {"mode": "frame"}},
    {"id": "aw",   "plugin": "article-writer","config": {"mode": "chronological", "auto_adopt": False}},
    {"id": "sink", "plugin": "material-sink"},
]
open(out, "w", encoding="utf-8").write(json.dumps(nodes, ensure_ascii=False))
PY

echo "==> 运行字幕直出流水线（跳过 ASR）"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$HERE/_run_pipeline.py" "$NODES"
