#!/usr/bin/env bash
# 浏览器端到端冒烟（官方 Playwright 镜像，无需本机安装浏览器）
#   tests/e2e/run.sh <base_url> <admin_user> <admin_pass> <instance_id> <account_id> <share_token>
# 前置：服务已启动（scripts/start.sh），且已有管理员、一个账号、一个实例与分享链接
#   （可用 tests/e2e/seed.py 准备：python tests/e2e/seed.py <base_url> <sqlite 路径>）
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMG=mcr.microsoft.com/playwright:v1.59.1-noble
docker run --rm --network host -v "${DIR}:/e2e" -w /e2e "${IMG}" bash -c \
  "[ -d node_modules/playwright ] || npm i --no-audit --no-fund --no-save playwright@1.59.1 >/dev/null 2>&1; \
   node ui_smoke.mjs $*; node ui_flows.mjs $1"
