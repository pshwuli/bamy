#!/usr/bin/env bash
# scripts/*.sh 的公共函数，只供 source，不直接执行。
# 部署模式：
#   docker —— Docker Compose（主推），环境变量来自 docker/.env
#   native —— apt + pip 手动安装（备选，Termux 只走这条），环境变量来自根目录 .env
# 显式指定：BAMY_MODE=docker|native；未指定时有 docker/.env 且 docker 可用则为 docker。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_DIR="${ROOT_DIR}/docker"
COMPOSE_FILE="${DOCKER_DIR}/docker-compose.yml"
RUN_DIR="${ROOT_DIR}/data/run"
PID_FILE="${RUN_DIR}/bamy.pid"

log()  { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date '+%F %T')" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

detect_mode() {
  # 1) 显式指定优先
  if [[ -n "${BAMY_MODE:-}" ]]; then
    [[ "${BAMY_MODE}" == docker || "${BAMY_MODE}" == native ]] || die "BAMY_MODE 只能是 docker 或 native"
    echo "${BAMY_MODE}"; return
  fi
  # 2) 看实际在跑的是哪种：native 有 PID 文件且进程活着；docker 有 bamy 的 app 容器在跑。
  #    只看 docker/.env 在不在会误判——native 部署常常也留着 docker/.env。
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}" 2>/dev/null)" 2>/dev/null; then
    echo native; return
  fi
  if command -v docker >/dev/null 2>&1 && [[ -n "$(docker ps -q --filter label=com.docker.compose.project=bamy \
       --filter label=com.docker.compose.service=app 2>/dev/null)" ]]; then
    echo docker; return
  fi
  # 3) 都没在跑（首次启动或已停止）：有 docker/.env 且装了 docker 才走 docker
  if [[ -f "${DOCKER_DIR}/.env" ]] && command -v docker >/dev/null 2>&1; then
    echo docker
  else
    echo native
  fi
}

MODE="$(detect_mode)"
if [[ "${MODE}" == docker ]]; then
  ENV_FILE="${DOCKER_DIR}/.env"
else
  ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
fi

# 读取 env 文件到当前 shell；已在进程环境中的变量优先（与“环境变量最高”一致）
load_env() {
  [[ -f "${ENV_FILE}" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ "${line}" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "${line}" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
    # 去掉一层成对引号
    if [[ "${val}" =~ ^\"(.*)\"$ || "${val}" =~ ^\'(.*)\'$ ]]; then val="${BASH_REMATCH[1]}"; fi
    [[ -n "${!key+x}" ]] || export "${key}=${val}"
  done < "${ENV_FILE}"
}

compose() {
  docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
}

# 本地 python：优先仓库虚拟环境
python_bin() {
  if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then echo "${ROOT_DIR}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then command -v python3
  else die "未找到 python3"; fi
}

# 当前生效的 DATABASE_URL（docker 模式留空时与 compose 的拼接规则一致）
effective_db_url() {
  if [[ -n "${DATABASE_URL:-}" ]]; then echo "${DATABASE_URL}"
  elif [[ "${MODE}" == docker ]]; then
    echo "postgresql+asyncpg://${BAMY_DB_USER:-bamy_app}:***@db:5432/${POSTGRES_DB:-bamy}"
  else
    echo "sqlite+aiosqlite:///./data/bamy.db"
  fi
}

db_kind() {
  case "$(effective_db_url)" in
    sqlite*)     echo sqlite ;;
    postgresql*) echo postgres ;;
    *) die "无法识别的 DATABASE_URL 类型（只支持 sqlite / postgresql）" ;;
  esac
}

# sqlite URL → 宿主机绝对路径（native 模式；相对路径相对仓库根目录，与启动目录一致）
sqlite_path() {
  local url path
  url="$(effective_db_url)"
  path="${url#*:///}"
  path="${path%%\?*}"
  [[ "${path}" == /* ]] || path="${ROOT_DIR}/${path#./}"
  echo "${path}"
}

# 把 postgres URL 解析成 PG* 环境变量，避免密码出现在进程参数里
export_pg_env() {
  local py
  py="$(python_bin)"
  eval "$("${py}" -I - "$1" <<'PY'
import shlex, sys
from urllib.parse import urlsplit, unquote
u = urlsplit(sys.argv[1].replace("postgresql+asyncpg://", "postgresql://", 1))
pairs = {
    "PGHOST": u.hostname or "127.0.0.1",
    "PGPORT": str(u.port or 5432),
    "PGUSER": unquote(u.username or ""),
    "PGPASSWORD": unquote(u.password or ""),
    "PGDATABASE": u.path.lstrip("/") or "postgres",
}
for k, v in pairs.items():
    print(f"export {k}={shlex.quote(v)}")
PY
)"
}

app_port() { echo "${APP_PORT:-8000}"; }
