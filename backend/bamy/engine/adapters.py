"""接入适配层：币安现货 / U 本位永续合约适配器（基于 ccxt）。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import ccxt.async_support as ccxt

from bamy.core.config import settings

log = logging.getLogger("bamy.adapter")


class ExchangeError(Exception):
    def __init__(self, message: str, code: int = 50201, *, unknown: bool = False, rejected: bool = False):
        self.code = code
        self.unknown = unknown      # 状态未知，进对账队列
        self.rejected = rejected    # 明确失败
        super().__init__(message)


@dataclass
class Balance:
    asset: str
    free: float
    used: float
    total: float

    def to_dict(self) -> dict[str, Any]:
        return {"asset": self.asset, "free": self.free, "used": self.used, "total": self.total}


@dataclass
class ExchangeOrder:
    exchange_order_id: str | None
    status: str
    filled_amount: float
    filled_price: float | None
    fee: float = 0.0
    raw: dict[str, Any] | None = None
    client_order_id: str | None = None


class BinanceSpotAdapter:
    """一个账号一个实例，独立限频（《账号模块设计》隔离一节）。"""

    def __init__(self, api_key: str, api_secret: str, env: str = "mainnet", readonly: bool = False):
        self.readonly = readonly
        opts: dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True},
        }
        if env == "testnet":
            opts["options"]["sandboxMode"] = True
        self.client: ccxt.binance = ccxt.binance(opts)
        if env == "testnet":
            self.client.set_sandbox_mode(True)
        self.env = env
        self.markets: dict[str, Any] = {}

    async def close(self) -> None:
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001
            pass

    async def load_markets(self) -> dict[str, Any]:
        if not self.markets:
            self.markets = await self.client.load_markets()
        return self.markets

    # ---- 校验 ----
    async def verify(self) -> dict[str, Any]:
        """格式以外的有效性检查：连通性、密钥有效性、权限、交易类型（《账号模块设计》4.2）。"""
        try:
            await self.load_markets()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"无法连接币安：{exc}", 50203) from exc
        try:
            account = await self.client.private_get_account()
        except ccxt.AuthenticationError as exc:
            raise ExchangeError(f"密钥无效：{exc}", 42210) from exc
        except ccxt.PermissionDenied as exc:
            raise ExchangeError(f"密钥权限不足：{exc}", 42211) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"校验失败：{exc}", 50201) from exc
        can_trade = bool(account.get("canTrade", True))
        if not self.readonly and not can_trade:
            raise ExchangeError("该密钥没有现货交易权限", 42211)
        return {"can_trade": can_trade, "can_withdraw": bool(account.get("canWithdraw"))}

    async def ping(self) -> int:
        import time

        start = time.perf_counter()
        await self.client.fetch_time()
        return int((time.perf_counter() - start) * 1000)

    # ---- 行情 ----
    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        try:
            t = await self.client.fetch_ticker(symbol)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"行情拉取失败：{exc}", 50203) from exc
        return {"symbol": symbol, "price": float(t["last"] or 0), "bid": _f(t.get("bid")),
                "ask": _f(t.get("ask")), "volume": _f(t.get("baseVolume")), "ts": int(t.get("timestamp") or 0)}

    async def fetch_klines(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> list[list[Any]]:
        return await self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    async def fetch_orderbook(self, symbol: str, depth: int = 20) -> dict[str, Any]:
        book = await self.client.fetch_order_book(symbol, limit=depth)
        return {"symbol": symbol, "bids": book["bids"][:depth], "asks": book["asks"][:depth],
                "ts": int(book.get("timestamp") or 0)}

    # ---- 账户 ----
    async def fetch_balance(self) -> list[Balance]:
        raw = await self.client.fetch_balance()
        out: list[Balance] = []
        for asset, item in (raw.get("total") or {}).items():
            total = float(item or 0)
            if total <= 0:
                continue
            out.append(Balance(asset, float(raw.get("free", {}).get(asset) or 0),
                               float(raw.get("used", {}).get(asset) or 0), total))
        return out

    # ---- 精度 ----
    async def normalize(self, symbol: str, amount: float, price: float | None) -> tuple[float, float | None]:
        await self.load_markets()
        market = self.markets.get(symbol)
        if market is None:
            raise ExchangeError(f"交易对不存在：{symbol}", 42231)
        try:
            amount_n = float(self.client.amount_to_precision(symbol, amount))
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"数量精度不合法：{exc}", 42240) from exc
        price_n = None
        if price is not None:
            try:
                price_n = float(self.client.price_to_precision(symbol, price))
            except Exception as exc:  # noqa: BLE001
                raise ExchangeError(f"价格精度不合法：{exc}", 42241) from exc
        limits = (market.get("limits") or {}).get("amount") or {}
        if limits.get("min") and amount_n < limits["min"]:
            raise ExchangeError(f"数量低于最小值 {limits['min']}", 42240)
        cost_limits = (market.get("limits") or {}).get("cost") or {}
        ref_price = price_n or float((await self.fetch_ticker(symbol))["price"])
        if cost_limits.get("min") and amount_n * ref_price < cost_limits["min"]:
            raise ExchangeError("名义价值低于最小值", 42242)
        return amount_n, price_n

    # ---- 下单 ----
    async def create_order(self, symbol: str, side: str, amount: float, price: float | None,
                           type_: str = "market", client_order_id: str | None = None,
                           stop_price: float | None = None) -> ExchangeOrder:
        params: dict[str, Any] = {"newClientOrderId": client_order_id} if client_order_id else {}
        otype = type_
        if type_ in {"stop_market", "stop_limit"}:
            otype = "market" if type_ == "stop_market" else "limit"
            params["stopPrice"] = stop_price
        try:
            raw = await self.client.create_order(symbol, otype, side, amount, price, params)
        except ccxt.InsufficientFunds as exc:
            raise ExchangeError(f"余额不足：{exc}", 42243, rejected=True) from exc
        except (ccxt.InvalidOrder, ccxt.BadRequest, ccxt.OrderNotFound) as exc:
            raise ExchangeError(f"下单被拒：{exc}", 42201, rejected=True) from exc
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            raise ExchangeError(f"下单结果未知：{exc}", 50401, unknown=True) from exc
        except ccxt.ExchangeError as exc:
            # 币安明确拒绝（如 MIN_NOTIONAL、LOT_SIZE）与网络中止需要区分
            if _looks_unknown(exc):
                raise ExchangeError(f"下单结果未知：{exc}", 50202, unknown=True) from exc
            raise ExchangeError(f"下单失败：{exc}", 50201, rejected=True) from exc
        return _parse_order(raw, client_order_id)

    async def fetch_order(self, exchange_order_id: str, symbol: str) -> ExchangeOrder:
        raw = await self.client.fetch_order(exchange_order_id, symbol)
        return _parse_order(raw, raw.get("clientOrderId"))

    async def fetch_order_by_client_id(self, client_order_id: str, symbol: str) -> ExchangeOrder:
        raw = await self.client.fetch_order(None, symbol, {"origClientOrderId": client_order_id})
        return _parse_order(raw, client_order_id)

    async def cancel_order(self, exchange_order_id: str, symbol: str) -> ExchangeOrder:
        raw = await self.client.cancel_order(exchange_order_id, symbol)
        return _parse_order(raw, raw.get("clientOrderId"))

    async def fetch_open_orders(self, symbol: str | None = None) -> list[ExchangeOrder]:
        raw = await self.client.fetch_open_orders(symbol)
        return [_parse_order(o, o.get("clientOrderId")) for o in raw]


class BinanceFuturesAdapter(BinanceSpotAdapter):
    """币安 U 本位永续（ccxt.binanceusdm）：单向持仓、逐仓；交易对形如 BTC/USDT:USDT。

    与现货适配器接口一致，另加 set_leverage / set_margin_mode / fetch_positions，下单支持 reduceOnly。
    """

    def __init__(self, api_key: str, api_secret: str, env: str = "mainnet", readonly: bool = False):
        self.readonly = readonly
        opts: dict[str, Any] = {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True,
                                "options": {"adjustForTimeDifference": True}}
        self.client = ccxt.binanceusdm(opts)
        if env == "testnet":
            self.client.set_sandbox_mode(True)
        self.env = env
        self.markets = {}
        self._leverage: dict[str, int] = {}

    async def verify(self) -> dict[str, Any]:
        try:
            await self.load_markets()
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"无法连接币安合约：{exc}", 50203) from exc
        try:
            account = await self.client.fapiPrivateV2GetAccount()
        except ccxt.AuthenticationError as exc:
            raise ExchangeError(f"密钥无效或未开通合约权限：{exc}", 42210) from exc
        except ccxt.PermissionDenied as exc:
            raise ExchangeError(f"密钥没有合约权限：{exc}", 42211) from exc
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"校验失败：{exc}", 50201) from exc
        can_trade = bool(account.get("canTrade", True))
        if not self.readonly and not can_trade:
            raise ExchangeError("该密钥没有 U 本位合约交易权限", 42211)
        return {"can_trade": can_trade, "can_withdraw": bool(account.get("canWithdraw"))}

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            await self.client.set_leverage(int(leverage), symbol)
        except Exception as exc:  # noqa: BLE001
            raise ExchangeError(f"设置杠杆失败：{exc}", 42201, rejected=True) from exc
        self._leverage[symbol] = int(leverage)

    async def set_margin_mode(self, symbol: str, mode: str = "isolated") -> None:
        try:
            await self.client.set_margin_mode(mode, symbol)
        except ccxt.ExchangeError as exc:
            if "-4046" in str(exc) or "No need to change" in str(exc):
                return   # 已经是该模式
            raise ExchangeError(f"设置保证金模式失败：{exc}", 42201, rejected=True) from exc

    async def fetch_positions(self) -> list[dict[str, Any]]:
        rows = await self.client.fetch_positions()
        out = []
        for p in rows:
            amt = float(p.get("contracts") or 0)
            if not amt:
                continue
            out.append({"symbol": p["symbol"], "side": p.get("side") or ("long" if amt > 0 else "short"),
                        "amount": abs(amt), "entry_price": _f(p.get("entryPrice")), "mark_price": _f(p.get("markPrice")),
                        "unrealized_pnl": _f(p.get("unrealizedPnl")), "leverage": _f(p.get("leverage")),
                        "liquidation_price": _f(p.get("liquidationPrice")), "margin": _f(p.get("initialMargin"))})
        return out

    async def create_order(self, symbol: str, side: str, amount: float, price: float | None,
                           type_: str = "market", client_order_id: str | None = None,
                           stop_price: float | None = None, reduce_only: bool = False) -> ExchangeOrder:
        params: dict[str, Any] = {"newClientOrderId": client_order_id} if client_order_id else {}
        if reduce_only:
            params["reduceOnly"] = True
        otype = type_
        if type_ in {"stop_market", "stop_limit"}:
            otype = "STOP_MARKET" if type_ == "stop_market" else "STOP"
            params["stopPrice"] = stop_price
        try:
            raw = await self.client.create_order(symbol, otype, side, amount, price, params)
        except ccxt.InsufficientFunds as exc:
            raise ExchangeError(f"保证金不足：{exc}", 42243, rejected=True) from exc
        except (ccxt.InvalidOrder, ccxt.BadRequest, ccxt.OrderNotFound) as exc:
            raise ExchangeError(f"下单被拒：{exc}", 42201, rejected=True) from exc
        except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
            raise ExchangeError(f"下单结果未知：{exc}", 50401, unknown=True) from exc
        except ccxt.ExchangeError as exc:
            if _looks_unknown(exc):
                raise ExchangeError(f"下单结果未知：{exc}", 50202, unknown=True) from exc
            raise ExchangeError(f"下单失败：{exc}", 50201, rejected=True) from exc
        return _parse_order(raw, client_order_id)

    async def fetch_order_by_client_id(self, client_order_id: str, symbol: str) -> ExchangeOrder:
        raw = await self.client.fetch_order(None, symbol, {"origClientOrderId": client_order_id})
        return _parse_order(raw, client_order_id)


def make_adapter(api_key: str, api_secret: str, *, env: str = "mainnet", readonly: bool = False,
                 market_type: str = "spot") -> BinanceSpotAdapter:
    cls = BinanceFuturesAdapter if market_type == "futures" else BinanceSpotAdapter
    return cls(api_key, api_secret, env=env, readonly=readonly)


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _looks_unknown(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(k in text for k in ("timeout", "timed out", "connection", "reset by peer", "502", "503", "504", "unknown"))


STATUS_MAP = {
    "open": "submitted", "new": "submitted", "partially_filled": "partial", "closed": "filled",
    "canceled": "canceled", "cancelled": "canceled", "expired": "canceled", "rejected": "rejected",
}


def _parse_order(raw: dict[str, Any], client_order_id: str | None) -> ExchangeOrder:
    status = STATUS_MAP.get(str(raw.get("status") or "").lower(), "submitted")
    return ExchangeOrder(
        exchange_order_id=str(raw.get("id")) if raw.get("id") is not None else None,
        status=status,
        filled_amount=float(raw.get("filled") or 0),
        filled_price=_f(raw.get("average")) or _f(raw.get("price")),
        fee=_sum_fee(raw),
        raw=raw,
        client_order_id=client_order_id,
    )


def _sum_fee(raw: dict[str, Any]) -> float:
    fee = raw.get("fee") or {}
    if fee.get("cost"):
        return float(fee["cost"])
    total = 0.0
    for f in raw.get("fees") or []:
        total += float(f.get("cost") or 0)
    return total


class PaperAdapter:
    """模拟盘适配器：由模拟撮合引擎实现，接口与实盘一致（《模拟盘设计》）。"""

    def __init__(self, engine) -> None:
        self.engine = engine

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        return await self.engine.market.ticker(symbol)

    async def fetch_klines(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> list[list[Any]]:
        return await self.engine.market.klines(symbol, timeframe, limit)

    async def fetch_orderbook(self, symbol: str, depth: int = 20) -> dict[str, Any]:
        return await self.engine.market.orderbook(symbol, depth)

    async def fetch_balance(self) -> list[Balance]:
        account = await self.engine.get_paper_account()
        return [Balance("USDT", float(account.balance), 0.0, float(account.balance))]

    async def normalize(self, symbol: str, amount: float, price: float | None) -> tuple[float, float | None]:
        market = (await self.engine.market.markets()).get(symbol)
        if market is None:
            raise ExchangeError(f"交易对不存在：{symbol}", 42231)
        step = float((market.get("limits") or {}).get("amount", {}).get("min") or 0)
        if step and amount < step:
            raise ExchangeError(f"数量低于最小值 {step}", 42240)
        return round(amount, 8), round(price, 8) if price is not None else None


def live_trading_enabled() -> bool:
    return bool(settings.enable_live_trading)
