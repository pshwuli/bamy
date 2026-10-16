"""网格策略：价格每下穿一条网格线买入一格，每上穿一条网格线卖出最低的一格。

状态全部放在 ctx.state，进程重启后可接着跑：
- grid.lines       网格线
- grid.last_index  上一次所在格子
- grid.positions   {pos_id: 持仓}，每格一仓，pos_id 在平仓前稳定
- grid.pending     {order_key: 挂单用途}，用于把成交回报对应回网格
- grid.paused      是否暂停
"""
from __future__ import annotations

import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick
from helpers.grid import grid_index, grid_prices, stop_loss_price

TERMINAL = {"filled", "canceled", "cancelled", "rejected", "failed", "expired"}


class GridStrategy(BaseStrategy):
    """网格策略。"""

    async def on_start(self) -> None:
        p = self.ctx.params
        lines = grid_prices(float(p["lower_price"]), float(p["upper_price"]), int(p["grid_num"]))
        saved = await self.ctx.state.get("grid.lines")
        if saved != lines:
            # 参数变化或首次启动：重置网格位置，已有持仓保留
            await self.ctx.state.set("grid.lines", lines)
            await self.ctx.state.set("grid.last_index", None)
        self.lines = lines
        self.positions: dict[str, dict[str, Any]] = await self.ctx.state.get("grid.positions", {}) or {}
        self.pending: dict[str, dict[str, Any]] = await self.ctx.state.get("grid.pending", {}) or {}
        self.last_index: int | None = await self.ctx.state.get("grid.last_index")
        self.paused: bool = bool(await self.ctx.state.get("grid.paused", False))
        self.ctx.log_info(f"网格启动：{len(lines) - 1} 格，区间 {lines[0]} - {lines[-1]}，已有持仓 {len(self.positions)}")

    async def on_tick(self, tick: Tick) -> None:
        if self.paused:
            return
        price = float(tick.price)
        p = self.ctx.params
        if p.get("enable_stop_loss") and price <= stop_loss_price(self.lines[0], float(p.get("stop_loss_pct", 5.0))):
            self.ctx.log_warn(f"价格 {price} 触发止损，全部平仓并暂停")
            await self.cancel_all_orders()
            await self.close_all_positions()
            await self.pause()
            return

        idx = grid_index(price, self.lines)
        if self.last_index is None:
            await self._set_index(idx)
            return
        if idx < self.last_index:
            # 下穿：每跨过一条线买一格
            for level in range(self.last_index, max(idx, -1), -1):
                if 0 <= level < len(self.lines) - 1:
                    await self._open_grid(level)
        elif idx > self.last_index:
            # 上穿：每跨过一条线卖出最低价的一格
            for _ in range(idx - self.last_index):
                await self._close_lowest()
        await self._set_index(idx)

    async def on_order(self, order: OrderView) -> None:
        await self._apply_order(order)

    async def on_stop(self) -> None:
        await self._save()
        self.ctx.log_info("网格停止，状态已保存")

    async def on_pause(self) -> None:
        self.paused = True
        await self.ctx.state.set("grid.paused", True)

    async def on_resume(self) -> None:
        self.paused = False
        await self.ctx.state.set("grid.paused", False)

    # ---- 能力方法 ----
    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        pos = self.positions.get(position_id)
        if pos is None:
            raise ValueError(f"持仓 {position_id} 不存在")
        qty = pos["amount"] if amount is None else min(float(amount), pos["amount"])
        if qty <= 0:
            raise ValueError("平仓数量必须大于 0")
        order = await self.ctx.sell(qty, type_="market")
        await self._track(order, {"purpose": "close", "pos_id": position_id, "amount": qty})
        return {"position_id": position_id, "amount": qty, "order_id": order.id}

    async def close_positions(self, position_ids: list[str]) -> Any:
        return [await self.close_position(pid) for pid in position_ids]

    async def close_all_positions(self) -> Any:
        return await self.close_positions(list(self.positions))

    async def cancel_order(self, order_id: str) -> Any:
        result = await self.ctx.cancel(order_id)
        self.pending.pop(str(order_id), None)
        await self._save()
        return result

    async def cancel_all_orders(self) -> Any:
        result = await self.ctx.cancel_all()
        self.pending.clear()
        await self._save()
        return result

    async def list_positions(self) -> list[dict[str, Any]]:
        ticker = self.ctx.get_ticker() or {}
        mark = ticker.get("last") or ticker.get("price")
        out = []
        for pid, pos in sorted(self.positions.items(), key=lambda kv: kv[1]["entry_price"]):
            entry = pos["entry_price"]
            pnl = (mark - entry) * pos["amount"] if mark else None
            out.append({
                "id": pid, "symbol": self.ctx.symbol, "side": "long", "amount": pos["amount"],
                "entry_price": entry, "mark_price": mark, "unrealized_pnl": pnl,
                "unrealized_pnl_pct": (mark / entry - 1) * 100 if mark and entry else None,
                "leverage": 1, "label": f"第{pos['level'] + 1}格", "opened_at": pos["opened_at"],
            })
        return out

    async def pause(self) -> None:
        await self.on_pause()
        self.ctx.log_info("网格已暂停")

    async def resume(self) -> None:
        await self.on_resume()
        self.ctx.log_info("网格已恢复")

    # ---- 内部 ----
    async def _open_grid(self, level: int) -> None:
        pos_id = f"grid_{level}"
        busy = any(v.get("pos_id") == pos_id and v["purpose"] == "open" for v in self.pending.values())
        if pos_id in self.positions or busy:
            return
        amount = float(self.ctx.params.get("amount_per_grid", 0.001))
        if self.ctx.params.get("order_type", "limit") == "limit":
            order = await self.ctx.buy(amount, price=self.lines[level], type_="limit")
        else:
            order = await self.ctx.buy(amount, type_="market")
        await self._track(order, {"purpose": "open", "pos_id": pos_id, "level": level, "amount": amount})

    async def _close_lowest(self) -> None:
        closing = {v.get("pos_id") for v in self.pending.values() if v["purpose"] == "close"}
        free = [(k, v) for k, v in self.positions.items() if k not in closing]
        if free:
            pid, _ = min(free, key=lambda kv: kv[1]["entry_price"])
            await self.close_position(pid)

    async def _track(self, order: OrderView, meta: dict[str, Any]) -> None:
        self.pending[self._key(order)] = meta
        await self._apply_order(order)  # 市价单可能已同步成交
        await self._save()

    async def _apply_order(self, order: OrderView) -> None:
        meta = self.pending.get(self._key(order))
        if meta is None or order.status not in TERMINAL:
            return
        self.pending.pop(self._key(order), None)
        if order.status == "filled":
            qty = float(order.filled_amount or meta["amount"])
            if meta["purpose"] == "open":
                self.positions[meta["pos_id"]] = {
                    "level": meta["level"], "amount": qty,
                    "entry_price": float(order.filled_price or order.price or self.lines[meta["level"]]),
                    "opened_at": int(time.time() * 1000),
                }
            else:
                pos = self.positions.get(meta["pos_id"])
                if pos is not None:
                    pos["amount"] = round(pos["amount"] - qty, 12)
                    if pos["amount"] <= 0:
                        self.positions.pop(meta["pos_id"], None)
        await self._save()

    @staticmethod
    def _key(order: OrderView) -> str:
        return str(order.id if order.id is not None else order.client_order_id)

    async def _set_index(self, idx: int) -> None:
        self.last_index = idx
        await self.ctx.state.set("grid.last_index", idx)

    async def _save(self) -> None:
        await self.ctx.state.set("grid.positions", self.positions)
        await self.ctx.state.set("grid.pending", self.pending)


STRATEGY = GridStrategy
