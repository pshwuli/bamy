"""系统更新（超级管理员）：读更新状态、请求"检查"或"更新"。

应用自己不跑 git、不重启自己：只往共享目录写一个请求文件（内容只有 check/apply），
宿主机上的 systemd（scripts/install-updater.sh 装的）看到后执行 scripts/update.sh。
这样容器里不需要 Docker 权限，也拿不到宿主机 shell。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from bamy import __version__
from bamy.api.deps import Ctx, super_admin
from bamy.core.config import ROOT_DIR
from bamy.core.errors import ApiError
from bamy.core.response import ok
from bamy.services.audit import write_audit

router = APIRouter()


def _dir() -> Path:
    env = os.environ.get("UPDATE_DIR")
    if env:
        return Path(env)
    for p in (Path("/app/update"), ROOT_DIR / "data" / "update"):
        if p.is_dir():
            return p
    return ROOT_DIR / "data" / "update"


def _status() -> dict:
    d = _dir()
    try:
        st = json.loads((d / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {"state": "unknown", "message": "还没检查过", "updater": False}
    pending = (d / "request").exists()
    # 宿主机更新服务是否在：有它写过的状态文件就算装了；请求挂了 60 秒没人处理说明服务没跑
    req_age = time.time() - (d / "request").stat().st_mtime if pending else 0
    st["installed"] = bool(st.get("updater"))
    st["stuck"] = pending and req_age > 60
    st["requested"] = pending
    st["version"] = __version__
    return st


def _tail(n: int = 120) -> list[str]:
    try:
        lines = (_dir() / "update.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-n:]


@router.get("/api/super/update")
async def update_status(ctx: Ctx = Depends(super_admin)):
    return ok(_status() | {"log": _tail()})


class UpdateIn(BaseModel):
    action: str


@router.post("/api/super/update")
async def request_update(body: UpdateIn, ctx: Ctx = Depends(super_admin)):
    if body.action not in {"check", "apply"}:
        raise ApiError(40003, "action 只能是 check 或 apply")
    st = _status()
    if st.get("state") in {"running", "checking"} and not st.get("stuck"):
        raise ApiError(40902, "已有更新任务在进行")
    d = _dir()
    if not d.is_dir():
        raise ApiError(42232, "服务器还没装后台更新服务：在服务器上用 root 运行 scripts/install-updater.sh")
    try:
        (d / "request").write_text(body.action, encoding="utf-8")
    except OSError as exc:
        raise ApiError(42232, f"写更新请求失败（{exc.strerror}）：在服务器上用 root 运行 scripts/install-updater.sh") from exc
    await write_audit(ctx.db, action=f"system.update_{body.action}", target_type="system", **ctx.audit_kw())
    return ok(_status())
