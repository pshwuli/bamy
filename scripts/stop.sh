#!/usr/bin/env bash
# 停止 BAMY（优雅停止，总超时 60 秒，超时强制退出，《部署与运行》7.2-7.4）。
#   docker 模式：docker compose stop（保留容器与卷）；加 --down 则 compose down（仍保留卷）
#   native 模式：向 PID 文件中的进程发 SIGTERM，60 秒后仍在则 SIGKILL
# 用法：scripts/stop.sh [--down]
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

GRACE=60
load_env

if [[ "${MODE}" == docker ]]; then
  if [[ "${1:-}" == "--down" ]]; then
    compose down --timeout "${GRACE}"
  else
    compose stop --timeout "${GRACE}"
  fi
  log "已停止（docker）"
  exit 0
fi

if [[ ! -f "${PID_FILE}" ]]; then
  log "未找到 PID 文件，BAMY 未在运行"
  exit 0
fi
PID="$(cat "${PID_FILE}")"
if ! kill -0 "${PID}" 2>/dev/null; then
  log "进程 ${PID} 不存在，清理 PID 文件"
  rm -f "${PID_FILE}"
  exit 0
fi

log "发送 SIGTERM 到 ${PID}，等待最多 ${GRACE} 秒..."
kill -TERM "${PID}"
for _ in $(seq 1 "${GRACE}"); do
  if ! kill -0 "${PID}" 2>/dev/null; then
    rm -f "${PID_FILE}"
    log "已优雅停止"
    exit 0
  fi
  sleep 1
done

warn "优雅停止超时，发送 SIGKILL；下次启动将走崩溃恢复"
kill -KILL "${PID}" 2>/dev/null || true
rm -f "${PID_FILE}"
