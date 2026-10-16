"""敏感字段加密（对称）与脱敏工具。字段清单见《数据模型》第八节。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from bamy.core.config import settings


def _key() -> bytes:
    return hashlib.sha256(settings.encrypt_key.encode()).digest()


def encrypt(plain: str) -> str:
    if plain == "":
        return ""
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plain.encode(), None)
    return "v1:" + base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    if token.startswith("v1:"):
        raw = base64.urlsafe_b64decode(token[3:])
        return AESGCM(_key()).decrypt(raw[:12], raw[12:], None).decode()
    # 兼容未加密的历史值
    return token


def mask(value: str, head: int = 4, tail: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= head + tail:
        return "*" * len(value)
    return f"{value[:head]}****{value[-tail:]}"


def hmac_token(token: str) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return base64.urlsafe_b64encode(os.urandom(nbytes)).decode().rstrip("=")
