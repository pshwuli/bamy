"""定投加仓：每隔 interval 秒按 quote_amount 市价买入；可选在跌破均价 dip_pct% 时追加。

ctx.state 键：
- dca.stats  {rounds, dips, total_base, total_quote, last_buy_at, last_dip_at}
- dca.paused 是否暂停
"""
from __future__ import annotations

import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick


def _empty_stats() -> dict[str, Any]:
    return {"rounds": 0, "dips": 0, "total_base": 0.0, "total_quote": 0.0,
            "last_buy_at": 0, "last_dip_at": 0}


class DcaStrategy(BaseStrategy):
    """定投加仓策略。"""

    async def on_start(self) -> None:
        self.stats: dict[str, Any] = await self.ctx.state.get("dca.stats") or _empty_stats()
        self.paused = bool(await self.ctx.state.get("dca.paused", False))
        self.ctx.log_info(
            f"定投启动：每 {self.ctx.params['interval']} 秒投入 {self.ctx.params['quote_amount']}，"
            f"已定投 {self.stats['rounds']} 次，累计 {self.stats['total_base']}"
        )

    async def on_tick(self, tick: Tick) -> None:
        if self.paused:
            return
        p = self.ctx.params
        now = int(time.time())
        price = float(tick.price)
        if price <= 0:
            return

        max_rounds = int(p.get("max_rounds", 0))
        due = now - int(self.stats["last_buy_at"]) >= int(p["interval"])
        if due and (max_rounds == 0 or self.stats["rounds"] < max_rounds):
            await self._buy(price, kind="round")
            return

        # depends_on 不满足时 dip_pct / dip_cooldown 不会出现在 params 里
        if p.get("enable_dip_buy") and self.stats["total_base"] > 0:
            avg = self.stats["total_quote"] / self.stats["total_base"]
            cooled = now - int(self.stats["last_dip_at"]) >= int(p.get("dip_cooldown", 3600))
            if cooled and price <= avg * (1 - float(p.get("dip_pct", 5)) / 100):
                self.ctx.log_info(f"价格 {price} 低于均价 {avg:.8f} 达 {p.get('dip_pct')}%，追加买入")
                await self._buy(price, kind="dip")

    async def on_order(self, order: OrderView) -> None:
        if order.status == "filled" and order.side == "buy":
            qty = float(order.filled_amount or order.amount)
            px = float(order.filled_price or order.price or 0)
            self.stats["total_base"] += qty
            self.stats["total_quote"] += qty * px
            await self.ctx.state.set("dca.stats", self.stats)
            self.ctx.log_info(f"定投成交 {qty} @ {px}，累计持有 {self.stats['total_base']}")
        elif order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"定投下单失败：{order.reason}")

    async def on_stop(self) -> None:
        await self.ctx.state.set("dca.stats", self.stats)
        self.ctx.log_info("定投停止")

    async def pause(self) -> None:
        self.paused = True
        await self.ctx.state.set("dca.paused", True)
        self.ctx.log_info("定投已暂停")

    async def resume(self) -> None:
        self.paused = False
        await self.ctx.state.set("dca.paused", False)
        self.ctx.log_info("定投已恢复")

    async def reset(self) -> None:
        self.stats = _empty_stats()
        await self.ctx.state.delete("dca.stats")
        self.ctx.log_info("定投统计已重置")

    async def _buy(self, price: float, kind: str) -> None:
        amount = round(float(self.ctx.params["quote_amount"]) / price, 8)
        now = int(time.time())
        # 先记时间，避免下单失败时每个 tick 都重试
        if kind == "round":
            self.stats["rounds"] += 1
            self.stats["last_buy_at"] = now
        else:
            self.stats["dips"] += 1
            self.stats["last_dip_at"] = now
        await self.ctx.state.set("dca.stats", self.stats)
        await self.ctx.buy(amount, type_="market")


STRATEGY = DcaStrategy
