"""日线趋势 + 波动率目标仓位（现货只做多）。

每天 UTC 0 点日线收盘后计算一次目标仓位，偏离超过阈值才调仓；平时不交易。

ctx.state 键：
- tv.last_bar 上次处理的日线收盘时间戳
- tv.weight   上次的目标仓位
"""
from __future__ import annotations

import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick
from helpers.signals import Params, needs_rebalance, target_weight

DAY = 86400
QUOTE = "USDT"
MIN_NOTIONAL = 5.0


class TrendVolTargetStrategy(BaseStrategy):
    """日线趋势 + 波动率目标仓位。"""

    async def on_start(self) -> None:
        p = self.ctx.params
        self.p = Params(fast=int(p["fast_ema"]), slow=int(p["slow_ema"]), vol_n=int(p["vol_window"]),
                        target_vol=float(p["target_vol_pct"]) / 100, max_alloc=float(p["max_alloc_pct"]) / 100,
                        rebalance_band=float(p["rebalance_band_pct"]) / 100)
        if self.p.fast >= self.p.slow:
            raise ValueError("快线周期必须小于慢线周期")
        self.last_bar = int(await self.ctx.state.get("tv.last_bar", 0) or 0)
        self.weight = float(await self.ctx.state.get("tv.weight", 0) or 0)
        self.busy = self.paused = False
        self.ctx.log_info(f"日线趋势启动：EMA{self.p.fast}/{self.p.slow}，目标年化波动 {self.p.target_vol:.0%}，"
                          f"最多投入 {self.p.max_alloc:.0%}，偏离 {self.p.rebalance_band:.0%} 才调仓")

    async def on_tick(self, tick: Tick) -> None:
        if self.paused or self.busy:
            return
        bar = int(time.time()) // DAY * DAY
        if bar <= self.last_bar:
            return
        self.busy = True
        try:
            await self._daily(bar, float(tick.price))
        finally:
            self.busy = False

    async def _daily(self, bar: int, price: float) -> None:
        klines = await self.ctx.get_klines(timeframe="1d", limit=self.p.warmup + 10)
        closes = [float(k[4]) for k in klines if int(k[0]) // 1000 + DAY <= bar]
        self.last_bar = bar
        await self.ctx.state.set("tv.last_bar", bar)
        w = target_weight(closes, self.p)
        if w is None:
            self.ctx.log_info(f"日线不足 {self.p.warmup} 根（现有 {len(closes)}），等待")
            return
        bal = await self.ctx.fetch_balance()
        base = self.ctx.symbol.split("/")[0]
        cash = float((bal.get(QUOTE) or {}).get("free") or 0)
        held = float((bal.get(base) or {}).get("free") or 0)
        equity = cash + held * price
        if equity <= 0:
            return
        cur = held * price / equity
        self.weight = w
        await self.ctx.state.set("tv.weight", w)
        if not needs_rebalance(cur, w, self.p.rebalance_band):
            self.ctx.log_info(f"目标仓位 {w:.0%}，当前 {cur:.0%}，偏离在阈值内，不调仓")
            return
        delta = (w - cur) * equity
        if abs(delta) < MIN_NOTIONAL:
            self.ctx.log_info(f"需调仓 {delta:+.2f} {QUOTE}，低于最小下单额 {MIN_NOTIONAL:g}，跳过")
            return
        if delta > 0:
            amount = round(min(delta, cash) / price, 8)
            self.ctx.log_info(f"趋势开、目标仓位 {w:.0%}（当前 {cur:.0%}），买入约 {delta:.2f} {QUOTE}")
            await self.ctx.buy(amount, type_="market")
        else:
            amount = round(min(-delta / price, held), 8)
            why = "趋势关闭，清仓" if w == 0 else f"波动变大，目标仓位降到 {w:.0%}"
            self.ctx.log_info(f"{why}（当前 {cur:.0%}），卖出 {amount:.8g}")
            await self.ctx.sell(amount, type_="market")

    async def on_order(self, order: OrderView) -> None:
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"调仓订单 {order.status}：{order.reason}")

    async def on_stop(self) -> None:
        self.ctx.log_info("日线趋势停止")

    async def list_positions(self) -> list[dict[str, Any]]:
        bal = await self.ctx.fetch_balance()
        base = self.ctx.symbol.split("/")[0]
        qty = float((bal.get(base) or {}).get("total") or 0)
        if qty <= 0:
            return []
        t = self.ctx.get_ticker() or {}
        mark = t.get("price") or t.get("last")
        return [{"id": f"tv-{base}", "symbol": self.ctx.symbol, "side": "long", "amount": qty, "mark_price": mark,
                 "label": f"目标仓位 {self.weight:.0%}"}]

    async def close_all_positions(self) -> Any:
        bal = await self.ctx.fetch_balance()
        qty = float((bal.get(self.ctx.symbol.split("/")[0]) or {}).get("free") or 0)
        if qty > 0:
            await self.ctx.sell(round(qty, 8), type_="market")
        return {"sold": qty}

    async def pause(self) -> None:
        self.paused = True
        self.ctx.log_info("已暂停：不再调仓，持仓保留")

    async def resume(self) -> None:
        self.paused = False
        self.ctx.log_info("已恢复")


STRATEGY = TrendVolTargetStrategy
