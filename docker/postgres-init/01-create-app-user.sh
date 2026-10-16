#!/usr/bin/env bash
# PostgreSQL 首次初始化（仅在 db_data 卷为空时由官方镜像执行一次）
# 创建独立的应用账号并授予库权限：生产不使用数据库超级用户（《配置项》3.3）
set -euo pipefail

: "${BAMY_DB_USER:?BAMY_DB_USER 未设置}"
: "${BAMY_DB_PASSWORD:?BAMY_DB_PASSWORD 未设置，请在 docker/.env 中生成}"

# 用 psql 变量传参，避免把密码拼进 SQL 字符串
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_user="$BAMY_DB_USER" -v app_pass="$BAMY_DB_PASSWORD" -v db_name="$POSTGRES_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE', :'app_user', :'app_pass')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('GRANT CONNECT, TEMP ON DATABASE %I TO %I', :'db_name', :'app_user') \gexec
SELECT format('GRANT USAGE, CREATE ON SCHEMA public TO %I', :'app_user') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'app_user') \gexec
SQL
