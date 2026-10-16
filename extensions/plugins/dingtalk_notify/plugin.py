"""钉钉群机器人通知。

加签（安全设置选"加签"时）：sign = urlencode(base64(HmacSHA256(key=secret, msg=f"{timestamp}\\n{secret}")))，timestamp 为毫秒，拼到 URL 上。
ctx.state 键：dt.rate {minute, count}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote_plus

from bamy.plugin import BasePlugin


def _data(event: Any) -> dict[str, Any]:
    d = event.get("data") if isinstance(event, dict) else None
    return d if isinstance(d, dict) else {}


def dingtalk_sign(secret: str, ts_ms: int) -> str:
    digest = hmac.new(secret.encode(), f"{ts_ms}\n{secret}".encode(), hashlib.sha256).digest()
    return quote_plus(base64.b64encode(digest).decode())


class DingtalkNotify(BasePlugin):
    """钉钉通知。"""

    async def on_filled(self, event: Any) -> None:
        if not self.ctx.setting("notify_fills", True):
            return
        d = _data(event)
        await self._send(f"成交 · 实例#{d.get('instance_id', '')} {d.get('symbol', '')} {d.get('side', '')} "
                         f"{d.get('filled_amount') or d.get('amount') or ''} @ {d.get('filled_price') or ''}")

    async def on_error(self, event: Any) -> None:
        d = _data(event)
        await self._send(f"实例出错 · #{d.get('instance_id', '')} {d.get('error', '')}")

    async def on_auto_stopped(self, event: Any) -> None:
        d = _data(event)
        await self._send(f"实例自动停止 · #{d.get('instance_id', '')} 原因：{d.get('reason', '未知')}")

    async def on_risk(self, event: Any) -> None:
        d = _data(event)
        await self._send(f"风控触发 · #{d.get('instance_id', '')} {d.get('message') or d.get('rule') or ''}")

    async def on_alert(self, event: Any) -> None:
        d = _data(event)
        await self._send(f"系统告警 · {d.get('title', '')} {d.get('message', '')}")

    async def _send(self, text: str) -> None:
        if self.ctx.http is None or not await self._quota():
            return
        body = {"msgtype": "text", "text": {"content": f"BAMY {text}"}}
        url = str(self.ctx.setting("webhook_url"))
        secret = str(self.ctx.setting("secret") or "")
        if secret:
            ts = int(time.time() * 1000)
            url += ("&" if "?" in url else "?") + f"timestamp={ts}&sign={dingtalk_sign(secret, ts)}"
        try:
            resp = await self.ctx.http.post(url, json=body)
            data = resp.json() if getattr(resp, "status_code", 200) < 400 else {}
            if getattr(resp, "status_code", 200) >= 400 or (data.get("errcode") not in (None, 0)):
                self.ctx.log_error(f"钉钉推送失败：HTTP {resp.status_code} {data.get('errmsg', '')}")
        except Exception as exc:  # noqa: BLE001
            self.ctx.log_error(f"钉钉推送失败：{type(exc).__name__}")

    async def _quota(self) -> bool:
        minute = int(time.time() // 60)
        rate = await self.ctx.state.get("dt.rate") or {}
        count = int(rate.get("count", 0)) if rate.get("minute") == minute else 0
        if count >= int(self.ctx.setting("rate_limit", 20)):
            self.ctx.log_warn("超过每分钟推送上限，本条丢弃")
            return False
        await self.ctx.state.set("dt.rate", {"minute": minute, "count": count + 1})
        return True


PLUGIN = DingtalkNotify
