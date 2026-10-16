"""行情中心：同一 symbol 只订阅一次，一份行情喂所有实例；延迟检测（《引擎设计》行情中心一节）。"""
from __future__ import annotations

import asyncio
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
        self._markets: dict[str, Any] = {}
        self._source: Callable[[str], Awaitable[dict[str, Any]]] | None = None
        self.poll_interval = 1.0
        self.stale_threshold = float(settings.engine.get("market_stale_threshold", 5))
        self.use_ws = False

    # ---- 行情源 ----
    def _rest(self) -> Any:
        if self._client is None:
            self._client = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        return self._client

    def set_source(self, source: Callable[[str], Awaitable[dict[str, Any]]] | None,
                   markets: dict[str, Any] | None = None) -> None:
        """注入行情源（离线/测试）。"""
        self._source = source
        if markets is not None:
            self._markets = markets

    async def markets(self) -> dict[str, Any]:
        if not self._markets:
            try:
                self._markets = await self._rest().load_markets()
            except Exception as exc:  # noqa: BLE001
                log.warning("加载交易对失败：%s", exc)
                return {}
        return self._markets

    async def is_valid_symbol(self, symbol: str) -> bool:
        markets = await self.markets()
        if not markets:
            return "/" in symbol
        m = markets.get(symbol)
        return bool(m) and m.get("spot", True) and m.get("active", True) is not False

    async def fetch(self, symbol: str) -> dict[str, Any]:
        if self._source is not None:
            tick = await self._source(symbol)
        else:
            raw = await self._rest().fetch_ticker(symbol)
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
        return await self._rest().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    async def orderbook(self, symbol: str, depth: int = 20) -> dict[str, Any]:
        if self._source is not None:
            tick = await self.ticker(symbol)
            p = tick["price"]
            return {"symbol": symbol, "bids": [[p * 0.999, 1.0]], "asks": [[p * 1.001, 1.0]], "ts": tick["ts"]}
        book = await self._rest().fetch_order_book(symbol, limit=depth)
        return {"symbol": symbol, "bids": book["bids"][:depth], "asks": book["asks"][:depth],
                "ts": int(book.get("timestamp") or 0)}

    # ---- 订阅 ----
    def subscribe(self, symbol: str, name: str, handler: TickHandler) -> None:
        self._subscribers[symbol][name] = handler
        if symbol not in self._tasks or self._tasks[symbol].done():
            self._tasks[symbol] = asyncio.create_task(self._pump(symbol), name=f"market:{symbol}")

    def unsubscribe(self, symbol: str, name: str) -> None:
        self._subscribers[symbol].pop(name, None)
        if not self._subscribers[symbol]:
            task = self._tasks.pop(symbol, None)
            if task is not None:
                task.cancel()

    def subscriptions(self) -> dict[str, int]:
        return {s: len(h) for s, h in self._subscribers.items() if h}

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
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        if self._client is not None:
            await self._client.close()
            self._client = None


market = MarketHub()
