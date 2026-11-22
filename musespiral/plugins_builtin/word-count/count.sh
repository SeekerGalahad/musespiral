#!/bin/bash
# word-count 官方示例（exec runtime 实战，插件 v2 ARCH-4 §三）
# 协议：stdin = inputs 的 JSON（exec_runtime.run_exec_plugin 喂入）
#       stdout = outputs 的 JSON dict（引擎解析；须含 manifest.outputs 全键）
#       stderr = 逐行透传到运行日志（失败时尾部进 step.detail）
#
# macOS 自带 bash 3.2：无关联数组、无 ${var,,}、无进程替换——本脚本只做
# 依赖检查与进程壳；JSON 解析与计数调 python3 子进程，stdin 原样透传
# （payload 不经手 shell 变量/参数，大文本不受 ARG_MAX 限制）。
# 退出码：0=成功；2=输入协议错（非 JSON / 缺 text 字符串）；3=缺 python3。
# （非零退出码被引擎收敛为可读节点失败，stderr 尾行进 step.detail。）

if ! command -v python3 >/dev/null 2>&1; then
    echo "word-count: 需要 python3（PATH 中未找到）" >&2
    exit 3
fi

exec python3 -c '
import json, sys

raw = sys.stdin.read()
try:
    data = json.loads(raw)
except ValueError as exc:
    print(f"word-count: stdin 非合法 JSON: {exc}", file=sys.stderr)
    sys.exit(2)

text = data.get("text") if isinstance(data, dict) else None
if not isinstance(text, str):
    print("word-count: inputs.text 须为字符串", file=sys.stderr)
    sys.exit(2)

# 口径（与 manifest description 一致）：
#   chars = 字符数（含空白与标点，len 口径）
#   words = 空白分词数（英文按空格切词；中文整句无空格则计 1）
#   lines = splitlines 行数（空文本为 0）
stats = {
    "chars": len(text),
    "words": len(text.split()),
    "lines": len(text.splitlines()),
}
sys.stdout.write(json.dumps(stats, ensure_ascii=False))
'
