#!/usr/bin/env bash
# 备份 BAMY（《部署与运行》11.1-11.5）：数据库 + config/ + extensions/ 打成一个归档。
# 数据库类型按 DATABASE_URL 判断：
#   sqlite*      → sqlite3 在线备份 API（不停服，结果一致），文件 db.sqlite3
#   postgresql*  → pg_dump -Fc（docker 模式在 db 容器内执行），文件 db.pgdump
# 用法：scripts/backup.sh [--with-keys]
#   --with-keys  同时把环境文件（含 SECRET_KEY/ENCRYPT_KEY）放进归档；默认不放，密钥应单独保管
# 环境变量：
#   BACKUP_DIR             输出目录，默认 <仓库>/backups
#   BACKUP_RETENTION_DAYS  保留天数，默认 7；0 表示不清理
#   BACKUP_PASSPHRASE      设置后用 openssl AES-256 加密归档（.tar.gz.enc）
# 适合 cron：0 3 * * * /path/to/bamy/scripts/backup.sh >> /path/to/bamy/logs/backup.log 2>&1
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

WITH_KEYS=0
[[ "${1:-}" == "--with-keys" ]] && WITH_KEYS=1

load_env
BACKUP_DIR="${BACKUP_DIR:-${ROOT_DIR}/backups}"
[[ "${BACKUP_DIR}" == /* ]] || BACKUP_DIR="${ROOT_DIR}/${BACKUP_DIR#./}"
RETENTION="${BACKUP_RETENTION_DAYS:-7}"
STAMP="$(date '+%Y%m%d-%H%M%S')"
NAME="bamy-${STAMP}"
KIND="$(db_kind)"

umask 077
mkdir -p "${BACKUP_DIR}"
WORK="$(mktemp -d "${BACKUP_DIR}/.tmp-${NAME}-XXXX")"
trap 'rm -rf "${WORK}"' EXIT
STAGE="${WORK}/${NAME}"
mkdir -p "${STAGE}"

log "开始备份：模式=${MODE} 数据库=${KIND}"

# ---------- 数据库 ----------
SQLITE_BACKUP_PY='import sqlite3,sys
src=sqlite3.connect(f"file:{sys.argv[1]}?mode=ro",uri=True); dst=sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
ok=dst.execute("PRAGMA integrity_check").fetchone()[0]; dst.close(); src.close()
sys.exit(0 if ok=="ok" else "integrity_check 失败: "+ok)'

case "${MODE}:${KIND}" in
  docker:postgres)
    if [[ -n "${DATABASE_URL:-}" ]] && [[ "${DATABASE_URL}" != *"@db:"* && "${DATABASE_URL}" != *"@db/"* ]]; then
      # 外部数据库：用宿主机 pg_dump
      command -v pg_dump >/dev/null || die "外部 PostgreSQL 需要宿主机安装 pg_dump（apt install postgresql-client）"
      export_pg_env "${DATABASE_URL}"
      pg_dump -Fc --no-owner -f "${STAGE}/db.pgdump"
    else
      compose exec -T db sh -c 'pg_dump -Fc --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > "${STAGE}/db.pgdump"
    fi
    ;;
  docker:sqlite)
    path="${DATABASE_URL#*:///}"; path="${path%%\?*}"
    [[ "${path}" == /* ]] || path="/app/${path#./}"
    compose exec -T app python -c "${SQLITE_BACKUP_PY}" "${path}" /app/data/.backup.sqlite3
    docker cp "$(compose ps -q app):/app/data/.backup.sqlite3" "${STAGE}/db.sqlite3"
    compose exec -T app rm -f /app/data/.backup.sqlite3
    ;;
  native:postgres)
    command -v pg_dump >/dev/null || die "未找到 pg_dump（apt install postgresql-client）"
    export_pg_env "${DATABASE_URL}"
    pg_dump -Fc --no-owner -f "${STAGE}/db.pgdump"
    ;;
  native:sqlite)
    src="$(sqlite_path)"
    [[ -f "${src}" ]] || die "SQLite 文件不存在：${src}"
    "$(python_bin)" -I -c "${SQLITE_BACKUP_PY}" "${src}" "${STAGE}/db.sqlite3"
    ;;
esac
DB_FILE="$(find "${STAGE}" -maxdepth 1 -name 'db.*' -type f -size +0 | head -n1)"
[[ -n "${DB_FILE}" ]] || die "数据库备份为空"

# ---------- 配置与扩展 ----------
[[ -d "${ROOT_DIR}/config" ]] && cp -a "${ROOT_DIR}/config" "${STAGE}/config"
[[ -d "${ROOT_DIR}/extensions" ]] && cp -a "${ROOT_DIR}/extensions" "${STAGE}/extensions"
if (( WITH_KEYS )) && [[ -f "${ENV_FILE}" ]]; then
  cp "${ENV_FILE}" "${STAGE}/env"
  warn "归档包含密钥，请妥善保管（建议同时设置 BACKUP_PASSPHRASE）"
fi

cat > "${STAGE}/MANIFEST" <<EOF
created=${STAMP}
mode=${MODE}
db_kind=${KIND}
with_keys=${WITH_KEYS}
host=$(hostname 2>/dev/null || echo unknown)
EOF
( cd "${STAGE}" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )

# ---------- 打包 / 加密 ----------
OUT="${BACKUP_DIR}/${NAME}.tar.gz"
tar -C "${WORK}" -czf "${OUT}" "${NAME}"
if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
  command -v openssl >/dev/null || die "设置了 BACKUP_PASSPHRASE 但未找到 openssl"
  openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -in "${OUT}" -out "${OUT}.enc" -pass env:BACKUP_PASSPHRASE
  rm -f "${OUT}"
  OUT="${OUT}.enc"
fi
tar -tzf "${OUT}" >/dev/null 2>&1 || [[ "${OUT}" == *.enc ]] || die "归档校验失败：${OUT}"
log "备份完成：${OUT}（$(du -h "${OUT}" | cut -f1)）"

# ---------- 保留策略 ----------
if [[ "${RETENTION}" =~ ^[0-9]+$ ]] && (( RETENTION > 0 )); then
  find "${BACKUP_DIR}" -maxdepth 1 -type f \( -name 'bamy-*.tar.gz' -o -name 'bamy-*.tar.gz.enc' \) \
       -mtime "+${RETENTION}" -print -delete | sed 's/^/[清理] /' || true
fi
