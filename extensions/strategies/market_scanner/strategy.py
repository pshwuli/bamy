"""全市场动量扫描：扫描全部 USDT 交易对，满足"近 N 小时涨幅 + 贴近高点 + 成交额"才开仓。

- 扫描：引擎每约 5 秒推一个 ALL/USDT 的扫描 tick，本策略每 scan_seconds 秒做一轮粗筛→细筛
- 持仓：持有的币由引擎单独订阅 1 秒行情，tick.symbol 为该币，用于止盈/止损/到期检查
- 现货只做多；U 本位合约按 direction 做多/做空/双向

ctx.state 键：
- ms.pos   {symbol: {side, amount, entry, best, armed, confirm, opened_at}}
- ms.cool  {symbol: 冷却到期时间戳}
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "AEUR", "USDE", "USD1", "PYUSD", "BFUSD",
           "EURI", "RLUSD", "USDS", "XUSD", "UST", "USTC", "PAXG", "WBTC", "WBETH", "BNSOL"}
LEVERAGED_BASES = {"BTC", "ETH", "BNB", "XRP", "ADA", "DOT", "LINK", "TRX", "XLM", "LTC", "EOS", "FIL", "SXP",
                   "YFI", "UNI", "AAVE", "SUSHI", "1INCH", "BCH", "XTZ"}
MIN_NOTIONAL = 6.0           # 币安现货/合约多数交易对最小下单额 5 USDT，留一点余量
LOG_EVERY = 120              # 没有新动作时，扫描结果最多每 2 分钟写一行日志
SIDE_TXT = {"long": "多", "short": "空"}


def _base(symbol: str) -> str:
    return symbol.split("/")[0]


def excluded_base(base: str, extra: set[str]) -> bool:
    if base in STABLES or base in extra:
        return True
    if base.endswith(("BULL", "BEAR", "DOWN")):
        return True
    return base.endswith("UP") and base[:-2] in LEVERAGED_BASES


def pick(stats: dict[str, dict[str, float]], p: dict[str, Any], sides: list[str]):
    """细筛：返回 (满足条件的 [(symbol, side, change, dist)] 按强度排序, 离门槛最近的 (symbol, side, change, dist, gap))。"""
    need_chg = float(p["min_change_pct"])
    need_dist = float(p["max_from_high_pct"])       # 负数，如 -5
    hits, near = [], None
    for sym, w in stats.items():
        last, chg = w.get("last") or 0, w.get("change_pct") or 0
        if last <= 0:
            continue
        for side in sides:
            if side == "long":
                hi = w.get("high") or last
                dist = (last / hi - 1) * 100 if hi else 0
                gap = max(0.0, need_chg - chg) + max(0.0, need_dist - dist)
            else:
                lo = w.get("low") or last
                dist = (last / lo - 1) * 100 if lo else 0      # 离最低价的距离（正数）
                gap = max(0.0, need_chg + chg) + max(0.0, dist + need_dist)
            if gap <= 0:
                hits.append((sym, side, chg, dist))
            elif near is None or gap < near[4]:
                near = (sym, side, chg, dist, gap)
    hits.sort(key=lambda x: abs(x[2]), reverse=True)
    return hits, near


class MarketScannerStrategy(BaseStrategy):
    """全市场动量扫描。"""

    async def on_start(self) -> None:
        p = self.ctx.params
        self.p = p
        self.futures = self.ctx.is_futures
        d = p.get("direction", "long")
        self.sides = ["long"] if not self.futures or d == "long" else ["short"] if d == "short" else ["long", "short"]
        self.window = p.get("window", "4h")
        self.exclude = {x.strip().upper() for x in str(p.get("exclude") or "").split(",") if x.strip()}
        self.pos: dict[str, dict[str, Any]] = await self.ctx.state.get("ms.pos", {}) or {}
        self.cool: dict[str, float] = await self.ctx.state.get("ms.cool", {}) or {}
        self.last_scan = 0.0
        self.scan_task: asyncio.Task | None = None
        self.stats: dict[str, dict[str, float]] = {}
        self.stats_head = ""
        self.last_log = 0.0
        self.last_msg = ""
        self.paused = False
        self.lev = int(p.get("leverage") or 1) if self.futures else 1
        if self.futures and self.ctx.paper:
            await self.ctx.set_leverage(self.lev)
        self.watched = set(self.pos)
        mkt = "U 本位合约" if self.futures else "现货"
        self.ctx.log_info(
            f"全市场扫描启动（{mkt}，方向 {'/'.join(SIDE_TXT[s] for s in self.sides)}）：成交额 ≥{float(p['min_quote_volume']):g} 万U，"
            f"{self.window} 涨幅 ≥{float(p['min_change_pct']):g}%，距{self.window}高 ≥{float(p['max_from_high_pct']):g}%；"
            f"摸 +{float(p['arm_pct']):g}% 后回撤 {float(p['trail_pct']):g}%（{int(p['confirm_ticks'])} 次确认）止盈，"
            f"止损 -{float(p['stop_pct']):g}%，{float(p['max_hold_hours']):g}h 到期，冷却 {float(p['cooldown_hours']):g}h，"
            f"槽位 {int(p['slots'])}" + (f"，杠杆 {self.lev}x 逐仓" if self.futures else "")
            + (f"；恢复持仓 {'、'.join(self.pos)}" if self.pos else ""))

    # ------------------------------------------------------------------ 主循环
    async def on_tick(self, tick: Tick) -> None:
        if self.paused:
            return
        if tick.symbol.startswith("ALL/"):
            for sym in list(self.pos):           # 兜底：持仓币的单独行情断了也能按缓存价检查
                t = self.ctx.get_ticker(sym)
                if t and t.get("price"):
                    await self._manage(sym, float(t["price"]))
            if self.scan_task is not None and self.scan_task.done():
                exc = self.scan_task.exception()
                self.scan_task = None
                if exc is not None:
                    self.ctx.log_warn(f"扫描失败：{exc!r}")
                else:
                    await self._decide()       # 下单在 tick 里做（持有实例锁），扫描本身在后台跑
            if self.scan_task is None and time.time() - self.last_scan >= float(self.p["scan_seconds"]):
                self.last_scan = time.time()
                # 合约要逐个拉 K 线算 4h 涨幅，可能要十几秒：放后台，不挡持仓的每秒止盈止损
                self.scan_task = asyncio.create_task(self._scan())
        elif tick.symbol in self.pos:
            await self._manage(tick.symbol, float(tick.price))

    async def _scan(self) -> None:
        """后台：拉全市场行情 + 候选币窗口统计，结果留给 _decide。"""
        p = self.p
        now = time.time()
        tickers = await self.ctx.all_tickers()
        min_qv = float(p["min_quote_volume"]) * 10000
        self.cool = {k: v for k, v in self.cool.items() if v > now}
        cands = [s for s, t in tickers.items()
                 if (t.get("quote_volume") or 0) >= min_qv and not excluded_base(_base(s), self.exclude)
                 and s not in self.pos and s not in self.cool]
        self.stats_head = f"粗筛：{len(tickers)} 个 → {len(cands)} 个候选"
        full = len(self.pos) >= int(p["slots"])
        self.stats = {} if full or not cands else await self.ctx.window_stats(cands, self.window)

    async def _decide(self) -> None:
        p = self.p
        now = time.time()
        head = self.stats_head
        slots = int(p["slots"])
        if len(self.pos) >= slots:
            held = " | ".join(self._pos_text(s) for s in self.pos)
            await self._status(f"{head} | 槽位已满（{len(self.pos)}/{slots}）：{held}")
            return
        stats = {k: v for k, v in self.stats.items() if k not in self.pos and self.cool.get(k, 0) <= now}
        hits, near = pick(stats, p, self.sides)
        opened = False
        for sym, side, chg, dist in hits:
            if len(self.pos) >= slots:
                break
            what = "距高" if side == "long" else "距低"
            self.ctx.log_info(f"{head} | 命中 {sym} {self.window}{chg:+.1f}% {what}{dist:+.1f}%，开{SIDE_TXT[side]}")
            if await self._open(sym, side, float(stats[sym]["last"])):
                opened = True
        if opened:
            self.last_log = now
            await self._status(f"{head} | 持有 {'、'.join(self.pos)}", log=False)
            return
        if near is not None:
            sym, side, chg, dist, gap = near
            what = "距高" if side == "long" else "距低"
            msg = f"{head} | 空槽等待：无满足标的 | 离门槛最近 {sym} {self.window}{chg:+.1f}% {what}{dist:+.1f}%（还差 {gap:.1f}pp）"
        else:
            msg = f"{head} | 空槽等待：无满足标的"
        await self._status(msg)

    async def _status(self, msg: str, log: bool = True) -> None:
        now = time.time()
        if log and (now - self.last_log >= LOG_EVERY or msg.split("|")[1:2] != self.last_msg.split("|")[1:2]):
            self.ctx.log_info(msg)
            self.last_log = now
        self.last_msg = msg
        nxt = now + float(self.p["scan_seconds"])
        await self.ctx.state.set("ui.status", {"summary": msg, "at": int(now), "next_at": int(nxt)})

    def _pos_text(self, sym: str) -> str:
        pos = self.pos[sym]
        t = self.ctx.get_ticker(sym) or {}
        px = float(t.get("price") or pos["entry"])
        pnl = (px / pos["entry"] - 1) * 100 * (1 if pos["side"] == "long" else -1)
        return f"{sym} {SIDE_TXT[pos['side']]} 浮盈 {pnl:+.2f}%" + ("（已摸止盈线）" if pos["armed"] else "")

    # ------------------------------------------------------------------ 开平仓
    async def _equity_and_free(self) -> tuple[float, float]:
        """
        返回 (实例级权益, 账号可用余额)
        - equity: 本实例的初始资金 + 本实例的浮动盈亏
        - free: 账号级可用余额（用于校验是否足够下单）
        """
        bal = await self.ctx.fetch_balance()
        usdt = bal.get("USDT") or {}
        account_free = float(usdt.get("free") or 0)

        # 获取实例初始资金
        from backend.bamy.services.live_equity import live_capital
        from backend.bamy.db import session_scope
        from backend.bamy.models import Instance
        async with session_scope() as db:
            inst = await db.get(Instance, self.ctx.instance_id)
            initial = live_capital(inst) if inst else 0.0

        # 计算本实例的浮动盈亏
        unrealized = 0.0
        for sym, pos in self.pos.items():
            t = self.ctx.get_ticker(sym) or {}
            px = float(t.get("price") or pos["entry"])
            if self.futures:
                unrealized += (px - pos["entry"]) * pos["amount"] * (1 if pos["side"] == "long" else -1)
            else:
                # 现货持仓价值 - 初始成本
                unrealized += (px - pos["entry"]) * pos["amount"]

        # 实例级权益 = 初始资金 + 浮动盈亏
        instance_equity = initial + unrealized if initial else 0.0

        return instance_equity, account_free

    async def _open(self, sym: str, side: str, price: float) -> bool:
        equity, free = await self._equity_and_free()
        quote = min(equity * float(self.p["position_pct"]) / 100, free * 0.98)
        notional = quote * self.lev
        if notional < MIN_NOTIONAL:
            self.ctx.log_warn(f"想开 {sym}，但可用资金 {free:.2f} USDT 不够最小下单额，跳过")
            return False
        if self.futures and not self.ctx.paper:
            try:
                await self.ctx.set_margin_mode("isolated", sym)
                await self.ctx.set_leverage(self.lev, sym)
            except Exception as exc:  # noqa: BLE001
                self.ctx.log_warn(f"{sym} 设置逐仓/杠杆失败：{exc}，跳过")
                return False
        amount = notional / price
        amount = float(f"{amount:.8g}")
        order = await (self.ctx.buy if side == "long" else self.ctx.sell)(amount, symbol=sym, reduce_only=False)
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"{sym} 开{SIDE_TXT[side]}失败：{order.reason}")
            self.cool[sym] = time.time() + 600     # 下单失败的币 10 分钟内不再试
            return False
        fill = float(order.filled_price or price)
        qty = float(order.filled_amount or amount)
        self.pos[sym] = {"side": side, "amount": qty, "entry": fill, "best": fill, "armed": False, "confirm": 0,
                         "opened_at": time.time()}
        self.watched = set(self.pos)
        await self._save()
        stop = fill * (1 - float(self.p["stop_pct"]) / 100) if side == "long" else fill * (1 + float(self.p["stop_pct"]) / 100)
        self.ctx.log_info(f"开{SIDE_TXT[side]} {sym} {qty:.8g} @ {fill:.8g}（{'保证金' if self.futures else '投入'}约 {quote:.2f} USDT"
                          + (f"，{self.lev}x" if self.futures else "") + f"），止损 {stop:.8g}")
        return True

    async def _manage(self, sym: str, px: float) -> None:
        pos = self.pos.get(sym)
        if pos is None or px <= 0:
            return
        p = self.p
        long_ = pos["side"] == "long"
        pnl = (px / pos["entry"] - 1) * 100 * (1 if long_ else -1)
        pos["best"] = max(pos["best"], px) if long_ else min(pos["best"], px)
        if pnl <= -float(p["stop_pct"]):
            return await self._close(sym, f"止损（{pnl:+.2f}%）")
        if time.time() - pos["opened_at"] >= float(p["max_hold_hours"]) * 3600:
            return await self._close(sym, f"到期 {float(p['max_hold_hours']):g}h（{pnl:+.2f}%）")
        if not pos["armed"] and pnl >= float(p["arm_pct"]):
            pos["armed"] = True
            self.ctx.log_info(f"{sym} 浮盈 {pnl:+.2f}% 摸到 +{float(p['arm_pct']):g}%，开始跟踪回撤 {float(p['trail_pct']):g}%")
            await self._save()
        if pos["armed"]:
            back = (1 - px / pos["best"]) * 100 if long_ else (px / pos["best"] - 1) * 100
            if back >= float(p["trail_pct"]):
                pos["confirm"] += 1
                if pos["confirm"] >= int(p["confirm_ticks"]):
                    return await self._close(sym, f"移动止盈：从{'最高' if long_ else '最低'} {pos['best']:.8g} 回撤 {back:.2f}%（{pnl:+.2f}%）")
            else:
                pos["confirm"] = 0

    async def _close(self, sym: str, reason: str) -> None:
        pos = self.pos.get(sym)
        if pos is None:
            return
        qty = pos["amount"]
        if not self.futures:
            bal = await self.ctx.fetch_balance()
            held = float((bal.get(_base(sym)) or {}).get("free") or 0)
            qty = min(qty, held) if held > 0 else qty
        order = await (self.ctx.sell if pos["side"] == "long" else self.ctx.buy)(
            float(f"{qty:.8g}"), symbol=sym, reduce_only=True)
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"{sym} {reason}，平仓失败：{order.reason}")
            if "持仓不足" in str(order.reason) or "42244" in str(order.reason) or "没有可平" in str(order.reason):
                self._forget(sym)   # 仓位已经不在了（被强平或手动卖掉），不再反复尝试
                await self._save()
            return
        fill = float(order.filled_price or pos["entry"])
        pnl = (fill / pos["entry"] - 1) * 100 * (1 if pos["side"] == "long" else -1)
        self.ctx.log_info(f"平{SIDE_TXT[pos['side']]} {sym} @ {fill:.8g}：{reason}，本笔 {pnl:+.2f}%"
                          + (f"（保证金 {pnl * self.lev:+.2f}%）" if self.futures and self.lev > 1 else ""))
        self._forget(sym)
        await self._save()

    def _forget(self, sym: str) -> None:
        self.pos.pop(sym, None)
        self.cool[sym] = time.time() + float(self.p["cooldown_hours"]) * 3600
        self.watched = set(self.pos)

    async def _save(self) -> None:
        await self.ctx.state.set("ms.pos", self.pos)
        await self.ctx.state.set("ms.cool", self.cool)

    async def on_order(self, order: OrderView) -> None:
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"订单 {order.symbol} {order.side} {order.status}：{order.reason}")

    async def on_stop(self) -> None:
        if self.scan_task is not None:
            self.scan_task.cancel()
        await self._save()
        self.ctx.log_info("全市场扫描停止" + (f"，持仓 {'、'.join(self.pos)} 已保存" if self.pos else ""))

    # ------------------------------------------------------------------ 能力
    async def list_positions(self) -> list[dict[str, Any]]:
        out = []
        for sym, pos in self.pos.items():
            t = self.ctx.get_ticker(sym) or {}
            mark = float(t.get("price") or pos["entry"])
            sign = 1 if pos["side"] == "long" else -1
            pnl = (mark - pos["entry"]) * pos["amount"] * sign
            out.append({"id": sym, "symbol": sym, "side": pos["side"], "amount": pos["amount"],
                        "entry_price": pos["entry"], "mark_price": mark, "unrealized_pnl": pnl,
                        "unrealized_pnl_pct": (mark / pos["entry"] - 1) * 100 * sign,
                        "leverage": self.lev if self.futures else None,
                        "label": f"{_base(sym)} {SIDE_TXT[pos['side']]}" + ("·已摸止盈线" if pos["armed"] else "")})
        return out

    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        if position_id not in self.pos:
            raise ValueError(f"持仓 {position_id} 不存在")
        await self._close(position_id, "手动平仓")
        return {"position_id": position_id}

    async def close_positions(self, position_ids: list[str]) -> Any:
        return [await self.close_position(pid) for pid in position_ids]

    async def close_all_positions(self) -> Any:
        return await self.close_positions(list(self.pos))

    async def pause(self) -> None:
        self.paused = True
        self.ctx.log_info("已暂停：不扫描、不开新仓、不检查止盈止损（已有持仓保留）")

    async def resume(self) -> None:
        self.paused = False
        self.ctx.log_info("已恢复")


STRATEGY = MarketScannerStrategy
