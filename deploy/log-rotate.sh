#!/usr/bin/env bash
# musespiral 日志轮转（copytruncate 语义，2026-11-23 建制）
# 为什么不用 newsyslog：launchd 服务无 pidfile，newsyslog 的重命名式轮转会让
# 进程继续写旧 inode（写进已压缩文件）；copytruncate 原地截断保 inode，进程无感。
# 用法（建议由周任务调用，见 ~/.musespiral/local/weekly-ops.sh）：
#   bash log-rotate.sh [日志目录] [保留份数] [大小阈值KB]
set -euo pipefail
DIR="${1:-$HOME/.musespiral/logs}"
KEEP="${2:-3}"
SIZE_KB="${3:-1024}"

for log in "$DIR"/studio.log "$DIR"/topic-radar.log "$DIR"/backup.log; do
  [ -f "$log" ] || continue
  kb=$(( $(stat -f%z "$log") / 1024 ))
  [ "$kb" -lt "$SIZE_KB" ] && continue
  n=$(( KEEP - 1 ))
  while [ "$n" -ge 1 ]; do
    [ -f "$log.$n" ] && mv "$log.$n" "$log.$(( n + 1 ))"
    n=$(( n - 1 ))
  done
  cp "$log" "$log.1" && : > "$log"
  echo "$(date '+%F %T') 轮转 ${log}（${kb}KB → ${log}.1，保 ${KEEP} 份）"
done
