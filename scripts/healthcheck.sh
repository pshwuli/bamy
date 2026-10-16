#!/usr/bin/env bash
# 探测 /health（《部署与运行》4.5 / 9.4）。可用于 cron、监控或 start.sh 的就绪等待。
# 用法：scripts/healthcheck.sh [--quiet] [URL]
#   URL 缺省：http://127.0.0.1:${APP_PORT:-8000}/health（docker 与 native 都直连后端）
# 退出码：0 健康；1 不健康或不可达
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

QUIET=0
if [[ "${1:-}" == "--quiet" ]]; then QUIET=1; shift; fi
load_env
URL="${1:-http://127.0.0.1:$(app_port)/health}"
TIMEOUT="${HEALTHCHECK_TIMEOUT:-5}"

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time "${TIMEOUT}" "${URL}"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T "${TIMEOUT}" -O - "${URL}"
  else
    "$(python_bin)" -I -c 'import sys,urllib.request as u;print(u.urlopen(sys.argv[1],timeout=float(sys.argv[2])).read().decode())' "${URL}" "${TIMEOUT}"
  fi
}

if body="$(fetch 2>/dev/null)"; then
  (( QUIET )) || log "健康：${URL} -> ${body}"
  exit 0
fi
(( QUIET )) || printf '[%s] 不健康或不可达：%s\n' "$(date '+%F %T')" "${URL}" >&2
exit 1
