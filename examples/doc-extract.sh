#!/usr/bin/env bash
# 例 3：文档提取 —— PDF / DOCX / EPUB / Markdown / TXT → 文本
#
# 节点链（单节点即可，doc-extract 是 process 插件）：
#   doc-extract  按后缀选后端：PDF→pdfplumber / DOCX→python-docx /
#                EPUB→纯标准库 / MD·TXT→直接读；装了 markitdown 则优先用它
#                扫描版 PDF 会明确拒绝（不静默返回空文），需 OCR（v2）
#
# 用法： bash examples/doc-extract.sh <文档绝对路径> [标题]
# 前置： PDF 需 pip install pdfplumber；DOCX 需 pip install python-docx；
#        EPUB/MD/TXT 零可选依赖；更多格式 pip install -e ".[docs]"（FAQ Q2）
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if [ $# -lt 1 ]; then
  echo "用法: bash $0 <文档绝对路径> [标题]" >&2
  echo "支持: .pdf .docx .epub .md .txt（及装 markitdown 后的更多格式）" >&2
  exit 2
fi
DOC="$1"; TITLE="${2:-}"

# 步骤 0：确认后缀被支持（doc-extract 有白名单，越界可读报错）
# 用 tr 转小写而非 ${VAR,,}——后者是 bash 4 语法，macOS 自带 bash 3.2 不支持
DOC_LOWER="$(printf '%s' "$DOC" | tr '[:upper:]' '[:lower:]')"
case "$DOC_LOWER" in
  *.pdf|*.docx|*.epub|*.md|*.txt|*.pptx|*.xlsx) ;;
  *) echo "⚠️ 后缀可能不受支持: ${DOC}（基础版支持 pdf/docx/epub/md/txt）" >&2 ;;
esac

NODES="$(mktemp).json"
trap 'rm -f "$NODES"' EXIT
"$PYTHON" - "$DOC" "$TITLE" "$NODES" <<'PY'
import json, sys
doc, title, out = sys.argv[1], sys.argv[2], sys.argv[3]
node = {"id": "doc", "plugin": "doc-extract", "inputs": {"doc_path": doc}}
if title:
    node["inputs"]["title"] = title
open(out, "w", encoding="utf-8").write(json.dumps([node], ensure_ascii=False))
PY

echo "==> 运行文档提取"
PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$HERE/_run_pipeline.py" "$NODES"
echo
echo "提示：提取的 markdown/text 在该 run 的 workspace/doc/outputs.json 的 inline 段；"
echo "      扫描版 PDF 若被拒绝，属于诚实报错——需 OCR（当前 v0.5 未提供）。"
