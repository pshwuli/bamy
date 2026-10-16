#!/usr/bin/env bash
# 一键更新：拉取 GitHub 上当前分支的新代码 → 备份 → 构建前端 → 重启服务。失败自动回滚到更新前的版本。
#   scripts/update.sh check    只检查有没有新版本
#   scripts/update.sh apply    检查并更新
# 管理后台"系统更新"页的按钮会在 data/update/request 里写 check/apply，
# 由宿主机上的 systemd（scripts/install-updater.sh 安装）调用本脚本；也可以手动在服务器上跑。
# 状态写 data/update/status.json，过程写 data/update/update.log，后台页面直接读这两个文件。
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

# 当前部署模式（lib.sh 按实际在跑的服务判断）固定下来，子脚本 backup.sh / start.sh 都继承同一个，不各自再猜
export BAMY_MODE="${MODE}"

DIR="${UPDATE_DIR:-${ROOT_DIR}/data/update}"
mkdir -p "${DIR}"
LOG="${DIR}/update.log"
ACTION="${1:-}"
if [[ -z "${ACTION}" && -f "${DIR}/request" ]]; then
  ACTION="$(head -c 16 "${DIR}/request" | tr -cd 'a-z')"   # 只取字母，内容只当作开关，绝不执行
  rm -f "${DIR}/request"
fi
[[ "${ACTION}" == check || "${ACTION}" == apply ]] || { echo "用法：$0 check|apply"; exit 2; }

exec 9>"${DIR}/.lock"
flock -n 9 || { echo "已有更新在进行"; exit 0; }

cd "${ROOT_DIR}"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
OLD="$(git rev-parse HEAD)"

status() {   # status <state> <message>：写状态 JSON（用 python3 做转义，避免手拼 JSON）
  python3 - "$1" "$2" "${DIR}/status.json" "${BRANCH}" <<'PY'
import json, subprocess, sys, time
state, msg, path, branch = sys.argv[1:5]
def git(*a):
    try:
        return subprocess.run(["git", *a], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return ""
head = git("log", "-1", "--format=%h|%s|%cI")
h, s, d = (head.split("|", 2) + ["", "", ""])[:3]
remote = f"origin/{branch}"
behind = git("rev-list", "--count", f"HEAD..{remote}") or "0"
pending = [x for x in git("log", "--format=%h %s", f"HEAD..{remote}").splitlines() if x][:30]
old = {}
try:
    old = json.load(open(path))
except Exception:
    pass
out = {"state": state, "message": msg, "branch": branch, "current": h, "current_subject": s, "current_date": d,
       "remote": git("rev-parse", "--short", remote), "behind": int(behind or 0), "pending": pending,
       "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "updater": True,
       "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") if state in {"idle", "available", "latest"} else old.get("checked_at")}
tmp = path + ".tmp"
json.dump(out, open(tmp, "w"), ensure_ascii=False)
import os; os.replace(tmp, path)
PY
}

step() { log "$*" | tee -a "${LOG}"; status running "$*"; }
fail() {
  log "失败：$*" | tee -a "${LOG}"
  status failed "$*"
  exit 1
}

if [[ "${ACTION}" == apply ]]; then : > "${LOG}"; fi
log "检查更新（分支 ${BRANCH}，部署模式 ${MODE}）" | tee -a "${LOG}"
status checking "正在检查更新"
if ! git fetch --quiet origin "${BRANCH}" >>"${LOG}" 2>&1; then
  if git ls-remote --exit-code origin >/dev/null 2>&1; then
    fail "GitHub 上没有分支 ${BRANCH}（服务器当前在这个分支上，先把它推上去或切到 main）"
  fi
  fail "连不上 GitHub（git fetch 失败），看日志"
fi
BEHIND="$(git rev-list --count "HEAD..origin/${BRANCH}")"

if [[ "${ACTION}" == check ]]; then
  if (( BEHIND > 0 )); then status available "有 ${BEHIND} 个新提交可以更新"; else status latest "已是最新版本"; fi
  exit 0
fi

(( BEHIND > 0 )) || { status latest "已是最新版本，不需要更新"; log "已是最新" | tee -a "${LOG}"; exit 0; }
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  fail "服务器上的代码有未提交的改动，为了不覆盖它们已停止更新（在服务器上 git status 查看）"
fi

step "备份数据库与配置（更新前）"
"${SCRIPT_DIR}/backup.sh" >>"${LOG}" 2>&1 || fail "备份失败，已停止更新（数据没有任何改动）"

step "拉取新代码（${BEHIND} 个提交）"
LOCK_BEFORE="$(git rev-parse HEAD:frontend/package-lock.json 2>/dev/null)"
git merge --ff-only --quiet "origin/${BRANCH}" >>"${LOG}" 2>&1 || fail "无法快进合并（服务器分支和 GitHub 分叉了），已停止"

rollback() {
  log "回滚到更新前的版本 ${OLD:0:7}" | tee -a "${LOG}"
  git reset --quiet --hard "${OLD}" >>"${LOG}" 2>&1
  (cd frontend && npm run build >>"${LOG}" 2>&1)
  "${SCRIPT_DIR}/start.sh" >>"${LOG}" 2>&1
}

if [[ "$(git rev-parse HEAD:frontend/package-lock.json 2>/dev/null)" != "${LOCK_BEFORE}" ]]; then
  step "前端依赖有变化，安装依赖"
  (cd frontend && npm ci --no-audit --no-fund >>"${LOG}" 2>&1) || { rollback; fail "前端依赖安装失败，已回滚到 ${OLD:0:7}"; }
fi

step "构建前端（几分钟）"
(cd frontend && npm run build >>"${LOG}" 2>&1) || { rollback; fail "前端构建失败，已回滚到 ${OLD:0:7}"; }

step "重启服务（期间页面会短暂断开）"
"${SCRIPT_DIR}/start.sh" >>"${LOG}" 2>&1 || { rollback; fail "新版本启动失败，已回滚到 ${OLD:0:7}"; }

NEW="$(git rev-parse --short HEAD)"
log "更新完成：${OLD:0:7} → ${NEW}" | tee -a "${LOG}"
status success "更新完成：${OLD:0:7} → ${NEW}"
