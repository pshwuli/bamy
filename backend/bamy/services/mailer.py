"""邮件渠道（可选）：只用于把密码重置链接送到用户本人邮箱。

配置 SMTP_HOST 后启用；未配置时 send_reset_mail 返回 False，调用方只记录"有人申请"，不落任何 token。
"""
from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("bamy.mailer")


def _cfg() -> dict[str, str]:
    return {k: os.getenv(k, "") for k in ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM",
                                          "SMTP_TLS", "PUBLIC_BASE_URL")}


def enabled() -> bool:
    return bool(_cfg()["SMTP_HOST"])


def _send(to: str, subject: str, body: str) -> None:
    c = _cfg()
    msg = EmailMessage()
    msg["From"] = c["SMTP_FROM"] or c["SMTP_USER"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    port = int(c["SMTP_PORT"] or 587)
    mode = (c["SMTP_TLS"] or "starttls").lower()
    ctx = ssl.create_default_context()
    if mode == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(c["SMTP_HOST"], port, timeout=15, context=ctx)
    else:
        server = smtplib.SMTP(c["SMTP_HOST"], port, timeout=15)
        if mode == "starttls":
            server.starttls(context=ctx)
    with server:
        if c["SMTP_USER"]:
            server.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
        server.send_message(msg)


async def send_reset_mail(to: str | None, token: str) -> bool:
    if not to or not enabled():
        return False
    base = (_cfg()["PUBLIC_BASE_URL"] or "").rstrip("/")
    body = (f"有人申请重置你在 BAMY 的密码。如果是你本人，请在 30 分钟内打开：\n\n{base}/reset-password?token={token}\n\n"
            "不是你本人操作请忽略本邮件。")
    try:
        await asyncio.to_thread(_send, to, "BAMY 密码重置", body)
        return True
    except Exception as exc:  # noqa: BLE001 - 邮件失败不影响接口返回，也不泄露 token
        log.warning("重置邮件发送失败：%s", type(exc).__name__)
        return False
