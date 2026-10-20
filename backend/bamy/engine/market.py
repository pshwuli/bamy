"""行情中心：同一 symbol 只订阅一次，一份行情喂所有实例；延迟检测（《引擎设计》行情中心一节）。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

import ccxt.async_support as ccxt

from bamy.core.config import settings

log = logging.getLogger("bamy.market")

TickHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MarketHub:
    """公共行情：REST 轮询（默认）或 ccxt.pro WebSocket。

    行情源可替换（测试与离线环境用 `set_source` 注入），策略代码无感知。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, dict[str, TickHandler]] = defaultdict(dict)
        self._tasks: dict[str, asyncio.Task] = {}
        self._cache: dict[str, dict[str, Any]] = {}
        self._client: Any = None
        self._fclient: Any = None                  # U 本位合约（binanceusdm），交易对形如 BTC/USDT:USDT
        self._markets: dict[str, Any] = {}
        self._fmarkets: dict[str, Any] = {}
        self._scan_subs: dict[str, dict[str, TickHandler]] = defaultdict(dict)
        self._scan_tasks: dict[str, asyncio.Task] = {}
        self._all: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
        self._win: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self.scan_interval = 5.0
        self._source: Callable[[str], Awaitable[dict[str, Any]]] | None = None
        self.poll_interval = 1.0
        self.stale_threshold = float(settings.engine.get("market_stale_threshold", 5))
        self.use_ws = False

    # ---- 行情源 ----
    def _rest(self, symbol: str | None = None) -> Any:
        if symbol is not None and ":" in symbol:
            return self._frest()
        if self._client is None:
            self._client = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        return self._client

    def _frest(self) -> Any:
        if self._fclient is None:
            self._fclient = ccxt.binanceusdm({"enableRateLimit": True})
        return self._fclient

    def set_source(self, source: Callable[[str], Awaitable[dict[str, Any]]] | None,
                   markets: dict[str, Any] | None = None) -> None:
        """注入行情源（离线/测试）。"""
        self._source = source
        if markets is not None:
            self._markets = markets

    async def markets(self, market_type: str = "spot") -> dict[str, Any]:
        if market_type == "futures":
            if self._source is not None:
                return {k: v for k, v in self._markets.items() if ":" in k}
            if not self._fmarkets:
                try:
                    self._fmarkets = await self._frest().load_markets()
                except Exception as exc:  # noqa: BLE001
                    log.warning("加载合约交易对失败：%s", exc)
                    return {}
            return self._fmarkets
        if not self._markets:
            try:
                self._markets = await self._rest().load_markets()
            except Exception as exc:  # noqa: BLE001
                log.warning("加载交易对失败：%s", exc)
                return {}
        return self._markets

    async def contract_meta(self) -> dict[str, dict[str, Any]]:
        """U 本位合约元数据：{symbol: {status, contract_type, quote, underlying, onboard_ms}}。

        来自交易所 exchangeInfo 原始字段。模拟行情源没有这些字段时返回空 dict，调用方据此跳过过滤。
        """
        out: dict[str, dict[str, Any]] = {}
        for sym, m in (await self.markets("futures")).items():
            info = m.get("info") or {}
            if not info.get("contractType"):
                continue
            out[sym] = {"status": info.get("status"), "contract_type": info.get("contractType"),
                        "quote": info.get("quoteAsset"), "underlying": info.get("underlyingType"),
                        "onboard_ms": int(info.get("onboardDate") or 0)}
        return out

    async def is_valid_symbol(self, symbol: str) -> bool:
        if is_scan_symbol(symbol):
            return True
        fut = ":" in symbol
        markets = await self.markets("futures" if fut else "spot")
        if not markets:
            return "/" in symbol
        m = markets.get(symbol)
        if not m or m.get("active", True) is False:
            return False
        return bool(m.get("swap", True)) if fut else bool(m.get("spot", True))

    async def fetch(self, symbol: str) -> dict[str, Any]:
        if self._source is not None:
            tick = await self._source(symbol)
        else:
            raw = await self._rest(symbol).fetch_ticker(symbol)
            tick = {"symbol": symbol, "price": float(raw.get("last") or 0), "bid": raw.get("bid"),
                    "ask": raw.get("ask"), "volume": raw.get("baseVolume"),
                    "ts": int(raw.get("timestamp") or time.time() * 1000)}
        tick["received_at"] = time.time()
        self._cache[symbol] = tick
        return tick

    async def ticker(self, symbol: str) -> dict[str, Any]:
        cached = self._cache.get(symbol)
        if cached and time.time() - cached["received_at"] < max(self.poll_interval * 2, 2):
            return cached
        return await self.fetch(symbol)

    def cached(self, symbol: str) -> dict[str, Any] | None:
        return self._cache.get(symbol)

    def is_stale(self, symbol: str) -> bool:
        """行情延迟超阈值 → 停止开仓。"""
        tick = self._cache.get(symbol)
        if tick is None:
            return True
        return time.time() - tick["received_at"] > self.stale_threshold

    async def klines(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> list[list[Any]]:
        if self._source is not None:
            tick = await self.ticker(symbol)
            now = int(time.time() * 1000)
            return [[now - (limit - i) * 60000, tick["price"], tick["price"], tick["price"], tick["price"], 0]
                    for i in range(limit)]
        rest = self._rest(symbol)
        if limit <= 1000:
            return await rest.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        # 币安一次最多 1000 根：从最早一段开始往后分页拉，最多 5 页
        sec = rest.parse_timeframe(timeframe)
        limit = min(limit, 5000)
        since = int(time.time() * 1000) - limit * sec * 1000
        out: list[list[Any]] = []
        while len(out) < limit:
            page = await rest.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
            page = [k for k in page if not out or k[0] > out[-1][0]]
            if not page:
                break
            out.extend(page)
            since = int(page[-1][0]) + sec * 1000
        return out[-limit:]

    async def orderbook(self, symbol: str, depth: int = 20) -> dict[str, Any]:
        if self._source is not None:
            tick = await self.ticker(symbol)
            p = tick["price"]
            return {"symbol": symbol, "bids": [[p * 0.999, 1.0]], "asks": [[p * 1.001, 1.0]], "ts": tick["ts"]}
        book = await self._rest(symbol).fetch_order_book(symbol, limit=depth)
        return {"symbol": symbol, "bids": book["bids"][:depth], "asks": book["asks"][:depth],
                "ts": int(book.get("timestamp") or 0)}

    # ---- 全市场快照（扫描类策略用） ----
    async def all_tickers(self, market_type: str = "spot", max_age: float | None = None) -> dict[str, dict[str, Any]]:
        """全部 USDT 交易对的最新行情，一次请求拉完（现货权重 80、合约 40）。结果同时写进单币缓存。"""
        max_age = self.scan_interval if max_age is None else max_age
        hit = self._all.get(market_type)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        now = time.time()
        out: dict[str, dict[str, Any]] = {}
        if self._source is not None:
            for sym in await self.markets(market_type):
                if (":" in sym) != (market_type == "futures"):
                    continue
                t = await self._source(sym)
                out[sym] = t | {"quote_volume": float(t.get("quote_volume") or (t.get("volume") or 0) * t["price"]),
                                "percentage": t.get("percentage"), "high": t.get("high"), "low": t.get("low")}
        else:
            raw = await (self._frest() if market_type == "futures" else self._rest()).fetch_tickers()
            suffix = "/USDT:USDT" if market_type == "futures" else "/USDT"
            for sym, r in raw.items():
                if not sym.endswith(suffix) or not r.get("last"):
                    continue
                out[sym] = {"symbol": sym, "price": float(r["last"]), "bid": r.get("bid"), "ask": r.get("ask"),
                            "volume": r.get("baseVolume"), "quote_volume": float(r.get("quoteVolume") or 0),
                            "percentage": r.get("percentage"), "high": r.get("high"), "low": r.get("low"),
                            "ts": int(r.get("timestamp") or now * 1000)}
        for sym, t in out.items():
            self._cache[sym] = {k: t.get(k) for k in ("symbol", "price", "bid", "ask", "volume", "ts")} | {"received_at": now}
        self._all[market_type] = (now, out)
        return out

    async def window_stats(self, symbols: list[str], window: str = "4h") -> dict[str, dict[str, float]]:
        """候选币的滚动窗口统计：{symbol: {change_pct, high, low, open, last, quote_volume}}，按币缓存 60 秒。

        现货：/api/v3/ticker?windowSize=4h，一次 100 个币（每个币权重 4）。
        合约：币安合约没有滚动窗口 ticker，用 15 分钟 K 线自己算（每个币权重 1）。
        """
        now = time.time()
        out: dict[str, dict[str, float]] = {}
        todo = []
        for sym in symbols:
            hit = self._win.get((sym, window))
            if hit and now - hit[0] < 60 and self._source is None:
                out[sym] = hit[1]
            else:
                todo.append(sym)
        if not todo:
            return out
        if self._source is not None:
            for sym in todo:
                t = await self._source(sym)
                w = {"change_pct": float(t.get("change_4h", t.get("percentage") or 0)),
                     "high": float(t.get("high_4h", t.get("high") or t["price"])),
                     "low": float(t.get("low_4h", t.get("low") or t["price"])), "last": float(t["price"]),
                     "quote_volume": float(t.get("quote_volume") or 0)}
                out[sym] = w
                self._win[(sym, window)] = (now, w)
            return out
        spot = [x for x in todo if ":" not in x]
        fut = [x for x in todo if ":" in x]
        if spot:
            rest = self._rest()
            await self.markets()
            by_id = {}
            for sym in spot:
                try:
                    by_id[rest.market(sym)["id"]] = sym
                except Exception:  # noqa: BLE001
                    continue
            ids = list(by_id)

            # 过滤非法字符（币安 /api/v3/ticker 的 symbols 参数不支持中文等字符）
            def _legal(sid: str) -> bool:
                return all(c.isupper() or c.isdigit() or c in "-._" for c in sid)

            for i in range(0, len(ids), 100):
                chunk = [x for x in ids[i:i + 100] if _legal(x)]
                if not chunk:
                    continue
                rows = await rest.public_get_ticker({"symbols": json.dumps(chunk, separators=(",", ":")),
                                                     "windowSize": window})
                for r in rows:
                    sym = by_id.get(r.get("symbol"))
                    if sym is None:
                        continue
                    w = {"change_pct": float(r["priceChangePercent"]), "high": float(r["highPrice"]),
                         "low": float(r["lowPrice"]), "open": float(r["openPrice"]), "last": float(r["lastPrice"]),
                         "quote_volume": float(r["quoteVolume"])}
                    out[sym] = w
                    self._win[(sym, window)] = (now, w)
        if fut:
            rest = self._frest()
            bars = max(1, int(rest.parse_timeframe(window) // 900))
            sem = asyncio.Semaphore(8)

            async def one(sym: str) -> None:
                async with sem:
                    try:
                        k = await rest.fetch_ohlcv(sym, timeframe="15m", limit=bars + 1)
                    except Exception:  # noqa: BLE001 - 单币失败跳过，不影响整轮扫描
                        return
                if len(k) < 2:
                    return
                k = k[-(bars + 1):]
                last = float(k[-1][4])
                opn = float(k[1][1]) if len(k) > bars else float(k[0][1])
                w = {"change_pct": (last / opn - 1) * 100 if opn else 0.0, "high": max(float(x[2]) for x in k[1:]),
                     "low": min(float(x[3]) for x in k[1:]), "open": opn, "last": last,
                     "quote_volume": sum(float(x[5]) * float(x[4]) for x in k[1:])}
                out[sym] = w
                self._win[(sym, window)] = (now, w)

            await asyncio.gather(*(one(x) for x in fut))
        return out

    def _scan_type(self, symbol: str) -> str:
        return "futures" if ":" in symbol else "spot"

    async def _scan_pump(self, key: str) -> None:
        market_type = self._scan_type(key)
        failures = 0
        while self._scan_subs.get(key):
            try:
                tickers = await self.all_tickers(market_type, max_age=self.scan_interval / 2)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if failures in (1, 10) or failures % 60 == 0:
                    log.warning("全市场行情拉取失败 %s（第 %s 次）：%s", market_type, failures, exc)
                await asyncio.sleep(min(self.scan_interval * (2 ** min(failures, 4)), 60))
                continue
            tick = {"symbol": key, "price": 0.0, "bid": None, "ask": None, "volume": float(len(tickers)),
                    "ts": int(time.time() * 1000), "scan": True}
            for name, handler in list(self._scan_subs.get(key, {}).items()):
                try:
                    await handler(tick)
                except Exception:  # noqa: BLE001
                    log.exception("全市场行情分发失败 subscriber=%s", name)
            await asyncio.sleep(self.scan_interval)

    # ---- 订阅 ----
    def subscribe(self, symbol: str, name: str, handler: TickHandler) -> None:
        if is_scan_symbol(symbol):
            self._scan_subs[symbol][name] = handler
            if symbol not in self._scan_tasks or self._scan_tasks[symbol].done():
                self._scan_tasks[symbol] = asyncio.create_task(self._scan_pump(symbol), name=f"scan:{symbol}")
            return
        self._subscribers[symbol][name] = handler
        if symbol not in self._tasks or self._tasks[symbol].done():
            self._tasks[symbol] = asyncio.create_task(self._pump(symbol), name=f"market:{symbol}")

    def unsubscribe(self, symbol: str, name: str) -> None:
        if is_scan_symbol(symbol):
            self._scan_subs[symbol].pop(name, None)
            if not self._scan_subs[symbol]:
                task = self._scan_tasks.pop(symbol, None)
                if task is not None:
                    task.cancel()
            return
        self._subscribers[symbol].pop(name, None)
        if not self._subscribers[symbol]:
            task = self._tasks.pop(symbol, None)
            if task is not None:
                task.cancel()

    def subscriptions(self) -> dict[str, int]:
        subs = {s: len(h) for s, h in self._subscribers.items() if h}
        return subs | {s: len(h) for s, h in self._scan_subs.items() if h}

    async def _pump(self, symbol: str) -> None:
        failures = 0
        while self._subscribers.get(symbol):
            try:
                tick = await self.fetch(symbol)
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 行情失败不得让任务退出
                failures += 1
                if failures in (1, 10) or failures % 60 == 0:
                    log.warning("行情拉取失败 %s（第 %s 次）：%s", symbol, failures, exc)
                await asyncio.sleep(min(self.poll_interval * (2 ** min(failures, 5)), 30))
                continue
            for name, handler in list(self._subscribers.get(symbol, {}).items()):
                try:
                    await handler(tick)
                except Exception:  # noqa: BLE001 - 单个订阅者异常隔离
                    log.exception("行情分发失败 symbol=%s subscriber=%s", symbol, name)
            await asyncio.sleep(self.poll_interval)

    async def close(self) -> None:
        for task in [*self._tasks.values(), *self._scan_tasks.values()]:
            task.cancel()
        self._tasks.clear()
        self._scan_tasks.clear()
        for attr in ("_client", "_fclient"):
            c = getattr(self, attr)
            if c is not None:
                await c.close()
                setattr(self, attr, None)


def is_scan_symbol(symbol: str | None) -> bool:
    """全市场扫描实例的占位交易对：现货 ALL/USDT，U 本位合约 ALL/USDT:USDT。"""
    return bool(symbol) and symbol.startswith("ALL/")


def scan_symbol(market_type: str) -> str:
    return "ALL/USDT:USDT" if market_type == "futures" else "ALL/USDT"


market = MarketHub()
