"""模拟撮合（PaperEngine）：手续费、滑点、市价按 last、限价挂单簿可配（《模拟盘设计》）。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from bamy.engine.adapters import ExchangeError, ExchangeOrder


@dataclass
class PaperConfig:
    fee_rate: float = 0.001
    slippage: float = 0.0005
    limit_mode: str = "book"      # book：限价单挂单簿，价格穿越时成交；immediate：按限价立即成交
    quote_asset: str = "USDT"

    @classmethod
    def from_json(cls, raw: str | dict | None, defaults: dict[str, Any] | None = None) -> "PaperConfig":
        data = dict(defaults or {})
        if isinstance(raw, str) and raw:
            data.update(json.loads(raw))
        elif isinstance(raw, dict):
            data.update(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _D(v: Any) -> Decimal:
    return Decimal(str(v or 0))


class PaperBook:
    """单个模拟账户：持仓用 {base_asset: amount}，均价用 {base_asset: price}。"""

    def __init__(self, balance: Decimal, positions: dict[str, float], avg_price: dict[str, float],
                 realized_pnl: Decimal, config: PaperConfig) -> None:
        self.balance = _D(balance)
        self.positions = {k: _D(v) for k, v in positions.items()}
        self.avg_price = {k: _D(v) for k, v in avg_price.items()}
        self.realized_pnl = _D(realized_pnl)
        self.config = config
        self.open_orders: dict[str, dict[str, Any]] = {}

    def base(self, symbol: str) -> str:
        return symbol.split("/")[0]

    def fill(self, client_order_id: str, symbol: str, side: str, amount: float, price: float,
             *, apply_slippage: bool = True) -> ExchangeOrder:
        amt = _D(amount)
        px = _D(price)
        if apply_slippage:
            slip = _D(self.config.slippage)
            px = px * (1 + slip) if side == "buy" else px * (1 - slip)
        cost = amt * px
        fee = cost * _D(self.config.fee_rate)
        base = self.base(symbol)
        held = self.positions.get(base, Decimal(0))
        if side == "buy":
            if self.balance < cost + fee:
                raise ExchangeError("模拟账户资金不足", 42261, rejected=True)
            new_amt = held + amt
            old_avg = self.avg_price.get(base, Decimal(0))
            self.avg_price[base] = (old_avg * held + px * amt) / new_amt if new_amt else Decimal(0)
            self.positions[base] = new_amt
            self.balance -= cost + fee
        else:
            if held < amt:
                raise ExchangeError("持仓不足", 42244, rejected=True)
            avg = self.avg_price.get(base, Decimal(0))
            self.realized_pnl += (px - avg) * amt - fee
            self.positions[base] = held - amt
            if self.positions[base] <= 0:
                self.positions.pop(base, None)
                self.avg_price.pop(base, None)
            self.balance += cost - fee
        return ExchangeOrder(exchange_order_id=f"paper-{client_order_id}", status="filled",
                             filled_amount=float(amt), filled_price=float(px), fee=float(fee),
                             raw={"paper": True}, client_order_id=client_order_id)

    def place_limit(self, client_order_id: str, symbol: str, side: str, amount: float, price: float) -> ExchangeOrder:
        # 下单时预检资金/持仓，避免挂出无法成交的单
        if side == "buy" and self.balance < _D(amount) * _D(price) * (1 + _D(self.config.fee_rate)):
            raise ExchangeError("模拟账户资金不足", 42261, rejected=True)
        if side == "sell" and self.positions.get(self.base(symbol), Decimal(0)) < _D(amount):
            raise ExchangeError("持仓不足", 42244, rejected=True)
        self.open_orders[client_order_id] = {"symbol": symbol, "side": side, "amount": amount, "price": price}
        return ExchangeOrder(exchange_order_id=f"paper-{client_order_id}", status="submitted",
                             filled_amount=0.0, filled_price=None, raw={"paper": True},
                             client_order_id=client_order_id)

    def match(self, symbol: str, last: float) -> list[tuple[str, ExchangeOrder]]:
        """价格穿越限价时成交，按限价成交不加滑点。"""
        filled: list[tuple[str, ExchangeOrder]] = []
        for cid, o in list(self.open_orders.items()):
            if o["symbol"] != symbol:
                continue
            hit = (o["side"] == "buy" and last <= o["price"]) or (o["side"] == "sell" and last >= o["price"])
            if not hit:
                continue
            try:
                result = self.fill(cid, symbol, o["side"], o["amount"], o["price"], apply_slippage=False)
            except ExchangeError as exc:
                result = ExchangeOrder(f"paper-{cid}", "rejected", 0.0, None, raw={"error": str(exc)},
                                       client_order_id=cid)
            self.open_orders.pop(cid, None)
            filled.append((cid, result))
        return filled

    def cancel(self, client_order_id: str) -> bool:
        return self.open_orders.pop(client_order_id, None) is not None

    def equity(self, prices: dict[str, float]) -> Decimal:
        value = self.balance
        for base, amt in self.positions.items():
            px = prices.get(base)
            if px is None:
                px = float(self.avg_price.get(base, 0))
            value += amt * _D(px)
        return value

    def unrealized(self, prices: dict[str, float]) -> Decimal:
        total = Decimal(0)
        for base, amt in self.positions.items():
            px = prices.get(base, float(self.avg_price.get(base, 0)))
            total += (_D(px) - self.avg_price.get(base, Decimal(0))) * amt
        return total

    def positions_list(self, prices: dict[str, float], quote: str = "USDT") -> list[dict[str, Any]]:
        out = []
        for base, amt in self.positions.items():
            avg = float(self.avg_price.get(base, 0))
            px = prices.get(base, avg)
            pnl = (px - avg) * float(amt)
            out.append({"id": f"{base}/{quote}", "symbol": f"{base}/{quote}", "side": "long",
                        "amount": float(amt), "entry_price": avg, "mark_price": px,
                        "unrealized_pnl": pnl,
                        "unrealized_pnl_pct": (pnl / (avg * float(amt)) * 100) if avg and amt else 0.0,
                        "label": base})
        return out

    def dump_positions(self) -> str:
        return json.dumps({k: str(v) for k, v in self.positions.items()})

    def dump_avg(self) -> str:
        return json.dumps({k: str(v) for k, v in self.avg_price.items()})


def load_json_map(raw: str | None) -> dict[str, float]:
    if not raw:
        return {}
    data = json.loads(raw)
    if isinstance(data, list):
        return {}
    return {k: float(v) for k, v in data.items()}
