"""Telegram 通知插件。

- 订阅 system.alert / order.filled / instance.auto_stopped，经 ctx.http 推送到 Telegram
- order.before_submit 拦截演示：市价单 DENY、超量 MODIFY、其余 ALLOW
- 每分钟推送数受 rate_limit 限制，计数放在绑定级 ctx.state
"""
from __future__ import annotations

import time
from typing import Any

from bamy.plugin import ALLOW, DENY, MODIFY, BasePlugin

LEVELS = {"info": 0, "warn": 1, "error": 2}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """事件可能是 dict 也可能是对象，统一取值。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _data(event: Any) -> Any:
    """订阅型处理函数收到的是 {"event", "data", "ts"}，业务字段在 data 里。"""
    data = _get(event, "data")
    return data if isinstance(data, dict) else event


class TelegramNotify(BasePlugin):
    """Telegram 通知插件。"""

    async def on_bind(self) -> None:
        self.ctx.log_info(f"已绑定到 {self.ctx.scope}#{self.ctx.scope_id}")

    async def on_settings(self) -> None:
        await self.ctx.state.delete("rate")
        self.ctx.log_info("设置已更新，限频计数清零")

    async def on_unbind(self) -> None:
        await self.ctx.state.delete("rate")

    # ---- 订阅型 ----
    async def on_alert(self, event: Any) -> None:
        d = _data(event)
        level = str(_get(d, "level", "warn"))
        await self._send(level, f"[告警] {_get(d, 'title', '')}\n{_get(d, 'message', '')}")

    async def on_filled(self, event: Any) -> None:
        order = _data(event)
        text = (f"[成交] {_get(order, 'symbol', '')} {_get(order, 'side', '')} "
                f"{_get(order, 'filled_amount', _get(order, 'amount', ''))} @ {_get(order, 'filled_price', '')}")
        await self._send("info", text)

    async def on_auto_stopped(self, event: Any) -> None:
        d = _data(event)
        text = f"[实例自动停止] #{_get(d, 'instance_id', '')} 原因：{_get(d, 'reason', '未知')}"
        await self._send("error", text)

    # ---- 拦截型 ----
    async def before_order(self, payload: dict[str, Any]) -> Any:
        if self.ctx.setting("block_market_orders") and payload.get("type") == "market":
            return DENY("该绑定禁止市价单")
        cap = float(self.ctx.setting("max_order_amount", 0) or 0)
        if cap > 0 and float(payload.get("amount") or 0) > cap:
            self.ctx.log_warn(f"订单数量 {payload.get('amount')} 超过上限 {cap}，已改写")
            return MODIFY({**payload, "amount": cap})
        return ALLOW

    # ---- 内部 ----
    async def _send(self, level: str, text: str) -> None:
        if LEVELS.get(level, 0) < LEVELS.get(str(self.ctx.setting("min_level", "warn")), 1):
            return
        if not await self._take_quota():
            self.ctx.log_warn("超过每分钟推送上限，本条丢弃")
            return
        if self.ctx.http is None:
            self.ctx.log_error("未开放出网，无法推送")
            return
        url = f"https://api.telegram.org/bot{self.ctx.setting('bot_token')}/sendMessage"
        resp = await self.ctx.http.post(url, json={"chat_id": self.ctx.setting("chat_id"), "text": text})
        if getattr(resp, "status_code", 200) >= 400:
            # 不打印 URL，避免 token 进日志
            self.ctx.log_error(f"Telegram 推送失败：HTTP {resp.status_code}")

    async def _take_quota(self) -> bool:
        minute = int(time.time() // 60)
        rate = await self.ctx.state.get("rate") or {}
        count = int(rate.get("count", 0)) if rate.get("minute") == minute else 0
        if count >= int(self.ctx.setting("rate_limit", 20)):
            return False
        await self.ctx.state.set("rate", {"minute": minute, "count": count + 1})
        return True


PLUGIN = TelegramNotify
