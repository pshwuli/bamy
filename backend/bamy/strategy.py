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

    # ---- 下单 ----
    async def buy(self, amount: float, price: float | None = None, type_: str = "market") -> OrderView:
        return await self._engine.place_order(self, "buy", amount, price, type_)

    async def sell(self, amount: float, price: float | None = None, type_: str = "market") -> OrderView:
        return await self._engine.place_order(self, "sell", amount, price, type_)

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

    async def set_leverage(self, n: int) -> None:
        await self._engine.set_leverage(self, n)

    async def set_margin_mode(self, mode: str) -> None:
        await self._engine.set_margin_mode(self, mode)

    async def set_position_mode(self, mode: str) -> None:
        await self._engine.set_position_mode(self, mode)

    # ---- 行情 ----
    def get_ticker(self, symbol: str | None = None) -> dict[str, Any] | None:
        return self._engine.get_ticker(symbol or self.symbol)

    async def get_klines(self, symbol: str | None = None, timeframe: str = "1m", limit: int = 100) -> list[list[Any]]:
        return await self._engine.get_klines(symbol or self.symbol, timeframe, limit)

    async def get_orderbook(self, symbol: str | None = None, depth: int = 20) -> dict[str, Any]:
        return await self._engine.get_orderbook(symbol or self.symbol, depth)

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
