#!/usr/bin/env bash
# 启动 BAMY。
#   docker 模式：docker compose up -d --build，等待 app 健康
#   native 模式：后台启动 uvicorn（工作目录为仓库根目录），PID 写入 data/run/bamy.pid
# 用法：scripts/start.sh [--foreground]    （--foreground 仅 native，前台调试）
# 环境：BAMY_MODE=docker|native 强制模式
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FOREGROUND=0
[[ "${1:-}" == "--foreground" ]] && FOREGROUND=1

load_env
log "部署模式：${MODE}，环境文件：${ENV_FILE}"

check_secrets() {
  if [[ "${APP_ENV:-dev}" == prod ]]; then
    [[ -n "${SECRET_KEY:-}" ]]  || die "APP_ENV=prod 时 SECRET_KEY 必填（生成：python3 -c \"import secrets;print(secrets.token_urlsafe(48))\"）"
    [[ -n "${ENCRYPT_KEY:-}" ]] || die "APP_ENV=prod 时 ENCRYPT_KEY 必填"
    (( ${#SECRET_KEY} >= 32 && ${#ENCRYPT_KEY} >= 32 )) || die "SECRET_KEY / ENCRYPT_KEY 长度必须 ≥ 32"
    [[ "${SECRET_KEY}" != "${ENCRYPT_KEY}" ]] || die "SECRET_KEY 与 ENCRYPT_KEY 必须不同"
  fi
}

if [[ "${MODE}" == docker ]]; then
  [[ -f "${ENV_FILE}" ]] || die "缺少 ${ENV_FILE}，请先 cp docker/.env.docker.example docker/.env 并填写"
  check_secrets
  if [[ -z "${DATABASE_URL:-}" ]]; then
    [[ -n "${POSTGRES_PASSWORD:-}" && -n "${BAMY_DB_PASSWORD:-}" ]] || die "POSTGRES_PASSWORD / BAMY_DB_PASSWORD 未设置"
  fi
  mkdir -p "${ROOT_DIR}/frontend/dist"
  compose up -d --build
  # nginx.conf 是挂载文件，up 不会因内容变化重建容器：显式校验并热加载，配置错误直接失败
  compose exec -T nginx nginx -t >/dev/null 2>&1 && compose exec -T nginx nginx -s reload >/dev/null 2>&1 || true
  log "等待 app 健康检查（最长 120 秒）..."
  for _ in $(seq 1 60); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$(compose ps -q app)" 2>/dev/null || echo unknown)"
    if [[ "${status}" == healthy ]]; then
      log "BAMY 已就绪：http://localhost:${HTTP_PORT:-80}/  首次使用访问 /setup 创建管理员"
      exit 0
    fi
    sleep 2
  done
  compose logs --tail=50 app >&2 || true
  die "启动超时（120 秒），app 状态：${status}"
fi

# ---------- native ----------
[[ -f "${ENV_FILE}" ]] || warn "未找到 ${ENV_FILE}，将使用内置默认值（仅适合开发）"
check_secrets
PY="$(python_bin)"
LOG_PATH="${LOG_DIR:-./logs}"
[[ "${LOG_PATH}" == /* ]] || LOG_PATH="${ROOT_DIR}/${LOG_PATH#./}"
LOG_FILE="${LOG_PATH}/bamy.out"
mkdir -p "${RUN_DIR}" "${ROOT_DIR}/data" "${LOG_PATH}"

# 锁文件防多实例（《部署与运行》6.6）
if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  log "BAMY 已在运行（PID $(cat "${PID_FILE}")），不重复启动"
  exit 0
fi
rm -f "${PID_FILE}"

cd "${ROOT_DIR}"
export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}"
export ENV_FILE
CMD=("${PY}" -m uvicorn bamy.main:app
     --host "${APP_HOST:-0.0.0.0}" --port "$(app_port)"
     --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1}" --timeout-graceful-shutdown 60)

if (( FOREGROUND )); then
  exec "${CMD[@]}"
fi

nohup "${CMD[@]}" >>"${LOG_FILE}" 2>&1 &
echo $! > "${PID_FILE}"
log "已后台启动，PID $(cat "${PID_FILE}")，输出：${LOG_FILE}"

for _ in $(seq 1 60); do
  if ! kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
    tail -n 50 "${LOG_FILE}" >&2 || true
    rm -f "${PID_FILE}"
    die "进程已退出，见上方日志"
  fi
  if "${SCRIPT_DIR}/healthcheck.sh" --quiet; then
    log "BAMY 已就绪：监听 ${APP_HOST:-0.0.0.0}:$(app_port)，本机访问 http://127.0.0.1:$(app_port)/  首次使用访问 /setup 创建管理员"
    exit 0
  fi
  sleep 2
done
die "启动超时（120 秒），请检查 ${LOG_FILE}"
