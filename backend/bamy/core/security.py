"""密码哈希、JWT 签发与校验、TOTP。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
import pyotp

from bamy.core.config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if len(password) < settings.password_min_length:
        raise ValueError(f"密码长度不能少于 {settings.password_min_length} 位")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(user_id: int, role: str, extra: dict[str, Any] | None = None) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "jti": uuid.uuid4().hex,
        "iat": int(time.time()),
        "exp": int(time.time()) + settings.access_token_expire,
    }
    payload.update(extra or {})
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_refresh_token(user_id: int) -> str:
    return jwt.encode(
        {"sub": str(user_id), "type": "refresh", "jti": uuid.uuid4().hex, "iat": int(time.time()),
         "exp": int(time.time()) + settings.refresh_token_expire},
        settings.secret_key, algorithm=ALGORITHM,
    )


class TokenError(Exception):
    def __init__(self, code: int):
        self.code = code


def decode_token(token: str, expected: str = "access") -> dict[str, Any]:
    from bamy.core.errors import ApiError
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(40103) from exc
    except jwt.PyJWTError as exc:
        raise ApiError(40102) from exc
    if payload.get("type") != expected:
        raise ApiError(40102)
    return payload


def new_totp_secret() -> str:
    return pyotp.random_base32()


_used_totp: dict[tuple[str, str], float] = {}


def verify_totp_once(secret: str, code: str, user_key: str) -> bool:
    """校验 TOTP 并防重放：同一用户的同一个码，在有效窗口（±1 个 30 秒步长）内只能用一次。"""
    if not verify_totp(secret, code):
        return False
    now = time.time()
    for k in [k for k, t in _used_totp.items() if now - t > 120]:
        _used_totp.pop(k, None)
    key = (user_key, str(code).strip())
    if key in _used_totp:
        return False
    _used_totp[key] = now
    return True


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=settings.app_name)


def gen_recovery_codes(n: int = 10) -> list[str]:
    import secrets
    return ["-".join(secrets.token_hex(2) for _ in range(2)) for _ in range(n)]


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def in_seconds(sec: int) -> datetime:
    return utcnow() + timedelta(seconds=sec)
