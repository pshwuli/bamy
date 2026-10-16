"""策略与插件的基类、上下文与拦截结论。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---- 拦截型钩子的返回值 ----
ALLOW = "ALLOW"


@dataclass(frozen=True)
class _Decision:
    kind: str
    reason: str | None = None
    payload: dict[str, Any] | None = None


def DENY(reason: str = "") -> _Decision:
    return _Decision("DENY", reason=reason)


def MODIFY(payload: dict[str, Any]) -> _Decision:
    return _Decision("MODIFY", payload=payload)


class LoggerMixin:
    """ctx.log_* 的实现，写入 system_logs 与进程日志。"""

    def __init__(self) -> None:
        self._sink: Any = None

    def _emit(self, level: str, message: str) -> None:  # pragma: no cover - 由引擎注入
        if self._sink is not None:
            self._sink(level, message)

    def log(self, message: str, level: str = "info") -> None:
        self._emit(level, message)

    def log_info(self, message: str) -> None:
        self._emit("info", message)

    def log_warn(self, message: str) -> None:
        self._emit("warn", message)

    def log_error(self, message: str) -> None:
        self._emit("error", message)


@dataclass
class Tick:
    symbol: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    ts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "price": self.price, "bid": self.bid, "ask": self.ask,
                "volume": self.volume, "ts": self.ts}


@dataclass
class OrderView:
    """策略看到的订单视图（只读）。"""
    id: int | None = None
    client_order_id: str = ""
    exchange_order_id: str | None = None
    symbol: str = ""
    side: str = "buy"
    type: str = "market"
    amount: float = 0.0
    price: float | None = None
    filled_amount: float = 0.0
    filled_price: float | None = None
    status: str = "pending"
    reason: str | None = None
    source: str = "strategy"
    is_guard: bool = False


class StateStore:
    """实例级 / 绑定级状态存储。"""

    def __init__(self, loader, saver, deleter):
        self._loader = loader
        self._saver = saver
        self._deleter = deleter

    async def get(self, key: str, default: Any = None) -> Any:
        raw = await self._loader(key)
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return raw

    async def set(self, key: str, value: Any) -> None:
        await self._saver(key, json.dumps(value, ensure_ascii=False, default=str))

    async def delete(self, key: str) -> None:
        await self._deleter(key)


class BaseStrategy(LoggerMixin):
    """策略基类：实例方法由框架调度，不得并发。"""

    capabilities: dict[str, bool] = {}
    meta: dict[str, Any] = {}

    def __init__(self) -> None:
        super().__init__()
        self.ctx: Any = None

    # ---- 生命周期 ----
    async def on_start(self) -> None:  # pragma: no cover - 由策略实现
        ...

    async def on_tick(self, tick: Tick) -> None:  # pragma: no cover
        ...

    async def on_order(self, order: OrderView) -> None:
        return None

    async def on_stop(self) -> None:
        return None

    async def on_pause(self) -> None:
        return None

    async def on_resume(self) -> None:
        return None

    # ---- 能力对应的方法，声明了能力才需要实现 ----
    async def close_position(self, position_id: str, amount: float | None = None) -> Any:
        raise NotImplementedError

    async def close_positions(self, position_ids: list[str]) -> Any:
        raise NotImplementedError

    async def close_all_positions(self) -> Any:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> Any:
        raise NotImplementedError

    async def cancel_all_orders(self) -> Any:
        raise NotImplementedError

    async def list_positions(self) -> list[dict[str, Any]]:
        return []

    async def pause(self) -> None:
        return None

    async def resume(self) -> None:
        return None

    async def reset(self) -> None:
        return None


class BasePlugin(LoggerMixin):
    """插件基类：按绑定实例化，框架不提供任何下单接口。"""

    meta: dict[str, Any] = {}

    def __init__(self) -> None:
        super().__init__()
        self.ctx: Any = None

    async def on_bind(self) -> None:
        return None

    async def on_settings(self) -> None:
        return None

    async def on_unbind(self) -> None:
        return None
