#!/usr/bin/env bash
# 例 1：完整六节点流水线 —— 视频 → 转写 → 关键片段 → 抽帧 → 成稿 → 素材库
#
# 节点链（每步产物自动作为下游输入，见 _run_pipeline.py 说明）：
#   1. local-file    校验视频文件、ffprobe 探时长
#   2. transcribe    faster-whisper 把音轨转成带时间戳/质量信号的 segments（需 ffmpeg + faster-whisper）
#   3. key-segment   从转写里挑关键片段 ranges（AI 已配置走 AI，否则规则降级）
#   4. frame-grab    按 ranges 从视频抽关键帧（需 ffmpeg）
#   5. article-writer 组织成带时间戳锚的图文稿（AI 已配置走 AI，否则模板降级）
#   6. material-sink 把成稿落成素材库 preview 态（带血统链，人工采纳才转正式）
#
# 用法： bash examples/video-to-article.sh /绝对路径/lecture.mp4
# 前置： pip install -e ".[media]"  且  ffmpeg/ffprobe 可用
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if [ $# -lt 1 ]; then
  echo "用法: bash $0 <视频绝对路径>" >&2
  echo "例:   bash $0 /Users/me/lecture.mp4" >&2
  exit 2
fi
VIDEO="$1"
[ -f "$VIDEO" ] || { echo "❌ 文件不存在: $VIDEO" >&2; exit 2; }

# 步骤 0：确认媒体工具在（transcribe / frame-grab 依赖 ffmpeg + ffprobe）
command -v ffmpeg  >/dev/null || { echo "❌ 缺 ffmpeg（见 INSTALL.md §1）" >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "❌ 缺 ffprobe" >&2; exit 2; }
# 步骤 0b：确认转写后端在（可选依赖 media 组）
"$PYTHON" -c "import faster_whisper" 2>/dev/null || {
  echo "❌ 缺 faster-whisper：pip install -e \".[media]\"" >&2; exit 2;
}
# 步骤 0c：先看有没有音轨（FAQ Q1——纯静音素材转写为空）
echo "==> 检查音轨（mean_volume 接近 -91dB = 纯静音，转写会为空）"
ffmpeg -i "$VIDEO" -af volumedetect -f null - 2>&1 | grep -iE 'mean_volume|Audio:' || true

# 步骤 1-6：拼节点清单并交给共享执行器
NODES="$(mktemp).json"
trap 'rm -f "$NODES"' EXIT
"$PYTHON" - "$VIDEO" "$NODES" <<'PY'
import json, sys
video, out = sys.argv[1], sys.argv[2]
nodes = [
    {"id": "src",  "plugin": "local-file",    "inputs": {"path": video, "media_kind": "video"}},
    {"id": "tr",   "plugin": "transcribe",    "config": {"model": "large-v3-turbo", "vad_filter": True}},
    {"id": "ks",   "plugin": "key-segment"},
    {"id": "fg",   "plugin": "frame-grab",    "config": {"mode": "frame"}},
    {"id": "aw",   "plugin": "article-writer","config": {"mode": "chronological", "auto_adopt": False}},
    {"id": "sink", "plugin": "material-sink", "config": {"triage": {"read": False, "starred": False}}},
]
open(out, "w", encoding="utf-8").write(json.dumps(nodes, ensure_ascii=False))
PY

echo "==> 运行六节点流水线"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$HERE/_run_pipeline.py" "$NODES"
