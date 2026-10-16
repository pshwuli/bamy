"""认证与用户接口（《接口契约》第三、四节，22.3、22.4）。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bamy.api.deps import Ctx, Page, current, iso, paging
from bamy.api.serializers import user_out
from bamy.core.config import settings
from bamy.core.crypto import decrypt, encrypt, hmac_token, new_token
from bamy.core.errors import ApiError
from bamy.core.response import ok, paginated
from bamy.core.security import (
    create_access_token, create_refresh_token, decode_token, gen_recovery_codes, hash_password, in_seconds,
    new_totp_secret, totp_uri, verify_password, verify_totp, verify_totp_once,
)
from bamy.db.models import ApiToken, InviteCode, InviteUsage, RefreshToken, User, UserPreference, UserSession
from bamy.db.session import get_session
from bamy.services import system_config
from bamy.services.audit import audit_dict, query_audit, write_audit

router = APIRouter()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
MAX_FAILED = 5
LOCK_MINUTES = 15
RESET_TTL = 1800
_reset_tokens: dict[str, tuple[int, datetime]] = {}


def _ip(request: Request) -> str | None:
    # 不读 X-Forwarded-For：客户端可伪造。反代后的真实 IP 由 uvicorn --proxy-headers（只信任 FORWARDED_ALLOW_IPS）写入 client
    return request.client.host if request.client else None


def _check_password(pw: str) -> None:
    if len(pw) < settings.password_min_length or len(pw) > 128:
        raise ApiError(40003, f"密码长度需在 {settings.password_min_length}-128 位")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise ApiError(40003, "密码需同时包含字母和数字")


async def _issue(db: AsyncSession, user: User, request: Request) -> dict:
    """签发一对 token。access 里带会话 id（sid）：会话被撤销后，access 也立即失效，不必等 30 分钟过期。"""
    refresh = create_refresh_token(user.id)
    ua = request.headers.get("user-agent")
    db.add(RefreshToken(user_id=user.id, token_hash=hmac_token(refresh), device_info=(ua or "")[:255],
                        ip=_ip(request), expires_at=in_seconds(settings.refresh_token_expire)))
    sess = UserSession(user_id=user.id, session_token=hmac_token(refresh)[:128], ip=_ip(request), user_agent=ua,
                       expires_at=in_seconds(settings.refresh_token_expire))
    db.add(sess)
    await db.flush()
    access = create_access_token(user.id, user.role, {"sid": sess.id})
    await db.commit()
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer",
            "expires_in": settings.access_token_expire,
            "user": {"id": user.id, "username": user.username, "role": user.role}}


# ---------------------------------------------------------------- 3.1 初始化
class SetupIn(BaseModel):
    username: str
    password: str


@router.get("/api/setup/status")
async def setup_status(db: AsyncSession = Depends(get_session)):
    count = await db.scalar(select(func.count(User.id))) or 0
    return ok({"initialized": count > 0, "allow_register": bool(system_config.get("register.enabled", True)),
               "invite_only": bool(system_config.get("register.invite_only", False)) or settings.allow_invite_register,
               "allow_email_register": settings.allow_email_register})


@router.post("/api/setup")
async def setup(body: SetupIn, request: Request, db: AsyncSession = Depends(get_session)):
    if (await db.scalar(select(func.count(User.id))) or 0) > 0:
        raise ApiError(40301, "系统已初始化")
    if not USERNAME_RE.match(body.username):
        raise ApiError(40003, "用户名需为 3-32 位字母、数字或下划线")
    _check_password(body.password)
    user = User(username=body.username, password_hash=hash_password(body.password), role="super_admin",
                status="active")
    db.add(user)
    await db.commit()
    await write_audit(db, action="user.register", actor_id=user.id, actor_role=user.role, target_type="user",
                      target_id=user.id, new_value={"setup": True}, ip=_ip(request), source="system")
    return ok({"user_id": user.id, "username": user.username, "role": user.role}, status_code=201)


# ---------------------------------------------------------------- 3.2 注册
class RegisterIn(BaseModel):
    username: str
    password: str
    email: str | None = None
    invite_code: str | None = None


@router.post("/api/auth/register")
async def register(body: RegisterIn, request: Request, db: AsyncSession = Depends(get_session)):
    if not settings.allow_register or not system_config.get("register.enabled", True):
        raise ApiError(40301, "未开放注册")
    if not USERNAME_RE.match(body.username):
        raise ApiError(40003, "用户名需为 3-32 位字母、数字或下划线")
    _check_password(body.password)
    if body.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email):
        raise ApiError(40003, "邮箱格式不正确")
    if settings.allow_email_register and not body.email:
        raise ApiError(40004, "邮箱为必填")
    if await db.scalar(select(User.id).where(User.username == body.username)):
        raise ApiError(40901, "用户名已存在")
    if body.email and await db.scalar(select(User.id).where(User.email == body.email)):
        raise ApiError(40901, "邮箱已被使用")
    invite_only = settings.allow_invite_register or bool(system_config.get("register.invite_only", False))
    invite = None
    if invite_only or body.invite_code:
        invite = (await db.execute(select(InviteCode).where(InviteCode.code == (body.invite_code or "")))).scalar_one_or_none()
        if (invite is None or not invite.enabled or invite.used_count >= invite.max_uses
                or (invite.expires_at and invite.expires_at < datetime.utcnow())):
            raise ApiError(40109)
    role = invite.role if invite and invite.role in {"user", "admin"} else "user"
    status = "pending" if settings.allow_email_register and body.email else "active"
    user = User(username=body.username, email=body.email or None, password_hash=hash_password(body.password),
                role=role, status=status)
    db.add(user)
    await db.flush()
    if invite is not None:
        invite.used_count += 1
        db.add(InviteUsage(invite_id=invite.id, user_id=user.id, username=user.username))
    await db.commit()
    await write_audit(db, action="user.register", actor_id=user.id, actor_role=role, target_type="user",
                      target_id=user.id, ip=_ip(request), source="manual",
                      new_value={"invite_id": invite.id if invite else None})
    return ok({"user_id": user.id, "username": user.username, "status": user.status}, status_code=201)


# ---------------------------------------------------------------- 3.3 登录
class LoginIn(BaseModel):
    username: str
    password: str
    two_factor_code: str | None = None


@router.post("/api/auth/login")
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_session)):
    user = (await db.execute(select(User).where((User.username == body.username) | (User.email == body.username)))).scalar_one_or_none()
    now = datetime.utcnow()
    if user is None:
        raise ApiError(40104)
    if user.status == "locked":
        if user.locked_until and user.locked_until > now:
            left = int((user.locked_until - now).total_seconds())
            raise ApiError(40105, f"账号已锁定，请 {left // 60 + 1} 分钟后再试", data={"retry_after": left})
        user.status, user.failed_login_count, user.locked_until = "active", 0, None
    if not verify_password(body.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_FAILED and user.status == "active":
            user.status = "locked"
            user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
        await db.commit()
        await write_audit(db, action="user.login", actor_id=user.id, target_type="user", target_id=user.id,
                          result="failed", fail_reason="密码错误", ip=_ip(request))
        if user.status == "locked":
            raise ApiError(40105, "连续登录失败，账号已锁定 15 分钟")
        raise ApiError(40104)
    if user.status == "disabled":
        raise ApiError(40106)
    if user.status == "pending":
        raise ApiError(40110)
    if user.two_factor_enabled:
        code = (body.two_factor_code or "").strip()
        if not code:
            raise ApiError(40108)
        if not verify_totp_once(decrypt(user.two_factor_secret or ""), code, f"u{user.id}"):
            codes = json.loads(decrypt(user.recovery_codes or "") or "[]")
            if code in codes:
                codes.remove(code)
                user.recovery_codes = encrypt(json.dumps(codes))
            else:
                user.failed_login_count = (user.failed_login_count or 0) + 1
                if user.failed_login_count >= MAX_FAILED:
                    user.status = "locked"
                    user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                await db.commit()
                await write_audit(db, action="user.login", actor_id=user.id, target_type="user", target_id=user.id,
                                  result="failed", fail_reason="二次验证失败", ip=_ip(request))
                if user.status == "locked":
                    raise ApiError(40105, "连续验证失败，账号已锁定 15 分钟")
                raise ApiError(40107)
    user.failed_login_count = 0
    user.last_login_at = now
    user.last_login_ip = _ip(request)
    data = await _issue(db, user, request)
    await write_audit(db, action="user.login", actor_id=user.id, actor_role=user.role, target_type="user",
                      target_id=user.id, ip=_ip(request), user_agent=request.headers.get("user-agent"))
    return ok(data)


# ---------------------------------------------------------------- 3.4 刷新 / 3.5 登出
class RefreshIn(BaseModel):
    refresh_token: str


@router.post("/api/auth/refresh")
async def refresh(body: RefreshIn, request: Request, db: AsyncSession = Depends(get_session)):
    payload = decode_token(body.refresh_token, expected="refresh")
    row = (await db.execute(select(RefreshToken).where(RefreshToken.token_hash == hmac_token(body.refresh_token)))).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at < datetime.utcnow():
        raise ApiError(40102)
    user = await db.get(User, int(payload["sub"]))
    if user is None or user.status not in {"active"}:
        raise ApiError(40106)
    row.revoked_at = datetime.utcnow()  # 轮换
    await db.execute(update(UserSession).where(UserSession.session_token == row.token_hash[:128])
                     .values(revoked_at=datetime.utcnow()))
    data = await _issue(db, user, request)
    return ok({"access_token": data["access_token"], "refresh_token": data["refresh_token"],
               "expires_in": data["expires_in"]})


class LogoutIn(BaseModel):
    refresh_token: str | None = None
    all_devices: bool = False


@router.post("/api/auth/logout")
async def logout(body: LogoutIn, ctx: Ctx = Depends(current)):
    now = datetime.utcnow()
    if body.all_devices:
        await ctx.db.execute(update(RefreshToken).where(RefreshToken.user_id == ctx.user.id,
                                                        RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
        await ctx.db.execute(update(UserSession).where(UserSession.user_id == ctx.user.id,
                                                       UserSession.revoked_at.is_(None)).values(revoked_at=now))
    elif body.refresh_token:
        h = hmac_token(body.refresh_token)
        await ctx.db.execute(update(RefreshToken).where(RefreshToken.token_hash == h).values(revoked_at=now))
        await ctx.db.execute(update(UserSession).where(UserSession.session_token == h[:128]).values(revoked_at=now))
    else:
        # 只撤当前设备：access 里的 sid 指向本会话；旧版 access 没有 sid 时什么都不撤（而不是误撤别的设备）
        sid = getattr(ctx.request.state, "session_id", None)
        if sid is not None:
            sess = await ctx.db.get(UserSession, int(sid))
            if sess is not None and sess.user_id == ctx.user.id:
                sess.revoked_at = now
                await ctx.db.execute(update(RefreshToken).where(
                    RefreshToken.user_id == ctx.user.id, RefreshToken.revoked_at.is_(None),
                    func.substr(RefreshToken.token_hash, 1, 128) == sess.session_token).values(revoked_at=now))
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.logout", target_type="user", target_id=ctx.user.id, **ctx.audit_kw(),
                      new_value={"all_devices": body.all_devices})
    return ok(None, message="登出成功")


# ---------------------------------------------------------------- 3.6–3.9
class ChangePwIn(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/auth/change-password")
async def change_password(body: ChangePwIn, ctx: Ctx = Depends(current)):
    if not verify_password(body.old_password, ctx.user.password_hash):
        raise ApiError(40104, "旧密码错误")
    _check_password(body.new_password)
    ctx.user.password_hash = hash_password(body.new_password)
    await ctx.db.execute(update(RefreshToken).where(RefreshToken.user_id == ctx.user.id,
                                                    RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.utcnow()))
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.password_change", target_type="user", target_id=ctx.user.id, **ctx.audit_kw())
    return ok(None)


class ForgotIn(BaseModel):
    email: str


@router.post("/api/auth/forgot-password")
async def forgot(body: ForgotIn, db: AsyncSession = Depends(get_session)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is not None:
        token = new_token(24)
        _reset_tokens[hmac_token(token)] = (user.id, datetime.utcnow() + timedelta(seconds=RESET_TTL))
        # 重置链接只能经邮件渠道送达用户本人，绝不写日志/审计（否则管理员可借此接管任意账号）。
        # 未配置邮件渠道时：只记录"有人申请重置"，由管理员在后台为该用户生成临时密码。
        from bamy.services.audit import write_system_log
        from bamy.services.mailer import send_reset_mail

        delivered = await send_reset_mail(user.email, token)
        await write_system_log(db, "info", "收到密码重置申请" + ("，已发送邮件" if delivered else "，邮件渠道未配置"),
                               module="auth", extra={"user_id": user.id})
    return ok({"sent": True})  # 不暴露邮箱是否存在


class ResetIn(BaseModel):
    token: str
    new_password: str


@router.post("/api/auth/reset-password")
async def reset_password(body: ResetIn, request: Request, db: AsyncSession = Depends(get_session)):
    entry = _reset_tokens.pop(hmac_token(body.token), None)
    if entry is None or entry[1] < datetime.utcnow():
        raise ApiError(40102, "重置链接无效或已过期")
    _check_password(body.new_password)
    user = await db.get(User, entry[0])
    user.password_hash = hash_password(body.new_password)
    user.status = "active" if user.status == "locked" else user.status
    user.failed_login_count, user.locked_until = 0, None
    await db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked_at=datetime.utcnow()))
    await db.commit()
    await write_audit(db, action="user.reset_password", actor_id=user.id, target_type="user", target_id=user.id,
                      ip=_ip(request))
    return ok(None)


@router.get("/api/auth/me")
async def me(ctx: Ctx = Depends(current)):
    return ok(user_out(ctx.user))


# ---------------------------------------------------------------- 3.10 2FA
class CodeIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=16)


@router.post("/api/auth/2fa/enable")
async def tfa_enable(ctx: Ctx = Depends(current)):
    if ctx.user.two_factor_enabled:
        raise ApiError(40902, "已开启两步验证")
    secret = new_totp_secret()
    ctx.user.two_factor_secret = encrypt(secret)
    await ctx.db.commit()
    return ok({"secret": secret, "otpauth_uri": totp_uri(secret, ctx.user.username)})


@router.post("/api/auth/2fa/verify")
async def tfa_verify(body: CodeIn, ctx: Ctx = Depends(current)):
    secret = decrypt(ctx.user.two_factor_secret or "")
    if not secret or not verify_totp(secret, body.code):
        raise ApiError(40107)
    codes = gen_recovery_codes()
    ctx.user.two_factor_enabled = True
    ctx.user.recovery_codes = encrypt(json.dumps(codes))
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.2fa_enable", target_type="user", target_id=ctx.user.id, **ctx.audit_kw())
    return ok({"enabled": True, "recovery_codes": codes})


@router.post("/api/auth/2fa/disable")
async def tfa_disable(body: CodeIn, ctx: Ctx = Depends(current)):
    if not ctx.user.two_factor_enabled:
        return ok({"enabled": False})
    if not verify_totp(decrypt(ctx.user.two_factor_secret or ""), body.code):
        raise ApiError(40107)
    ctx.user.two_factor_enabled = False
    ctx.user.two_factor_secret = None
    ctx.user.recovery_codes = None
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.2fa_disable", target_type="user", target_id=ctx.user.id, **ctx.audit_kw())
    return ok({"enabled": False})


@router.post("/api/auth/2fa/recovery-codes")
async def tfa_codes(body: CodeIn, ctx: Ctx = Depends(current)):
    if not ctx.user.two_factor_enabled or not verify_totp(decrypt(ctx.user.two_factor_secret or ""), body.code):
        raise ApiError(40107)
    codes = gen_recovery_codes()
    ctx.user.recovery_codes = encrypt(json.dumps(codes))
    await ctx.db.commit()
    return ok({"recovery_codes": codes})


# ---------------------------------------------------------------- 第四节 用户
@router.get("/api/user/profile")
async def profile(ctx: Ctx = Depends(current)):
    return ok(user_out(ctx.user))


class ProfileIn(BaseModel):
    nickname: str | None = Field(None, max_length=64)
    email: str | None = None


@router.patch("/api/user/profile")
async def update_profile(body: ProfileIn, ctx: Ctx = Depends(current)):
    old = user_out(ctx.user)
    if body.email is not None:
        if body.email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", body.email):
            raise ApiError(40003, "邮箱格式不正确")
        exists = await ctx.db.scalar(select(User.id).where(User.email == body.email, User.id != ctx.user.id))
        if body.email and exists:
            raise ApiError(40901, "邮箱已被使用")
        ctx.user.email = body.email or None
    if body.nickname is not None:
        ctx.user.nickname = body.nickname
    await ctx.db.commit()
    await write_audit(ctx.db, action="user.update", target_type="user", target_id=ctx.user.id, old_value=old,
                      new_value=body.model_dump(exclude_none=True), **ctx.audit_kw())
    return ok(user_out(ctx.user))


@router.get("/api/user/audit-logs")
async def my_audit(request: Request, ctx: Ctx = Depends(current), pg: Page = Depends(paging)):
    q = dict(request.query_params)
    rows, total = await query_audit(ctx.db, q, pg.page, pg.page_size, owner_ids=[ctx.user.id])
    return paginated([audit_dict(r) for r in rows], total, pg.page, pg.page_size)


@router.get("/api/user/api-tokens")
async def list_tokens(ctx: Ctx = Depends(current)):
    rows = (await ctx.db.execute(select(ApiToken).where(ApiToken.user_id == ctx.user.id)
                                 .order_by(ApiToken.id.desc()))).scalars().all()
    return ok({"items": [{"id": r.id, "name": r.name, "token_prefix": r.token_prefix, "scope": r.scope,
                          "last_used_at": iso(r.last_used_at), "use_count": r.use_count,
                          "created_at": iso(r.created_at), "expires_at": iso(r.expires_at),
                          "revoked_at": iso(r.revoked_at)} for r in rows]})


class TokenIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    scope: str = "read"
    expires_at: datetime | None = None


@router.post("/api/user/api-tokens")
async def create_token(body: TokenIn, ctx: Ctx = Depends(current)):
    if body.expires_at is not None and body.expires_at.replace(tzinfo=None) <= datetime.utcnow():
        raise ApiError(40003, "过期时间必须晚于现在", errors=[{"field": "expires_at", "reason": "必须是将来的时间"}])
    if body.scope not in {"read", "write", "admin"}:
        raise ApiError(40003, "scope 必须是 read / write / admin")
    if body.scope == "admin" and not ctx.is_admin:
        raise ApiError(40302)
    token = "bamy_" + new_token(30)
    row = ApiToken(user_id=ctx.user.id, name=body.name, token_hash=hmac_token(token), token_prefix=token[:12],
                   scope=body.scope, expires_at=body.expires_at.replace(tzinfo=None) if body.expires_at else None)
    ctx.db.add(row)
    await ctx.db.commit()
    await write_audit(ctx.db, action="api_token.create", target_type="api_token", target_id=row.id,
                      new_value={"name": body.name, "scope": body.scope}, **ctx.audit_kw())
    return ok({"id": row.id, "name": row.name, "token": token, "scope": row.scope,
               "expires_at": iso(row.expires_at)}, status_code=201)


@router.delete("/api/user/api-tokens/{token_id}")
async def revoke_token(token_id: int, ctx: Ctx = Depends(current)):
    row = await ctx.db.get(ApiToken, token_id)
    if row is None or row.user_id != ctx.user.id:
        raise ApiError(40401)
    row.revoked_at = datetime.utcnow()
    await ctx.db.commit()
    await write_audit(ctx.db, action="api_token.revoke", target_type="api_token", target_id=token_id, **ctx.audit_kw())
    return ok(None)


@router.get("/api/user/sessions")
async def sessions(ctx: Ctx = Depends(current)):
    rows = (await ctx.db.execute(select(UserSession).where(UserSession.user_id == ctx.user.id,
                                                           UserSession.revoked_at.is_(None),
                                                           UserSession.expires_at > datetime.utcnow())
                                 .order_by(UserSession.id.desc()))).scalars().all()
    return ok({"items": [{"id": r.id, "ip": r.ip, "user_agent": r.user_agent, "created_at": iso(r.created_at),
                          "expires_at": iso(r.expires_at)} for r in rows]})


@router.delete("/api/user/sessions/{session_id}")
async def revoke_session(session_id: int, ctx: Ctx = Depends(current)):
    row = await ctx.db.get(UserSession, session_id)
    if row is None or row.user_id != ctx.user.id:
        raise ApiError(40401)
    row.revoked_at = datetime.utcnow()
    await ctx.db.execute(update(RefreshToken).where(RefreshToken.token_hash.like(row.session_token + "%"))
                         .values(revoked_at=datetime.utcnow()))
    await ctx.db.commit()
    await write_audit(ctx.db, action="session.revoke", target_type="session", target_id=session_id, **ctx.audit_kw())
    return ok(None)


# ---------------------------------------------------------------- 22.3 / 22.4
async def _prefs(ctx: Ctx) -> UserPreference:
    row = (await ctx.db.execute(select(UserPreference).where(UserPreference.user_id == ctx.user.id))).scalar_one_or_none()
    if row is None:
        row = UserPreference(user_id=ctx.user.id, preferences="{}", notification_settings="{}")
        ctx.db.add(row)
        await ctx.db.commit()
    return row


NOTIFY_DEFAULT = {"email_enabled": False, "webhook_url": "", "event_types": ["instance.error", "system.alert"]}
PREF_DEFAULT = {"theme": "dark", "language": "zh-CN", "time_format": "24h", "timezone": "Asia/Shanghai", "page_size": 20}


@router.get("/api/user/notification-settings")
async def get_notify(ctx: Ctx = Depends(current)):
    row = await _prefs(ctx)
    return ok(NOTIFY_DEFAULT | json.loads(row.notification_settings or "{}"))


class NotifyIn(BaseModel):
    email_enabled: bool | None = None
    webhook_url: str | None = None
    event_types: list[str] | None = None


@router.patch("/api/user/notification-settings")
async def set_notify(body: NotifyIn, ctx: Ctx = Depends(current)):
    if body.webhook_url:
        if not body.webhook_url.startswith(("https://", "http://")) or len(body.webhook_url) > 500:
            raise ApiError(40003, "Webhook 地址必须是 http(s) URL")
        from bamy.plugin import RestrictedHttp
        from urllib.parse import urlsplit

        try:   # 保存时就拒绝内网/回环地址，而不是等到投递时静默失败
            await RestrictedHttp([urlsplit(body.webhook_url).hostname or ""])._check_resolved(
                urlsplit(body.webhook_url).hostname or "", urlsplit(body.webhook_url).port or 443)
        except PermissionError as exc:
            raise ApiError(40003, f"Webhook 地址不可用：{exc}") from exc
    if body.event_types is not None:
        from bamy.engine.runtime import NOTIFY_EVENTS

        bad = [e for e in body.event_types if e not in NOTIFY_EVENTS]
        if bad:
            raise ApiError(40003, f"不支持的事件类型：{', '.join(bad)}")
    row = await _prefs(ctx)
    data = NOTIFY_DEFAULT | json.loads(row.notification_settings or "{}") | body.model_dump(exclude_none=True)
    row.notification_settings = json.dumps(data, ensure_ascii=False)
    await ctx.db.commit()
    from bamy.services import webhook

    webhook.invalidate(ctx.user.id)
    return ok(data)


@router.get("/api/user/preferences")
async def get_prefs(ctx: Ctx = Depends(current)):
    row = await _prefs(ctx)
    return ok(PREF_DEFAULT | json.loads(row.preferences or "{}"))


class PrefIn(BaseModel):
    theme: str | None = None
    language: str | None = None
    time_format: str | None = None
    timezone: str | None = None
    page_size: int | None = Field(None, ge=5, le=200)


@router.patch("/api/user/preferences")
async def set_prefs(body: PrefIn, ctx: Ctx = Depends(current)):
    if body.theme and body.theme not in {"light", "dark", "auto"}:
        raise ApiError(40003, "theme 取值 light/dark/auto")
    if body.language and body.language not in {"zh-CN", "en-US"}:
        raise ApiError(40003, "language 取值 zh-CN/en-US")
    if body.time_format and body.time_format not in {"24h", "12h"}:
        raise ApiError(40003, "time_format 取值 24h/12h")
    if body.timezone:
        # 前端用 Intl 按这个时区格式化时间，非法值会让页面抛 RangeError，必须在入库前拒绝
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        if body.timezone not in {"UTC", "local"}:
            try:
                ZoneInfo(body.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ApiError(40003, f"无效的时区：{body.timezone}") from exc
    row = await _prefs(ctx)
    data = PREF_DEFAULT | json.loads(row.preferences or "{}") | body.model_dump(exclude_none=True)
    row.preferences = json.dumps(data, ensure_ascii=False)
    await ctx.db.commit()
    return ok(data)
