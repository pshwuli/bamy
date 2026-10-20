#!/usr/bin/env bash
# 初始化数据库（《部署与运行》4.3 第 7 步 / 4.4）：
#   有 Alembic 配置（backend/alembic.ini）→ alembic upgrade head
#   否则 → 调用 bamy.db.session.init_db() 按模型建表（幂等，已存在的表不动）
# 管理员不在这里创建：首次访问 /setup，或设置 INIT_ADMIN_USER / INIT_ADMIN_PASS。
# 用法：scripts/init-db.sh
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

load_env
KIND="$(db_kind)"
log "初始化数据库：模式=${MODE} 类型=${KIND}"

INIT_PY='import asyncio
from bamy.db.session import init_db
asyncio.run(init_db())
print("init_db 完成")'

if [[ "${MODE}" == docker ]]; then
  compose up -d db
  if [[ "${KIND}" == postgres ]]; then
    for _ in $(seq 1 30); do compose exec -T db pg_isready -q && break; sleep 2; done
  fi
  compose run --rm --entrypoint sh app -c '
    set -e
    cd /app
    if [ -f backend/alembic.ini ]; then
      alembic -c backend/alembic.ini upgrade head
    else
      python -c "$0"
    fi' "${INIT_PY}"
  log "完成"
  exit 0
fi

# ---------- native ----------
PY="$(python_bin)"
cd "${ROOT_DIR}"
mkdir -p data
export PYTHONPATH="${ROOT_DIR}/backend${PYTHONPATH:+:${PYTHONPATH}}"
export ENV_FILE
if [[ -f "${ROOT_DIR}/backend/alembic.ini" ]]; then
  # 必须在仓库根目录跑：DATABASE_URL 默认是相对路径 ./data/bamy.db，换到 backend/ 下会把库建错地方
  "${PY}" -m alembic -c backend/alembic.ini upgrade head
else
  "${PY}" -c "${INIT_PY}"
fi
log "完成"
