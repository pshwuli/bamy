#!/usr/bin/env bash
# 下载进度条：挂着就行，每 5 秒刷新一次，下载完自动退出。Ctrl+C 随时退出，不影响后台下载。
#   用法：bash research/data/progress.sh
OUT="${OUT:-/root/bamy-data/um_1m}"
LOG="${LOG:-/root/bamy-data-fetch.log}"
start=$(date +%s)
first=""
while :; do
  total=$(wc -l < "$OUT/_symbols.txt" 2>/dev/null || echo 0)
  done_n=$(find "$OUT" -maxdepth 1 -name '*.npz' ! -name '*.tmp.npz' 2>/dev/null | wc -l)
  empty=$(wc -l < "$OUT/_empty.txt" 2>/dev/null || echo 0)
  n=$((done_n + empty))
  size=$(du -sh "$OUT" 2>/dev/null | cut -f1)
  [ -z "$first" ] && first=$n
  now=$(date +%s); el=$((now - start))
  if [ "$total" -gt 0 ]; then
    pct=$((n * 100 / total))
    w=40; fill=$((pct * w / 100))
    bar=$(printf '%*s' "$fill" '' | tr ' ' '#')$(printf '%*s' $((w - fill)) '' | tr ' ' '-')
    rate_n=$((n - first))
    if [ "$rate_n" -gt 0 ] && [ "$el" -gt 0 ]; then
      left=$(( (total - n) * el / rate_n ))
      eta=$(printf '%d:%02d:%02d' $((left / 3600)) $((left % 3600 / 60)) $((left % 60)))
    else
      eta="估算中"
    fi
    printf '\r[%s] %3d%%  %d/%d 个币  %s  剩余 %s   ' "$bar" "$pct" "$n" "$total" "${size:-0}" "$eta"
  else
    printf '\r正在列出交易对…   '
  fi
  if grep -q FETCH_DONE "$LOG" 2>/dev/null; then
    printf '\n下载完成：%d 个币有数据，%d 个该时段无数据，共 %s\n' "$done_n" "$empty" "$size"
    exit 0
  fi
  if ! pgrep -f fetch_um_1m.sh >/dev/null; then
    printf '\n后台下载没在运行（可能还没启动或中途退出了），看日志：tail -20 %s\n' "$LOG"
    exit 1
  fi
  sleep 5
done
