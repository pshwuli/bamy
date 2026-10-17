"""框架对策略包暴露的公共接口（策略里 `from bamy.strategy import ...`）。"""
from __future__ import annotations

from typing import Any

from bamy.extensions.base import (
    ALLOW, DENY, MODIFY, BaseStrategy, OrderView, StateStore, Tick,
)


class StrategyContext:
    """策略上下文：框架给策略的唯一能力入口（《策略规范》第七节）。"""

    instance_id: int
    symbol: str
    params: dict[str, Any]
    account_id: int | None
    paper: bool
    market_type: str
    state: StateStore

    # 由引擎注入实现
    _engine: Any = None

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)

    # ---- 只读属性 ----
    @property
    def name(self) -> str:
        return getattr(self, "_name", "")

    @property
    def is_scanner(self) -> bool:
        """全市场扫描实例（symbol 为 ALL/USDT 或 ALL/USDT:USDT），下单必须传 symbol。"""
        return str(self.symbol).startswith("ALL/")

    @property
    def is_futures(self) -> bool:
        return self.market_type == "futures"

    # ---- 下单 ----
    # symbol 不传 = 实例绑定的交易对；扫描实例必须传具体交易对。
    # 合约：buy 开多/平空，sell 开空/平多；reduce_only=True 只减仓不反手。
    async def buy(self, amount: float, price: float | None = None, type_: str = "market", *,
                  symbol: str | None = None, reduce_only: bool | None = None) -> OrderView:
        return await self._engine.place_order(self, "buy", amount, price, type_, symbol=symbol, reduce_only=reduce_only)

    async def sell(self, amount: float, price: float | None = None, type_: str = "market", *,
                   symbol: str | None = None, reduce_only: bool | None = None) -> OrderView:
        return await self._engine.place_order(self, "sell", amount, price, type_, symbol=symbol, reduce_only=reduce_only)

    async def cancel(self, order_id: str | int) -> Any:
        return await self._engine.cancel_order(self, order_id)

    async def cancel_all(self) -> Any:
        return await self._engine.cancel_all_orders(self)

    # ---- 持仓 ----
    async def list_positions(self) -> list[dict[str, Any]]:
        return await self._engine.list_positions(self)

    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        return await self._engine.close_position(self, position_id, amount, is_manual=False)

    async def close_positions(self, position_ids: list[str]) -> Any:
        return await self._engine.close_positions(self, position_ids, is_manual=False)

    async def close_all_positions(self) -> Any:
        return await self._engine.close_all_positions(self, is_manual=False)

    # ---- 账户 ----
    async def fetch_balance(self) -> dict[str, Any]:
        return await self._engine.fetch_balance(self)

    async def set_leverage(self, n: int, symbol: str | None = None) -> None:
        await self._engine.set_leverage(self, n, symbol)

    async def set_margin_mode(self, mode: str, symbol: str | None = None) -> None:
        await self._engine.set_margin_mode(self, mode, symbol)

    async def set_position_mode(self, mode: str) -> None:
        await self._engine.set_position_mode(self, mode)

    async def normalize_amount(self, amount: float, symbol: str | None = None) -> float:
        """把下单数量对齐到交易所的精度和最小步长，返回可直接下单的数量。

        不按精度截断会被交易所按 LOT_SIZE 拒绝（-1111 Precision is over the maximum
        defined），不同币种精度差别很大：BTC 是 1e-05、DOGE 是 1.0。
        """
        return await self._engine.normalize_amount(self, amount, symbol or self.symbol)

    # ---- 行情 ----
    def get_ticker(self, symbol: str | None = None) -> dict[str, Any] | None:
        return self._engine.get_ticker(symbol or self.symbol)

    async def get_klines(self, symbol: str | None = None, timeframe: str = "1m", limit: int = 100) -> list[list[Any]]:
        return await self._engine.get_klines(symbol or self.symbol, timeframe, limit)

    async def get_orderbook(self, symbol: str | None = None, depth: int = 20) -> dict[str, Any]:
        return await self._engine.get_orderbook(symbol or self.symbol, depth)

    async def all_tickers(self) -> dict[str, dict[str, Any]]:
        """全部 USDT 交易对的 24h 行情：{symbol: {price, quote_volume, percentage, high, low, ...}}，约 5 秒更新一次。"""
        return await self._engine.all_tickers("futures" if self.is_futures else "spot")

    async def window_stats(self, symbols: list[str], window: str = "4h") -> dict[str, dict[str, float]]:
        """候选币的滚动窗口统计（如近 4 小时涨幅、最高价）：{symbol: {change_pct, high, low, open, last, quote_volume}}。"""
        return await self._engine.window_stats(list(symbols), window)

    # ---- 日志 ----
    def log(self, message: str, level: str = "info") -> None:
        self._engine.log(self.instance_id, level, str(message))

    def log_info(self, message: str) -> None:
        self.log(message, "info")

    def log_warn(self, message: str) -> None:
        self.log(message, "warn")

    def log_error(self, message: str) -> None:
        self.log(message, "error")

    # ---- 保护 ----
    async def place_protection(self, side: str, amount: float, stop_price: float) -> OrderView:
        return await self._engine.place_protection(self, side, amount, stop_price)

    async def cancel_protection(self, protection_id: str | int) -> Any:
        return await self._engine.cancel_protection(self, protection_id)

    async def list_protections(self) -> list[OrderView]:
        return await self._engine.list_protections(self)


__all__ = ["ALLOW", "DENY", "MODIFY", "BaseStrategy", "OrderView", "StateStore", "StrategyContext", "Tick"]
