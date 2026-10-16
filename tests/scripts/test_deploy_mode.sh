#!/usr/bin/env bash
# 部署模式探测与 update→backup 传递：四种场景（独立目录、假 docker，不碰真实服务）
# 用法：bash tests/scripts/test_deploy_mode.sh
set -u
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
run_case() {   # $1 名称  $2 是否有 docker/.env  $3 假 docker 是否有 app 容器  $4 是否有活着的 native PID
  local T; T=$(mktemp -d)
  mkdir -p "$T/repo/scripts" "$T/repo/docker" "$T/repo/data/run" "$T/bin"
  cp "$SRC/scripts/lib.sh" "$T/repo/scripts/"
  [[ "$2" == yes ]] && echo "DATABASE_URL=sqlite+aiosqlite:///./data/bamy.db" > "$T/repo/docker/.env"
  # 假 docker：ps 时按场景输出容器 ID，其他子命令报"db 没在跑"
  cat > "$T/bin/docker" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == ps ]]; then [[ "$3" == yes ]] && echo abc123; exit 0; fi
echo 'service "db" is not running' >&2; exit 1
EOF
  chmod +x "$T/bin/docker"
  if [[ "$4" == yes ]]; then sleep 30 & echo $! > "$T/repo/data/run/bamy.pid"; fi
  # 子脚本：打印它看到的模式（模拟 backup.sh 继承）
  cat > "$T/repo/scripts/child.sh" <<'EOF'
#!/usr/bin/env bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"; echo "$MODE"
EOF
  cat > "$T/repo/scripts/parent.sh" <<'EOF'
#!/usr/bin/env bash
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
export BAMY_MODE="${MODE}"
echo "$MODE→$(bash "$(dirname "$0")/child.sh")"
EOF
  local got; got=$(cd "$T" && PATH="$T/bin:$PATH" env -u BAMY_MODE bash repo/scripts/parent.sh)
  printf '%-44s %s（期望 %s）\n' "$1" "$got" "$5"
  [[ "$got" == "$5" ]] || FAIL=1
  [[ "$4" == yes ]] && kill "$(cat "$T/repo/data/run/bamy.pid")" 2>/dev/null
  rm -rf "$T"
}
FAIL=0
run_case "native 在跑 + 留着 docker/.env"           yes no yes native→native
run_case "docker 在跑"                              yes yes no docker→docker
run_case "都没在跑 + 有 docker/.env（首次 docker）"  yes no no docker→docker
run_case "都没在跑 + 没有 docker/.env（首次 native）" no no no native→native
[[ $FAIL == 0 ]] && echo PASS || { echo FAIL; exit 1; }
