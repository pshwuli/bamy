"""全市场动量扫描（U 本位永续，只做多，1 倍杠杆）。

买：24h 成交额 ≥ 500 万U 且 24h 涨幅 ≥ 3%，4h 涨幅 ≥ 20% 且距 4h 高点 ≥ -5%；多个同时满足只取成交额最大的一个，
    再等 30 秒做 4 道确认（回落 >0.5% 放弃 / 最近已收盘 1 分钟 K 线是阴线放弃 / 掉离 4h 高点 >5% 放弃）。
卖：亏 6% 立刻卖（最先判断）；涨到过 +4% 后从最高点回落 3% 卖；持满 48 小时卖。
保护：买入后立刻挂交易所止损单（买入价×0.94），涨过 +4% 后止损单上移到「最高价×0.97」。
节奏：每 15 秒检查持仓，每 60 秒全市场扫描，每 60 秒用 1 分钟 K 线补记最高价。
对账：每轮扫描前先和交易所核对持仓，核对失败本轮不买。熔断时不买新仓，已有持仓的止损止盈照常。

ctx.state 键：
- ms.pos   {symbol: {amount, entry, best, armed, opened_at, guard_id, guard_stop, adopted}}
- ms.cool  {symbol: 买入冷却到期时间戳}
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from bamy.strategy import BaseStrategy, OrderView, Tick

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "AEUR", "USDE", "USD1", "PYUSD", "BFUSD",
           "EURI", "RLUSD", "USDS", "XUSD", "UST", "USTC", "PAXG", "WBTC", "WBETH", "BNSOL"}
MIN_NOTIONAL = 6.0           # 币安合约多数交易对最小下单额 5 USDT，留一点余量
LOG_EVERY = 120              # 没有新动作时，扫描结果最多每 2 分钟写一行日志
GUARD_STEP_PCT = 0.2         # 止损单上移的最小步长（相对当前止损价），避免每秒改单
GUARD_WARN_EVERY = 300       # 止损单挂不上时，告警最多每 5 分钟一条
LISTING_MIN_HOURS = 24       # 上市不满 24 小时的不碰（首日尖峰会被误判为暴涨）
EPS = 1e-9                   # 百分比比较的容差：(95/100-1)*100 浮点结果是 -5.000000000000004，不加容差会把刚好踩线的误拒


def _base(symbol: str) -> str:
    return symbol.split("/")[0]


def eligible(sym: str, meta: dict[str, dict[str, Any]], now_ms: float, exclude: set[str]) -> bool:
    """只要 USDT 本位永续：排除已下架、上市不满 24 小时、代币化美股/商品（TRADIFI）和非 USDT 计价的。

    meta 为空（模拟行情源没有这些字段）时不做元数据过滤；meta 非空但查不到该币，按「不明」处理，不买。
    """
    base = _base(sym)
    if base in STABLES or base in exclude:
        return False
    if not meta:
        return True
    m = meta.get(sym)
    if not m or m.get("status") != "TRADING" or m.get("contract_type") != "PERPETUAL":
        return False
    if m.get("quote") != "USDT" or m.get("underlying") != "COIN":
        return False
    onboard = float(m.get("onboard_ms") or 0)
    return onboard > 0 and now_ms - onboard >= LISTING_MIN_HOURS * 3600 * 1000


def passes_core(w: dict[str, float], p: dict[str, Any]) -> tuple[bool, float, float]:
    """核心条件：窗口涨幅 ≥ min_change_pct 且距窗口高点 ≥ max_from_high_pct（负数）。返回 (是否满足, 涨幅, 距高点)。"""
    last, hi = float(w.get("last") or 0), float(w.get("high") or 0)
    chg = float(w.get("change_pct") or 0)
    dist = (last / hi - 1) * 100 if last > 0 and hi > 0 else -100.0
    return chg >= float(p["min_change_pct"]) - EPS and dist >= float(p["max_from_high_pct"]) - EPS, chg, dist

class MarketScannerStrategy(BaseStrategy):
    """全市场动量扫描。"""

    async def on_start(self) -> None:
        if not self.ctx.is_futures:
            raise ValueError("本策略只支持 U 本位永续合约")
        p = self.ctx.params
        self.p = p
        self.exclude = {x.strip().upper() for x in str(p.get("exclude") or "").split(",") if x.strip()}
        self.pos: dict[str, dict[str, Any]] = await self.ctx.state.get("ms.pos", {}) or {}
        self.cool: dict[str, float] = await self.ctx.state.get("ms.cool", {}) or {}
        self.retry_at: dict[str, float] = {}       # 平仓失败后的退避，只放内存，和买入冷却分开
        self.guard_warned: dict[str, float] = {}
        self.t_scan = self.t_manage = self.t_peak = 0.0
        self.scan_task: asyncio.Task | None = None
        self.pending: dict[str, Any] | None = None  # 确认通过、等 tick 里下单的信号
        self.synced = False                          # 本轮对账是否成功
        self.breaker_until = 0.0
        self.last_log = 0.0
        self.last_msg = ""
        self.paused = False
        self.slots = int(p["slots"])
        self.mode = await self.ctx.position_mode()
        if self.ctx.paper:
            await self.ctx.set_leverage(1)
        self.watched = set(self.pos)
        self.ctx.log_info(
            f"全市场扫描启动（U 本位永续，只做多，1 倍）：成交额 ≥{float(p['min_quote_volume']):g} 万U 且 24h ≥{float(p['min_change_24h']):g}%，"
            f"{p['window']} 涨幅 ≥{float(p['min_change_pct']):g}% 距高点 ≥{float(p['max_from_high_pct']):g}%；"
            f"每次 {float(p['position_pct']):g}% 权益，最多同时 {self.slots} 个；止损 -{float(p['stop_pct']):g}%，+{float(p['arm_pct']):g}% 后回落 "
            f"{float(p['trail_pct']):g}% 卖，{float(p['max_hold_hours']):g}h 到期，冷却 {float(p['cooldown_hours']):g}h"
            + (f"；恢复持仓 {'、'.join(self.pos)}" if self.pos else ""))

    # ------------------------------------------------------------------ 节奏
    async def on_tick(self, tick: Tick) -> None:
        if tick.symbol.startswith("ALL/"):
            await self._cycle()
        elif tick.symbol in self.pos:
            self._touch(tick.symbol, float(tick.price))      # 只更新最高价，卖不卖由 15 秒一次的 _manage 决定

    def _touch(self, sym: str, px: float) -> None:
        pos = self.pos.get(sym)
        if pos is None or px <= 0:
            return
        pos["best"] = max(pos["best"], px)
        if not pos["armed"] and (pos["best"] / pos["entry"] - 1) * 100 >= float(self.p["arm_pct"]) - EPS:
            pos["armed"] = True        # 冲高摸到过 +4%，哪怕两次检查之间又掉回来，也算启动

    async def _cycle(self) -> None:
        now = time.time()
        for sym in list(self.pos):                       # 兜底：持仓币的单独行情断了也按缓存价更新
            t = self.ctx.get_ticker(sym)
            if t and t.get("price"):
                self._touch(sym, float(t["price"]))
        if now - self.t_peak >= float(self.p["peak_seconds"]):
            self.t_peak = now
            await self._record_peaks()
        if now - self.t_manage >= float(self.p["manage_seconds"]):
            self.t_manage = now
            for sym in list(self.pos):
                if not self.paused:                      # 暂停期间不执行程序侧的止损、止盈、到期平仓
                    await self._manage(sym)
        if self.scan_task is not None and self.scan_task.done():
            exc = self.scan_task.exception()
            self.scan_task = None
            if exc is not None:
                self.ctx.log_warn(f"扫描失败：{exc!r}")
        if self.pending is not None:
            sig, self.pending = self.pending, None
            if len(self.pos) < self.slots and not self.paused:
                await self._open(sig["symbol"], sig["price"])
        if self.scan_task is None and now - self.t_scan >= float(self.p["scan_seconds"]):
            self.t_scan = now
            self.synced = await self._reconcile()
            if self.paused or not self.synced or len(self.pos) >= self.slots:
                return
            if now < self.breaker_until:
                await self._status("熔断中：不买新仓，持仓止损止盈照常")
                return
            self.scan_task = asyncio.create_task(self._scan())

    # ------------------------------------------------------------------ 选币与确认
    async def _scan(self) -> None:
        """后台：粗筛 → 核心条件 → 取成交额最大的一个 → 等 30 秒做 4 道确认，通过则交给 tick 下单。"""
        p = self.p
        now = time.time()
        tickers = await self.ctx.all_tickers()
        meta = await self.ctx.contract_meta()
        min_qv = float(p["min_quote_volume"]) * 10000
        self.cool = {k: v for k, v in self.cool.items() if v > now}
        cands = [s for s, t in tickers.items()
                 if (t.get("quote_volume") or 0) >= min_qv and float(t.get("percentage") or 0) >= float(p["min_change_24h"])
                 and s not in self.pos and s not in self.cool and eligible(s, meta, now * 1000, self.exclude)]
        head = f"粗筛：{len(tickers)} 个 → {len(cands)} 个候选"
        stats = await self.ctx.window_stats(cands, p["window"]) if cands else {}
        hits = []
        for sym, w in stats.items():
            ok, chg, dist = passes_core(w, p)
            if ok:
                hits.append((float(tickers[sym].get("quote_volume") or 0), sym, w, chg, dist))
        if not hits:
            await self._status(f"{head} | 无满足标的")
            return
        _, sym, w, chg, dist = max(hits)
        self.ctx.log_info(f"{head} | 命中 {len(hits)} 个，取成交额最大的 {sym}：{p['window']}{chg:+.1f}% 距高{dist:+.1f}%，进入确认")
        if await self._confirm(sym, float(w["last"]), float(w["high"])):
            self.pending = {"symbol": sym, "price": float(self.ctx.get_ticker(sym)["price"])}

    async def _confirm(self, sym: str, start: float, high: float) -> bool:
        """等 confirm_seconds 秒再看一次，4 道关卡全过才买；专门躲瞬间插针。"""
        p = self.p
        lo, end = start, start
        for _ in range(max(1, int(p["confirm_seconds"]) // 2)):
            await asyncio.sleep(2)
            t = self.ctx.get_ticker(sym)
            if t and t.get("price"):
                end = float(t["price"])
                lo = min(lo, end)
        drop = (lo / start - 1) * 100
        if drop < -float(p["confirm_drop_pct"]) - EPS:
            return self._abandon(sym, f"关卡2 等待期间回落 {drop:.2f}%")
        k = await self.ctx.get_klines(sym, "1m", 3)
        if len(k) >= 2 and float(k[-2][4]) < float(k[-2][1]):
            return self._abandon(sym, "关卡3 最近一根已收盘的 1 分钟 K 线是阴线")
        high = max(high, start, end)
        away = (end / high - 1) * 100
        if away < float(p["max_from_high_pct"]) - EPS:
            return self._abandon(sym, f"关卡4 已掉离 {p['window']} 高点 {away:.2f}%")
        return True

    def _abandon(self, sym: str, why: str) -> bool:
        self.ctx.log_info(f"放弃 {sym}：{why}")
        return False

    async def _status(self, msg: str) -> None:
        now = time.time()
        if now - self.last_log >= LOG_EVERY or msg != self.last_msg:
            self.ctx.log_info(msg)
            self.last_log = now
        self.last_msg = msg
        await self.ctx.state.set("ui.status", {"summary": msg, "at": int(now),
                                               "next_at": int(now + float(self.p["scan_seconds"]))})

    # ------------------------------------------------------------------ 对账
    async def _reconcile(self) -> bool:
        """每轮扫描前和交易所核对持仓，以交易所为准；拿不到交易所数据返回 False，本轮不买新仓。

        余额不用对：权益每次都直接从交易所取，本地没有存余额。
        """
        try:
            rows = await self.ctx.exchange_positions()
        except Exception as exc:  # noqa: BLE001
            self.ctx.log_warn(f"对账失败，本轮不买新仓：{type(exc).__name__}: {exc}")
            return False
        ex = {r["symbol"]: r for r in rows
              if str(r.get("position_side") or r.get("side") or "long") == "long" and float(r.get("amount") or 0) > 0}
        for sym, r in ex.items():
            amt, entry = float(r["amount"]), float(r.get("entry_price") or 0)
            pos = self.pos.get(sym)
            if pos is None:
                if not self.p.get("adopt_exchange_positions", True) or entry <= 0:
                    continue
                mark = float(r.get("mark_price") or entry)
                self.pos[sym] = {"amount": amt, "entry": entry, "best": max(entry, mark), "armed": False,
                                 "opened_at": time.time(), "guard_id": None, "guard_stop": 0.0, "adopted": True}
                self.ctx.log_warn(f"对账：交易所有 {sym} {amt:g} @ {entry:.8g}，本地没记录，已补记")
            elif abs(pos["amount"] - amt) > 1e-9 or (entry > 0 and abs(pos["entry"] - entry) / entry > 1e-6):
                self.ctx.log_warn(f"对账：{sym} 本地 {pos['amount']:g}@{pos['entry']:.8g} 与交易所 "
                                  f"{amt:g}@{entry:.8g} 不一致，以交易所为准")
                changed = abs(pos["amount"] - amt) > 1e-9
                pos["amount"], pos["entry"] = amt, entry or pos["entry"]
                if changed and pos.get("guard_id"):
                    await self._cancel_guard(sym)            # 止损单数量已对不上，作废后下一轮按新数量重挂
        for sym in list(self.pos):
            if sym not in ex:
                await self._recover_exit(sym)
        self.watched = set(self.pos)
        await self._save()
        return True

    async def _recover_exit(self, sym: str) -> None:
        """本地有仓、交易所没有：说明已平仓（止损单触发或手动平），从本实例的成交历史恢复这笔交易。"""
        from sqlalchemy import select

        from bamy.db.models import Order
        from bamy.db.session import session_scope

        pos = self.pos[sym]
        async with session_scope() as db:
            o = (await db.execute(select(Order).where(
                Order.instance_id == self.ctx.instance_id, Order.symbol == sym, Order.side == "sell",
                Order.status == "filled").order_by(Order.id.desc()).limit(1))).scalars().first()
        if o is not None and o.filled_price:
            fill = float(o.filled_price)
            how = "保护单触发" if o.is_guard else "已平仓"
            self.ctx.log_info(f"对账：{sym} 交易所已无持仓（{how}），按成交历史 @ {fill:.8g} 恢复，"
                              f"本笔 {(fill / pos['entry'] - 1) * 100:+.2f}%")
        else:
            self.ctx.log_warn(f"对账：{sym} 交易所已无持仓，但没找到对应的卖出成交记录，只清除本地记录")
        await self._cancel_guard(sym)
        self._forget(sym)

    async def _cancel_guard(self, sym: str) -> None:
        pos = self.pos.get(sym)
        gid = pos.get("guard_id") if pos else None
        if not gid:
            return
        try:
            await self.ctx.cancel_protection(gid)
        except Exception as exc:  # noqa: BLE001 - 已成交/已撤的止损单再撤会报错，不影响后续
            self.ctx.log_info(f"{sym} 撤止损单 {gid}：{type(exc).__name__}（可能已触发或已撤）")
        pos["guard_id"], pos["guard_stop"] = None, 0.0

    def _forget(self, sym: str) -> None:
        self.pos.pop(sym, None)
        self.retry_at.pop(sym, None)
        self.cool[sym] = time.time() + float(self.p["cooldown_hours"]) * 3600   # 同一个币卖掉后 3 小时内不再买
        self.watched = set(self.pos)

    async def _save(self) -> None:
        await self.ctx.state.set("ms.pos", self.pos)
        await self.ctx.state.set("ms.cool", self.cool)

    # ------------------------------------------------------------------ 买入与保护
    def _unrealized(self) -> float:
        total = 0.0
        for sym, pos in self.pos.items():
            t = self.ctx.get_ticker(sym) or {}
            total += (float(t.get("price") or pos["entry"]) - pos["entry"]) * pos["amount"]
        return total

    async def _equity_and_free(self) -> tuple[float, float]:
        """总权益 = 账户余额 + 持仓浮盈（余额已含被占用的保证金，所以只加浮盈，不加持仓市值）。"""
        usdt = (await self.ctx.fetch_balance()).get("USDT") or {}
        free = float(usdt.get("free") or 0)
        if self.ctx.paper:
            return float(usdt.get("total") or free) + self._unrealized(), free
        from bamy.db.models import Instance
        from bamy.db.session import session_scope
        from bamy.services.live_equity import live_capital

        async with session_scope() as db:
            inst = await db.get(Instance, self.ctx.instance_id)
            capital = live_capital(inst) if inst else None
        # 实盘账号可能被多个实例共用：设了本金就按本金算，没设退回账号余额
        return (capital if capital else float(usdt.get("total") or free)) + self._unrealized(), free

    async def _open(self, sym: str, price: float) -> bool:
        equity, free = await self._equity_and_free()
        quote = min(equity * float(self.p["position_pct"]) / 100, free * 0.98)    # 1 倍杠杆：名义额 = 保证金
        if quote < MIN_NOTIONAL:
            self.ctx.log_warn(f"想买 {sym}，但下单金额 {quote:.2f} USDT 低于最小下单额 {MIN_NOTIONAL:.0f}，跳过"
                              f"（权益 {equity:.2f}，可用 {free:.2f}）")
            return False
        if not self.ctx.paper:
            try:
                await self.ctx.set_margin_mode("isolated", sym)
                await self.ctx.set_leverage(1, sym)
            except Exception as exc:  # noqa: BLE001
                self.ctx.log_warn(f"{sym} 设置逐仓/1 倍杠杆失败：{exc}，跳过")
                return False
        amount = await self.ctx.normalize_amount(quote / price, sym)
        if amount <= 0:
            self.ctx.log_warn(f"想买 {sym}，但按交易所精度对齐后数量为 0，跳过")
            return False
        order = await self.ctx.buy(amount, symbol=sym, reduce_only=False, position_side=self._ps())
        if order.status in {"rejected", "failed"}:
            reason = str(order.reason)
            if "熔断" in reason or "40910" in reason:
                self.breaker_until = time.time() + 300
                self.ctx.log_warn(f"熔断中，不买新仓：{reason}")
            else:
                self.cool[sym] = time.time() + 600     # 下单失败的币 10 分钟内不再试
                self.ctx.log_warn(f"{sym} 买入失败：{reason}")
            return False
        fill, qty = float(order.filled_price or price), float(order.filled_amount or amount)
        self.pos[sym] = {"amount": qty, "entry": fill, "best": fill, "armed": False, "opened_at": time.time(),
                         "guard_id": None, "guard_stop": 0.0, "adopted": False}
        self.watched = set(self.pos)
        self.ctx.log_info(f"买入 {sym} {qty:.8g} @ {fill:.8g}（约 {qty * fill:.2f} USDT），止损 {fill * (1 - float(self.p['stop_pct']) / 100):.8g}")
        await self._ensure_guard(sym)
        await self._save()
        return True

    def _ps(self) -> str | None:
        """双向持仓必须带 position_side，单向持仓不能带。"""
        return "long" if self.mode == "hedge" else None

    async def _ensure_guard(self, sym: str) -> None:
        """交易所止损单：买入价×0.94；涨过 +4% 后上移到「最高价×0.97」。先挂新的再撤旧的，中间不留空档。"""
        pos = self.pos.get(sym)
        if pos is None:
            return
        p = self.p
        target = pos["best"] * (1 - float(p["trail_pct"]) / 100) if pos["armed"] \
            else pos["entry"] * (1 - float(p["stop_pct"]) / 100)
        if pos.get("guard_id") and target <= pos["guard_stop"] * (1 + GUARD_STEP_PCT / 100):
            return
        try:
            view = await self.ctx.place_protection("sell", pos["amount"], target, symbol=sym, position_side=self._ps())
        except Exception as exc:  # noqa: BLE001
            view = None
            reason = f"{type(exc).__name__}: {exc}"
        else:
            reason = str(view.reason)
        if view is None or view.status in {"rejected", "failed"}:
            if time.time() - self.guard_warned.get(sym, 0) >= GUARD_WARN_EVERY:
                self.guard_warned[sym] = time.time()
                self.ctx.log_warn(f"{sym} 止损单没挂上（{reason}），下一轮重试；期间只靠程序自己的 {float(p['stop_pct']):g}% 止损")
            return
        old = pos.get("guard_id")
        pos["guard_id"], pos["guard_stop"] = view.id, target
        if old:
            try:
                await self.ctx.cancel_protection(old)
            except Exception:  # noqa: BLE001 - 旧单可能已触发或已撤
                pass
        await self._save()

    async def _record_peaks(self) -> None:
        """每 60 秒用 1 分钟 K 线补记最高价，防止漏掉两次检查之间的冲高。"""
        for sym, pos in list(self.pos.items()):
            try:
                k = await self.ctx.get_klines(sym, "1m", 5)
            except Exception:  # noqa: BLE001
                continue
            since = pos["opened_at"] * 1000 - 60000
            for bar in k:
                if float(bar[0]) >= since:
                    self._touch(sym, float(bar[2]))
        await self._save()

    async def _manage(self, sym: str) -> None:
        """每 15 秒：止损永远最先判断 → 移动止盈 → 到期；顺带把止损单补挂/上移。"""
        pos = self.pos.get(sym)
        t = self.ctx.get_ticker(sym) or {}
        px = float(t.get("price") or 0)
        if pos is None or px <= 0 or self.retry_at.get(sym, 0) > time.time():
            return
        p = self.p
        self._touch(sym, px)
        pnl = (px / pos["entry"] - 1) * 100
        if pnl <= -float(p["stop_pct"]) + EPS:
            return await self._close(sym, f"止损（{pnl:+.2f}%）")
        if pos["armed"]:
            back = (1 - px / pos["best"]) * 100
            if back >= float(p["trail_pct"]) - EPS:
                return await self._close(sym, f"移动止盈：从最高 {pos['best']:.8g} 回落 {back:.2f}%（{pnl:+.2f}%）")
        if time.time() - pos["opened_at"] >= float(p["max_hold_hours"]) * 3600:
            return await self._close(sym, f"持满 {float(p['max_hold_hours']):g}h（{pnl:+.2f}%）")
        await self._ensure_guard(sym)

    async def _close(self, sym: str, reason: str) -> None:
        pos = self.pos.get(sym)
        if pos is None:
            return
        qty = await self.ctx.normalize_amount(pos["amount"], sym)
        if qty <= 0:
            self.ctx.log_warn(f"{sym} {reason}，持仓量低于最小下单量，无法平仓，停止跟踪")
            await self._cancel_guard(sym)
            self._forget(sym)
            await self._save()
            return
        order = await self.ctx.sell(qty, symbol=sym, reduce_only=True, position_side=self._ps())
        if order.status in {"rejected", "failed"}:
            msg = str(order.reason)
            self.ctx.log_warn(f"{sym} {reason}，平仓失败：{msg}")
            if any(k in msg for k in ("持仓不足", "42244", "没有可平", "position is zero")):
                self._forget(sym)                                    # 仓位已不在（止损单已触发），不再跟踪
            elif any(k in msg for k in ("-1021", "-1003", "429", "timeout", "Timeout", "Connection")):
                self.retry_at[sym] = time.time() + 60                 # 临时错误：退避 1 分钟
            else:
                self.retry_at[sym] = time.time() + 300                # 未知错误：退避 5 分钟，防止刷单
            await self._save()
            return
        fill = float(order.filled_price or pos["entry"])
        self.ctx.log_info(f"卖出 {sym} @ {fill:.8g}：{reason}，本笔 {(fill / pos['entry'] - 1) * 100:+.2f}%")
        await self._cancel_guard(sym)
        self._forget(sym)
        await self._save()

    # ------------------------------------------------------------------ 回调与能力
    async def on_order(self, order: OrderView) -> None:
        if order.status in {"rejected", "failed"}:
            self.ctx.log_warn(f"订单 {order.symbol} {order.side} {order.status}：{order.reason}")
            return
        pos = self.pos.get(order.symbol)
        # 交易所止损单触发：立刻记账，不等下一轮对账
        if order.is_guard and order.status == "filled" and order.side == "sell" and pos is not None:
            fill = float(order.filled_price or pos["entry"])
            self.ctx.log_info(f"止损单触发 {order.symbol} @ {fill:.8g}，本笔 {(fill / pos['entry'] - 1) * 100:+.2f}%")
            pos["guard_id"] = None
            self._forget(order.symbol)
            await self._save()

    async def on_stop(self) -> None:
        if self.scan_task is not None:
            self.scan_task.cancel()
        await self._save()
        self.ctx.log_info("全市场扫描停止" + (f"，持仓 {'、'.join(self.pos)} 已保存，交易所止损单继续有效" if self.pos else ""))

    async def list_positions(self) -> list[dict[str, Any]]:
        out = []
        for sym, pos in self.pos.items():
            mark = float((self.ctx.get_ticker(sym) or {}).get("price") or pos["entry"])
            out.append({"id": sym, "symbol": sym, "side": "long", "position_side": "long", "amount": pos["amount"],
                        "entry_price": pos["entry"], "mark_price": mark,
                        "unrealized_pnl": (mark - pos["entry"]) * pos["amount"],
                        "unrealized_pnl_pct": (mark / pos["entry"] - 1) * 100, "leverage": 1,
                        "label": f"{_base(sym)} 多" + ("·已启动移动止盈" if pos["armed"] else "")})
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
        self.ctx.log_info("已暂停：不扫描、不买新仓、不检查止损止盈和到期（已有持仓保留，交易所上的止损单仍然有效）")

    async def resume(self) -> None:
        self.paused = False
        self.ctx.log_info("已恢复")


STRATEGY = MarketScannerStrategy
