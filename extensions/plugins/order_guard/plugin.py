"""下单守卫：order.before_submit 检查点，只管开仓单（reduce_only 一律放行）。"""
from __future__ import annotations

import time
from typing import Any

from bamy.plugin import ALLOW, DENY, MODIFY, BasePlugin


def parse_hours(text: str) -> list[tuple[int, int]]:
    out = []
    for part in str(text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or int(a) + 1)
        out.append((max(0, lo), min(24, hi)))
    return out


class OrderGuard(BasePlugin):
    """下单守卫。"""

    async def on_bind(self) -> None:
        try:
            parse_hours(self.ctx.setting("hours", ""))
        except ValueError:
            self.ctx.log_warn("允许开仓时段格式不对，例如 9-23 或 0-2,8-24；当前按全天处理")

    async def before_order(self, payload: dict[str, Any]) -> Any:
        if payload.get("reduce_only"):
            return ALLOW
        symbol = str(payload.get("symbol") or "")
        base = symbol.split("/")[0].upper()
        black = {x.strip().upper() for x in str(self.ctx.setting("blacklist", "")).replace("，", ",").split(",") if x.strip()}
        if base in black:
            return DENY(f"{base} 在下单守卫的黑名单里")
        try:
            ranges = parse_hours(self.ctx.setting("hours", ""))
        except ValueError:
            ranges = []
        if ranges:
            hour = time.gmtime(time.time() + int(self.ctx.setting("tz_offset", 8)) * 3600).tm_hour
            if not any(lo <= hour < hi for lo, hi in ranges):
                return DENY(f"现在 {hour} 点，不在允许开仓的时段 {self.ctx.setting('hours')}")
        cap = float(self.ctx.setting("max_notional", 0) or 0)
        price = float(payload.get("price") or payload.get("ref_price") or 0)
        amount = float(payload.get("amount") or 0)
        if cap > 0 and price > 0 and amount * price > cap:
            if self.ctx.setting("over_action", "shrink") == "deny":
                return DENY(f"单笔约 {amount * price:.2f} USDT，超过上限 {cap:g}")
            new_amount = float(f"{cap / price:.8g}")
            self.ctx.log_warn(f"{symbol} 单笔约 {amount * price:.2f} USDT 超过上限 {cap:g}，数量 {amount:g} → {new_amount:g}")
            return MODIFY({**payload, "amount": new_amount})
        return ALLOW


PLUGIN = OrderGuard
