"""趋势回调分批止盈（现货只做多）。

决策内核在 helpers/signals.py，与研究回测逐根重放结果一致。
- 每根 K 线收盘后（按 timeframe）判断入场/离场
- 止损与第一目标在每个 tick 用最新价检查（盘中触发）
- 仓位按"亏到止损只损失权益的 risk_pct"计算；金额不够交易所最小下单额时跳过并记日志

ctx.state 键：
- tp.pos   当前持仓 {id, amount, entry_price, stop, tp1, took_tp1, entry_bar_ts, cost}
- tp.last_bar 上次处理的已收盘 K 线时间戳
"""
from __future__ import annotations

import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick
from helpers.signals import Params, position_quote, should_enter, should_exit, snapshot

TF_SECONDS = {"1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}
QUOTE = "USDT"
MIN_NOTIONAL = 5.0          # 币安现货 USDT 交易对的最小下单额（NOTIONAL 过滤器，2026-10 实测）


class TrendPullbackStrategy(BaseStrategy):
    """趋势回调分批止盈。"""

    async def on_start(self) -> None:
        p = self.ctx.params
        self.p = Params(trend=int(p["trend_period"]), rsi_n=int(p["rsi_period"]), rsi_in=float(p["rsi_entry"]),
                        stop_atr=float(p["stop_atr"]), tp1_atr=float(p["tp1_atr"]), tp1_frac=float(p["tp1_fraction"]) / 100,
                        exit_ema=int(p["exit_ema"]), max_hold=int(p["max_hold_bars"]))
        self.tf = p.get("timeframe", "4h")
        self.tf_sec = TF_SECONDS[self.tf]
        self.pos: dict[str, Any] | None = await self.ctx.state.get("tp.pos")
        self.last_bar: int = int(await self.ctx.state.get("tp.last_bar", 0) or 0)
        self.busy = False
        self.paused = False
        self.ctx.log_info(
            f"趋势回调启动：{self.tf}，EMA{self.p.trend} 趋势过滤，RSI{self.p.rsi_n}<{self.p.rsi_in:g} 入场，"
            f"止损 {self.p.stop_atr:g}ATR，{self.p.tp1_atr:g}ATR 先卖 {self.p.tp1_frac:.0%}；"
            f"单笔风险 {float(p['risk_pct']):g}% 权益，最多投入 {float(p['max_alloc_pct']):g}%"
            + ("；恢复已有持仓" if self.pos else ""))

    # ------------------------------------------------------------------ 主循环
    async def on_tick(self, tick: Tick) -> None:
        if self.paused or self.busy:
            return
        self.busy = True
        try:
            price = float(tick.price)
            if self.pos and price > 0:
                await self._intrabar(price)
            bar_close = int(time.time()) // self.tf_sec * self.tf_sec
            if bar_close > self.last_bar:
                await self._on_bar(bar_close)
        finally:
            self.busy = False

    async def _intrabar(self, price: float) -> None:
        pos = self.pos
        if price <= pos["stop"]:
            reason = "保本止损" if pos["took_tp1"] else "止损"
            self.ctx.log_warn(f"{reason}：价格 {price} ≤ {pos['stop']:.8g}，全部卖出")
            await self._sell(pos["amount"], reason)
        elif not pos["took_tp1"] and price >= pos["tp1"]:
            qty = pos["amount"] * self.p.tp1_frac
            if qty * price < MIN_NOTIONAL:
                qty = pos["amount"]   # 一半不够最小下单额就整笔止盈
            self.ctx.log_info(f"第一目标 {pos['tp1']:.8g} 到达，卖出 {qty:.8g}，止损抬到保本 {pos['cost']:.8g}")
            pos["took_tp1"] = True
            pos["stop"] = max(pos["stop"], pos["cost"])
            await self._sell(qty, "第一目标")

    async def _on_bar(self, bar_close: int) -> None:
        klines = await self.ctx.get_klines(timeframe=self.tf, limit=self.p.warmup + 50)
        # 只用已收盘的 K 线：开盘时间 + 周期 ≤ 现在
        closed = [k for k in klines if int(k[0]) // 1000 + self.tf_sec <= bar_close]
        self.last_bar = bar_close
        await self.ctx.state.set("tp.last_bar", bar_close)
        if len(closed) < self.p.warmup:
            self.ctx.log_info(f"K 线不足 {self.p.warmup} 根（现有 {len(closed)}），等待")
            return
        H, L, C = [float(k[2]) for k in closed], [float(k[3]) for k in closed], [float(k[4]) for k in closed]
        s = snapshot(H, L, C, self.p)
        if s is None:
            return
        await self._report(s, bar_close)
        if self.pos:
            held = (bar_close - int(self.pos["entry_bar_ts"])) // self.tf_sec
            reason = should_exit(s, self.p, took_tp1=self.pos["took_tp1"], bars_held=held)
            if reason:
                self.ctx.log_info(f"离场：{reason}")
                await self._sell(self.pos["amount"], reason)
        elif should_enter(s, self.p):
            await self._enter(s.close, s.atr, bar_close)

    async def _report(self, s, bar_close: int) -> None:
        """每根 K 线写一行判断结果，并存进 ctx.state 供界面"策略状态"展示（空仓不再无声无息）。"""
        nxt = time.strftime("%m-%d %H:%M", time.localtime(bar_close + self.tf_sec))
        rsi_txt = f"{s.rsi:.1f}" if s.rsi is not None else "-"
        if self.pos:
            msg = f"持仓中 · 收盘 {s.close:g} · 止损 {self.pos['stop']:g} · 下一根 K 线 {nxt}"
        elif not s.up:
            msg = f"等待：趋势向下，不开仓 · 收盘 {s.close:g} · RSI {rsi_txt} · 下一根 K 线 {nxt}"
        else:
            gap = (s.rsi - self.p.rsi_in) if s.rsi is not None else None
            need = f"，还差 {gap:.1f}" if gap is not None and gap > 0 else ""
            msg = f"等待：趋势向上，RSI {rsi_txt}（入场需 <{self.p.rsi_in:g}{need}）· 收盘 {s.close:g} · 下一根 K 线 {nxt}"
        self.ctx.log_info(msg)
        await self.ctx.state.set("ui.status", {"summary": msg, "next_at": bar_close + self.tf_sec, "at": bar_close})

    # ------------------------------------------------------------------ 下单
    async def _enter(self, price: float, atr_value: float, bar_ts: int) -> None:
        bal = await self.ctx.fetch_balance()
        cash = float((bal.get(QUOTE) or {}).get("free") or 0)
        base = self.ctx.symbol.split("/")[0]
        held = float((bal.get(base) or {}).get("total") or 0)
        equity = cash + held * price
        p = self.ctx.params
        quote = position_quote(equity, cash, price, atr_value, self.p, risk_pct=float(p["risk_pct"]) / 100,
                               max_alloc=float(p["max_alloc_pct"]) / 100, min_notional=MIN_NOTIONAL)
        if quote <= 0:
            self.ctx.log_warn(f"出现入场信号，但可用资金 {cash:.2f} {QUOTE} 按风险算出的仓位不足 "
                              f"{2 * MIN_NOTIONAL:g} {QUOTE}（交易所最小下单额×2），跳过")
            return
        amount = round(quote / price, 8)
        order = await self.ctx.buy(amount, type_="market")
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"买入失败：{order.reason}")
            return
        fill = float(order.filled_price or price)
        qty = float(order.filled_amount or amount)
        self.pos = {"id": f"tp-{bar_ts}", "amount": qty, "entry_price": fill,
                    "stop": fill - self.p.stop_atr * atr_value, "tp1": fill + self.p.tp1_atr * atr_value,
                    "took_tp1": False, "entry_bar_ts": bar_ts, "cost": fill * 1.002}   # 含买卖两边手续费的保本价
        await self.ctx.state.set("tp.pos", self.pos)
        self.ctx.log_info(f"回调买入 {qty:.8g} @ {fill:.8g}（投入约 {quote:.2f} {QUOTE}），"
                          f"止损 {self.pos['stop']:.8g}，第一目标 {self.pos['tp1']:.8g}")

    async def _sell(self, qty: float, reason: str) -> None:
        if not self.pos or qty <= 0:
            return
        order = await self.ctx.sell(round(min(qty, self.pos["amount"]), 8), type_="market")
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"{reason}卖出失败：{order.reason}")
            return
        sold = float(order.filled_amount or qty)
        self.pos["amount"] = max(0.0, self.pos["amount"] - sold)
        if self.pos["amount"] * float(order.filled_price or self.pos["entry_price"]) < MIN_NOTIONAL:
            self.pos = None   # 剩余不足最小下单额，视为已平
        await self.ctx.state.set("tp.pos", self.pos)

    async def on_order(self, order: OrderView) -> None:
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"订单 {order.client_order_id} {order.status}：{order.reason}")

    async def on_stop(self) -> None:
        await self.ctx.state.set("tp.pos", self.pos)
        self.ctx.log_info("趋势回调停止" + ("，持仓状态已保存" if self.pos else ""))

    # ------------------------------------------------------------------ 能力
    async def list_positions(self) -> list[dict[str, Any]]:
        if not self.pos:
            return []
        t = self.ctx.get_ticker() or {}
        mark = t.get("price") or t.get("last")
        entry = self.pos["entry_price"]
        return [{"id": self.pos["id"], "symbol": self.ctx.symbol, "side": "long", "amount": self.pos["amount"],
                 "entry_price": entry, "mark_price": mark,
                 "unrealized_pnl": (mark - entry) * self.pos["amount"] if mark else None,
                 "unrealized_pnl_pct": (mark / entry - 1) * 100 if mark else None,
                 "label": "已止盈一半" if self.pos["took_tp1"] else "持仓"}]

    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        if not self.pos or self.pos["id"] != position_id:
            raise ValueError(f"持仓 {position_id} 不存在")
        await self._sell(self.pos["amount"] if amount is None else float(amount), "手动平仓")
        return {"position_id": position_id}

    async def close_positions(self, position_ids: list[str]) -> Any:
        return [await self.close_position(pid) for pid in position_ids]

    async def close_all_positions(self) -> Any:
        return await self.close_positions([self.pos["id"]]) if self.pos else []

    async def pause(self) -> None:
        self.paused = True
        self.ctx.log_info("已暂停：不开新仓、不检查止损（已有持仓保留）")

    async def resume(self) -> None:
        self.paused = False
        self.ctx.log_info("已恢复")


STRATEGY = TrendPullbackStrategy
