#!/usr/bin/env bash
# 安装"后台一键更新"的宿主机服务（只需装一次，需要 root）：
#   bamy-update.path     监听 data/update/request，后台点按钮就会触发
#   bamy-update.service  执行 scripts/update.sh（拉代码 → 备份 → 构建 → 重启，失败自动回滚）
#   bamy-update.timer    每 6 小时自动"检查"一次有没有新版本（只检查，不自动更新）
# 卸载：systemctl disable --now bamy-update.path bamy-update.timer; rm /etc/systemd/system/bamy-update.*
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
[[ $(id -u) -eq 0 ]] || die "需要 root 运行"
command -v systemctl >/dev/null || die "没有 systemd，不能装后台更新服务（可以手动跑 scripts/update.sh apply）"

DIR="${ROOT_DIR}/data/update"
mkdir -p "${DIR}"
# 容器里的应用用户 uid 10001 要能写 request、读状态：目录归 10001 组，组可写
chgrp 10001 "${DIR}" 2>/dev/null || true
chmod 2775 "${DIR}"

cat > /etc/systemd/system/bamy-update.service <<EOF
[Unit]
Description=BAMY 一键更新
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=${ROOT_DIR}
Environment=HOME=${HOME}
ExecStart=/usr/bin/env bash ${ROOT_DIR}/scripts/update.sh
TimeoutStartSec=1800
EOF

cat > /etc/systemd/system/bamy-update.path <<EOF
[Unit]
Description=监听 BAMY 后台的更新请求

[Path]
PathExists=${DIR}/request
Unit=bamy-update.service

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/bamy-update-check.service <<EOF
[Unit]
Description=BAMY 检查新版本

[Service]
Type=oneshot
WorkingDirectory=${ROOT_DIR}
Environment=HOME=${HOME}
ExecStart=/usr/bin/env bash ${ROOT_DIR}/scripts/update.sh check
EOF

cat > /etc/systemd/system/bamy-update.timer <<EOF
[Unit]
Description=每 6 小时检查 BAMY 新版本

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
Unit=bamy-update-check.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now bamy-update.path bamy-update.timer >/dev/null
bash "${ROOT_DIR}/scripts/update.sh" check || true
log "已安装：后台 → 超级管理 → 系统更新 可以用了（状态：$(systemctl is-active bamy-update.path)）"
