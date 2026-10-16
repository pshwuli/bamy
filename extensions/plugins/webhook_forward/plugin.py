"""Webhook 转发：把勾选的事件转发到用户自己的公网地址（出网经框架 RestrictedHttp：只许公网、禁跳转）。

ctx.state 键：
- wf.rate  {minute, count} 每分钟转发计数
"""
from __future__ import annotations

import json
import time
from typing import Any

from bamy.plugin import BasePlugin

TITLES = {"order.filled": "成交", "order.rejected": "拒单", "order.failed": "下单失败", "position.changed": "持仓变化",
          "instance.error": "实例出错", "instance.auto_stopped": "实例自动停止", "risk.breached": "风控触发",
          "risk.circuit_break": "熔断", "account.invalid": "账号失效", "guard.missing": "保护单丢失",
          "system.alert": "系统告警"}


def summarize(event: str, d: dict[str, Any]) -> str:
    """把事件压成一行人能看懂的话（群机器人格式用）。"""
    if event.startswith("order."):
        px = d.get("filled_price") or d.get("price") or ""
        qty = d.get("filled_amount") or d.get("amount") or ""
        tail = f" 原因：{d['reason']}" if d.get("reason") and event != "order.filled" else ""
        return f"[{TITLES.get(event, event)}] 实例#{d.get('instance_id', '')} {d.get('symbol', '')} {d.get('side', '')} {qty} @ {px}{tail}"
    msg = d.get("message") or d.get("reason") or d.get("error") or d.get("title") or ""
    ref = d.get("instance_id") or d.get("account_id") or ""
    return f"[{TITLES.get(event, event)}] #{ref} {msg}".strip()


class WebhookForward(BasePlugin):
    """Webhook 转发。"""

    async def on_event(self, event: Any) -> None:
        if not isinstance(event, dict):
            return
        name = str(event.get("event") or "")
        wanted = self.ctx.setting("events") or []
        if wanted and name not in wanted:
            return
        if not await self._quota():
            self.ctx.log_warn("超过每分钟转发上限，本条丢弃")
            return
        if self.ctx.http is None:
            return
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        fmt = self.ctx.setting("format", "json")
        if fmt == "feishu":
            payload = {"msg_type": "text", "content": {"text": summarize(name, data)}}
        elif fmt in {"dingtalk", "wecom"}:
            payload = {"msgtype": "text", "text": {"content": summarize(name, data)}}
        else:
            payload = json.loads(json.dumps({"event": name, "data": data, "ts": event.get("ts")}, default=str))
        try:
            resp = await self.ctx.http.post(str(self.ctx.setting("url")), json=payload)
            if getattr(resp, "status_code", 200) >= 400:
                self.ctx.log_error(f"转发 {name} 失败：HTTP {resp.status_code}")
        except PermissionError as exc:
            self.ctx.log_error(f"转发被拒：{exc}")
        except Exception as exc:  # noqa: BLE001
            self.ctx.log_error(f"转发 {name} 失败：{type(exc).__name__}")

    async def _quota(self) -> bool:
        minute = int(time.time() // 60)
        rate = await self.ctx.state.get("wf.rate") or {}
        count = int(rate.get("count", 0)) if rate.get("minute") == minute else 0
        if count >= int(self.ctx.setting("rate_limit", 30)):
            return False
        await self.ctx.state.set("wf.rate", {"minute": minute, "count": count + 1})
        return True


PLUGIN = WebhookForward
