#!/usr/bin/env bash
# 从 backup.sh 生成的归档恢复（《部署与运行》12.2）：
#   停服 → 备份当前数据（防误操作）→ 恢复数据库 → 恢复配置 → 恢复密钥（若归档含）→ 恢复扩展 → 启动 → 验证
# 用法：scripts/restore.sh <归档路径> [--yes]
#   --yes  跳过第一次确认；第二次确认（输入 RESTORE）仍然需要，除非 BAMY_RESTORE_CONFIRM=RESTORE
# 加密归档（.enc）需设置 BACKUP_PASSPHRASE。
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ARCHIVE="${1:-}"
ASSUME_YES=0
[[ "${2:-}" == "--yes" ]] && ASSUME_YES=1
[[ -n "${ARCHIVE}" && -f "${ARCHIVE}" ]] || die "用法：scripts/restore.sh <归档路径> [--yes]"
ARCHIVE="$(cd "$(dirname "${ARCHIVE}")" && pwd)/$(basename "${ARCHIVE}")"

load_env
KIND="$(db_kind)"

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
umask 077

# ---------- 解包与校验 ----------
TARBALL="${ARCHIVE}"
if [[ "${ARCHIVE}" == *.enc ]]; then
  [[ -n "${BACKUP_PASSPHRASE:-}" ]] || die "加密归档需要 BACKUP_PASSPHRASE"
  TARBALL="${WORK}/archive.tar.gz"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -in "${ARCHIVE}" -out "${TARBALL}" -pass env:BACKUP_PASSPHRASE
fi
tar -C "${WORK}" -xzf "${TARBALL}"
SRC="$(find "${WORK}" -mindepth 1 -maxdepth 1 -type d -name 'bamy-*' | head -n1)"
[[ -n "${SRC}" && -f "${SRC}/MANIFEST" ]] || die "归档结构不正确（缺少 MANIFEST）"
( cd "${SRC}" && sha256sum -c --quiet SHA256SUMS ) || die "SHA256 校验失败，归档可能已损坏"

ARCH_KIND="$(sed -n 's/^db_kind=//p' "${SRC}/MANIFEST")"
[[ "${ARCH_KIND}" == "${KIND}" ]] || die "归档数据库类型（${ARCH_KIND}）与当前 DATABASE_URL（${KIND}）不一致"

# ---------- 二次确认 ----------
echo "================================================================"
echo " 即将用归档覆盖当前数据：${ARCHIVE}"
sed 's/^/   /' "${SRC}/MANIFEST"
echo " 目标：模式=${MODE} 数据库=${KIND} $(effective_db_url | sed -E 's#://([^:/@]+):[^@]*@#://\1:***@#')"
echo " 服务将被停止；恢复前会先自动备份当前数据。"
echo "================================================================"
if (( ! ASSUME_YES )); then
  read -r -p "第一次确认：继续吗？[y/N] " a1
  [[ "${a1}" =~ ^[Yy]$ ]] || die "已取消"
fi
if [[ "${BAMY_RESTORE_CONFIRM:-}" != "RESTORE" ]]; then
  read -r -p "第二次确认：请输入大写 RESTORE 继续： " a2
  [[ "${a2}" == "RESTORE" ]] || die "已取消"
fi

# ---------- 1. 停服（docker 模式保留 db 以便导入）----------
if [[ "${MODE}" == docker ]]; then
  compose stop app nginx || true
else
  "${SCRIPT_DIR}/stop.sh"
fi

# ---------- 2. 防误操作：先备份当前数据 ----------
log "备份当前数据..."
if [[ "${MODE}" == docker && "${KIND}" == postgres ]]; then
  compose up -d db
fi
if [[ "${MODE}" == docker && "${KIND}" == sqlite ]]; then
  warn "docker+sqlite：app 已停止，跳过恢复前在线备份，直接覆盖卷内文件前会保留 .pre-restore 副本"
else
  "${SCRIPT_DIR}/backup.sh" || die "恢复前备份失败，已中止（服务保持停止状态）"
fi

# ---------- 3. 恢复数据库 ----------
log "恢复数据库（${KIND}）..."
case "${MODE}:${KIND}" in
  docker:postgres)
    for _ in $(seq 1 30); do compose exec -T db pg_isready -q && break; sleep 2; done
    if [[ -n "${DATABASE_URL:-}" && "${DATABASE_URL}" != *"@db:"* && "${DATABASE_URL}" != *"@db/"* ]]; then
      export_pg_env "${DATABASE_URL}"
      pg_restore --clean --if-exists --no-owner --single-transaction -d "${PGDATABASE}" "${SRC}/db.pgdump"
    else
      compose exec -T db sh -c 'pg_restore --clean --if-exists --no-owner --role="${BAMY_DB_USER}" --single-transaction -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "${SRC}/db.pgdump"
    fi
    ;;
  docker:sqlite)
    path="${DATABASE_URL#*:///}"; path="${path%%\?*}"
    [[ "${path}" == /* ]] || path="/app/${path#./}"
    cid="$(compose ps -aq app)"
    [[ -n "${cid}" ]] || die "找不到 app 容器"
    docker cp "${SRC}/db.sqlite3" "${cid}:${path}.restore"
    compose run --rm --no-deps --entrypoint sh app -c "[ -f '${path}' ] && cp '${path}' '${path}.pre-restore'; mv '${path}.restore' '${path}'"
    ;;
  native:postgres)
    export_pg_env "${DATABASE_URL}"
    pg_restore --clean --if-exists --no-owner --single-transaction -d "${PGDATABASE}" "${SRC}/db.pgdump"
    ;;
  native:sqlite)
    dst="$(sqlite_path)"
    mkdir -p "$(dirname "${dst}")"
    rm -f "${dst}-wal" "${dst}-shm"
    cp "${SRC}/db.sqlite3" "${dst}.restore" && mv "${dst}.restore" "${dst}"
    ;;
esac

# ---------- 4-6. 配置、密钥、扩展 ----------
if [[ -d "${SRC}/config" ]]; then
  rm -rf "${ROOT_DIR}/config"; cp -a "${SRC}/config" "${ROOT_DIR}/config"; log "已恢复 config/"
fi
if [[ -f "${SRC}/env" ]]; then
  cp "${SRC}/env" "${ENV_FILE}"; chmod 600 "${ENV_FILE}"; log "已恢复环境文件（含密钥）：${ENV_FILE}"
fi
if [[ -d "${SRC}/extensions" ]]; then
  rm -rf "${ROOT_DIR}/extensions"; cp -a "${SRC}/extensions" "${ROOT_DIR}/extensions"; log "已恢复 extensions/"
fi

# ---------- 7-8. 启动与验证 ----------
"${SCRIPT_DIR}/start.sh"
"${SCRIPT_DIR}/healthcheck.sh"
log "恢复完成。请登录后台核对用户数、账号数、实例数、订单数与审计完整性（《部署与运行》12.3）"
