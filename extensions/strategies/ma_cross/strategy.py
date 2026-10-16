"""均线交叉：快线上穿慢线（金叉）开一笔多仓，下穿（死叉）平掉全部持仓。

ctx.state 键：
- ma.positions {pos_id: {amount, entry_price, opened_at, seq}}
- ma.last_diff 上一次 快线-慢线 的差值，用于判断穿越
- ma.seq       持仓序号
"""
from __future__ import annotations

import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


class MaCrossStrategy(BaseStrategy):
    """均线交叉策略。"""

    async def on_start(self) -> None:
        p = self.ctx.params
        if int(p["fast_period"]) >= int(p["slow_period"]):
            raise ValueError("快线周期必须小于慢线周期")
        self.positions: dict[str, dict[str, Any]] = await self.ctx.state.get("ma.positions", {}) or {}
        self.last_diff: float | None = await self.ctx.state.get("ma.last_diff")
        self.seq: int = int(await self.ctx.state.get("ma.seq", 0) or 0)
        self.pending: dict[str, dict[str, Any]] = {}
        self.last_check = 0.0
        self.mark: float | None = None
        self.ctx.log_info(f"均线交叉启动：MA{p['fast_period']}/MA{p['slow_period']} {p.get('timeframe', '15m')}")

    async def on_tick(self, tick: Tick) -> None:
        self.mark = float(tick.price)
        now = time.time()
        if now - self.last_check < int(self.ctx.params.get("check_interval", 60)):
            return
        self.last_check = now

        p = self.ctx.params
        fast_n, slow_n = int(p["fast_period"]), int(p["slow_period"])
        klines = await self.ctx.get_klines(timeframe=p.get("timeframe", "15m"), limit=slow_n + 1)
        closes = [float(k[4]) for k in klines]  # [ts, open, high, low, close, volume]
        fast, slow = sma(closes, fast_n), sma(closes, slow_n)
        if fast is None or slow is None:
            return
        diff = fast - slow
        prev = self.last_diff
        self.last_diff = diff
        await self.ctx.state.set("ma.last_diff", diff)
        if prev is None:
            return

        if prev <= 0 < diff and len(self.positions) < int(p.get("max_positions", 1)):
            self.ctx.log_info(f"金叉：MA{fast_n}={fast:.8f} 上穿 MA{slow_n}={slow:.8f}，开仓")
            order = await self.ctx.buy(float(p["amount"]), type_="market")
            self.seq += 1
            await self.ctx.state.set("ma.seq", self.seq)
            await self._track(order, {"purpose": "open", "pos_id": f"ma_{self.seq}", "seq": self.seq})
        elif prev >= 0 > diff and self.positions:
            self.ctx.log_info(f"死叉：MA{fast_n}={fast:.8f} 下穿 MA{slow_n}={slow:.8f}，全部平仓")
            await self.close_all_positions()

    async def on_order(self, order: OrderView) -> None:
        await self._apply(order)

    async def on_stop(self) -> None:
        await self.ctx.state.set("ma.positions", self.positions)

    # ---- 能力方法 ----
    async def list_positions(self) -> list[dict[str, Any]]:
        mark = self.mark
        if mark is None:
            ticker = self.ctx.get_ticker() or {}
            mark = ticker.get("last") or ticker.get("price")
        out: list[dict[str, Any]] = []
        for pid, pos in sorted(self.positions.items(), key=lambda kv: kv[1]["seq"]):
            entry = pos["entry_price"]
            pnl = (mark - entry) * pos["amount"] if mark and entry else None
            out.append({
                "id": pid,
                "symbol": self.ctx.symbol,
                "side": "long",
                "amount": pos["amount"],
                "entry_price": entry,
                "mark_price": mark,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": (mark / entry - 1) * 100 if mark and entry else None,
                "leverage": 1,
                "label": f"第{pos['seq']}仓",
                "opened_at": pos["opened_at"],
            })
        return out

    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        pos = self.positions.get(position_id)
        if pos is None:
            raise ValueError(f"持仓 {position_id} 不存在")
        qty = pos["amount"] if amount is None else min(float(amount), pos["amount"])
        if qty <= 0:
            raise ValueError("平仓数量必须大于 0")
        order = await self.ctx.sell(qty, type_="market")
        await self._track(order, {"purpose": "close", "pos_id": position_id})
        return {"position_id": position_id, "amount": qty, "order_id": order.id}

    async def close_positions(self, position_ids: list[str]) -> Any:
        return [await self.close_position(pid) for pid in position_ids]

    async def close_all_positions(self) -> Any:
        return await self.close_positions(list(self.positions))

    # ---- 内部 ----
    async def _track(self, order: OrderView, meta: dict[str, Any]) -> None:
        self.pending[str(order.id if order.id is not None else order.client_order_id)] = meta
        await self._apply(order)

    async def _apply(self, order: OrderView) -> None:
        key = str(order.id if order.id is not None else order.client_order_id)
        meta = self.pending.get(key)
        if meta is None or order.status not in {"filled", "canceled", "rejected", "failed"}:
            return
        self.pending.pop(key, None)
        if order.status != "filled":
            self.ctx.log_warn(f"订单 {key} 未成交：{order.status} {order.reason or ''}")
            return
        qty = float(order.filled_amount or order.amount)
        if meta["purpose"] == "open":
            self.positions[meta["pos_id"]] = {
                "amount": qty, "seq": meta["seq"], "opened_at": int(time.time() * 1000),
                "entry_price": float(order.filled_price or order.price or self.mark or 0),
            }
        else:
            pos = self.positions.get(meta["pos_id"])
            if pos is not None:
                pos["amount"] = round(pos["amount"] - qty, 12)
                if pos["amount"] <= 0:
                    self.positions.pop(meta["pos_id"], None)
        await self.ctx.state.set("ma.positions", self.positions)


STRATEGY = MaCrossStrategy
