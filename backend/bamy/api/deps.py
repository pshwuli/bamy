"""接口依赖：鉴权、角色、数据隔离、分页、请求上下文。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.core.crypto import hmac_token
from bamy.core.errors import ApiError
from bamy.core.security import decode_token
from bamy.db.models import Account, ApiToken, Instance, User, UserSession
from bamy.db.session import get_session

ROLE_RANK = {"user": 1, "admin": 2, "super_admin": 3}


@dataclass
class Ctx:
    user: User
    request: Request
    db: AsyncSession
    via_api_token: str | None = None   # read / write / admin

    @property
    def ip(self) -> str | None:
        return self.request.client.host if self.request.client else None

    @property
    def ua(self) -> str | None:
        return self.request.headers.get("user-agent")

    @property
    def rid(self) -> str | None:
        return getattr(self.request.state, "request_id", None)

    @property
    def is_admin(self) -> bool:
        return ROLE_RANK.get(self.user.role, 0) >= 2

    def audit_kw(self) -> dict[str, Any]:
        return {"actor_id": self.user.id, "actor_role": self.user.role, "ip": self.ip, "user_agent": self.ua,
                "request_id": self.rid, "source": "api" if self.via_api_token else ("admin" if self.is_admin and
                                                                                    self.request.url.path.startswith(("/api/admin", "/api/super")) else "manual")}


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def current(request: Request, db: AsyncSession = Depends(get_session)) -> Ctx:
    token = _bearer(request)
    if not token:
        raise ApiError(40101)
    if token.startswith("bamy_"):
        row = (await db.execute(select(ApiToken).where(ApiToken.token_hash == hmac_token(token)))).scalar_one_or_none()
        if row is None or row.revoked_at is not None or (row.expires_at and row.expires_at < datetime.utcnow()):
            raise ApiError(40102)
        user = await db.get(User, row.user_id)
        row.last_used_at = datetime.utcnow()
        row.use_count = (row.use_count or 0) + 1
        await db.commit()
        scope = row.scope
        if request.method not in {"GET", "HEAD", "OPTIONS"} and scope == "read":
            raise ApiError(40301, "API Token 只读")
    else:
        payload = decode_token(token)
        user = await db.get(User, int(payload["sub"]))
        scope = None
        sid = payload.get("sid")
        if sid is not None:
            sess = await db.get(UserSession, int(sid))
            if sess is None or sess.revoked_at is not None or sess.user_id != int(payload["sub"]) \
                    or sess.expires_at < datetime.utcnow():
                raise ApiError(40102, "登录已失效，请重新登录")
            request.state.session_id = int(sid)
    if user is None:
        raise ApiError(40102)
    if user.status == "disabled":
        raise ApiError(40106)
    if user.status == "locked" and user.locked_until and user.locked_until > datetime.utcnow():
        raise ApiError(40105)
    if user.status == "pending":
        raise ApiError(40110)
    request.state.user_id = user.id
    return Ctx(user=user, request=request, db=db, via_api_token=scope)


async def admin(ctx: Ctx = Depends(current)) -> Ctx:
    if ROLE_RANK.get(ctx.user.role, 0) < 2:
        raise ApiError(40302)
    if ctx.via_api_token and ctx.via_api_token != "admin":
        raise ApiError(40302, "API Token 无管理员权限")
    return ctx


async def super_admin(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.user.role != "super_admin":
        raise ApiError(40302)
    if ctx.via_api_token and ctx.via_api_token != "admin":
        raise ApiError(40302, "API Token 无管理员权限")
    return ctx


@dataclass
class Page:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def paging(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200)) -> Page:
    return Page(page, page_size)


async def own_account(db: AsyncSession, user: User, account_id: int, *, admin_ok: bool = False) -> Account:
    """越权访问一律以 404 呈现（40303 伪装）。"""
    acc = await db.get(Account, account_id)
    if acc is None or acc.deleted_at is not None or (acc.owner_id != user.id and not admin_ok):
        raise ApiError(40303 if acc is not None and acc.deleted_at is None else 40404)
    return acc


async def own_instance(db: AsyncSession, user: User, instance_id: int, *, admin_ok: bool = False) -> Instance:
    inst = await db.get(Instance, instance_id)
    if inst is None or inst.deleted_at is not None or (inst.owner_id != user.id and not admin_ok):
        raise ApiError(40303 if inst is not None and inst.deleted_at is None else 40403)
    return inst


def jload(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def num(v: Any) -> float | None:
    return float(v) if v is not None else None
