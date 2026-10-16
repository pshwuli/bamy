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
    leverage: int = 1             # 仅 U 本位合约：杠杆倍数（逐仓）

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
    """单个现货模拟账户：持仓用 {base_asset: amount}，均价用 {base_asset: price}。"""

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

    # ---- 与合约账户共用的接口 ----
    def price_keys(self) -> dict[str, str]:
        """持仓键 → 取行情用的交易对。"""
        return {base: f"{base}/{self.config.quote_asset}" for base in self.positions}

    def held_text(self) -> str:
        return "、".join(f"{k} {float(v):g}" for k, v in self.positions.items() if v) or "空仓"

    def is_reducing(self, symbol: str, side: str) -> bool:
        return side == "sell"

    def precheck(self, symbol: str, side: str, amount: float, price: float, *, reduce_only: bool = False) -> int | None:
        """下单前资金/持仓检查，返回错误码或 None。"""
        if side == "buy" and float(self.balance) < amount * price * (1 + self.config.fee_rate):
            return 42261
        if side == "sell" and float(self.positions.get(self.base(symbol), 0)) < amount - 1e-12:
            return 42244
        return None

    def liquidations(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        return []   # 现货没有强平

    def balance_view(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        q = self.config.quote_asset
        return {q: {"free": float(self.balance), "used": 0.0, "total": float(self.balance)},
                **{k: {"free": float(v), "used": 0.0, "total": float(v)} for k, v in self.positions.items()}}

    def fill(self, client_order_id: str, symbol: str, side: str, amount: float, price: float,
             *, apply_slippage: bool = True, maker: bool = False, reduce_only: bool = False) -> ExchangeOrder:
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

    def place_limit(self, client_order_id: str, symbol: str, side: str, amount: float, price: float,
                    *, reduce_only: bool = False) -> ExchangeOrder:
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


MMR = Decimal("0.005")          # 维持保证金率：币安 U 本位第一档约 0.4%–1%，这里取 0.5%
MAKER_FEE = 0.0002              # 币安 U 本位默认挂单费率
TAKER_FEE = 0.0005              # 币安 U 本位默认吃单费率


class FuturesBook:
    """U 本位永续合约模拟账户：单向持仓（每个交易对一个净仓位）、逐仓、按标记价算浮盈、到强平价自动平仓。

    positions: {symbol: {"side": long|short, "amount", "entry", "margin", "leverage"}}
    balance 是可用余额（不含已占用保证金）；权益 = 可用余额 + Σ(保证金 + 浮盈)。
    资金费率暂不计算。
    """

    def __init__(self, balance: Decimal, positions: dict[str, dict[str, Any]], realized_pnl: Decimal,
                 config: PaperConfig) -> None:
        self.balance = _D(balance)
        self.positions: dict[str, dict[str, Any]] = {
            k: {"side": v["side"], "amount": _D(v["amount"]), "entry": _D(v["entry"]), "margin": _D(v["margin"]),
                "leverage": int(v.get("leverage") or 1)} for k, v in positions.items()}
        self.avg_price: dict[str, Decimal] = {}
        self.realized_pnl = _D(realized_pnl)
        self.config = config
        self.open_orders: dict[str, dict[str, Any]] = {}

    # ---- 工具 ----
    @staticmethod
    def liq_price(side: str, entry: Decimal, leverage: int) -> Decimal:
        inv = Decimal(1) / Decimal(leverage)
        return entry * (1 - inv + MMR) if side == "long" else entry * (1 + inv - MMR)

    def price_keys(self) -> dict[str, str]:
        return {sym: sym for sym in self.positions}

    def held_text(self) -> str:
        return "、".join(f"{k} {'多' if p['side'] == 'long' else '空'} {float(p['amount']):g}"
                        for k, p in self.positions.items()) or "空仓"

    def is_reducing(self, symbol: str, side: str) -> bool:
        p = self.positions.get(symbol)
        return bool(p) and ((p["side"] == "long" and side == "sell") or (p["side"] == "short" and side == "buy"))

    def precheck(self, symbol: str, side: str, amount: float, price: float, *, reduce_only: bool = False) -> int | None:
        p = self.positions.get(symbol)
        closing = min(float(p["amount"]), amount) if self.is_reducing(symbol, side) else 0.0
        if reduce_only:
            return None if closing > 0 else 42244
        opening = amount - closing
        if opening <= 0:
            return None
        lev = self.config.leverage or 1
        need = opening * price / lev + opening * price * TAKER_FEE
        return 42261 if float(self.balance) < need else None

    # ---- 成交 ----
    def fill(self, client_order_id: str, symbol: str, side: str, amount: float, price: float,
             *, apply_slippage: bool = True, maker: bool = False, reduce_only: bool = False) -> ExchangeOrder:
        amt = _D(amount)
        px = _D(price)
        if apply_slippage:
            slip = _D(self.config.slippage)
            px = px * (1 + slip) if side == "buy" else px * (1 - slip)
        fee_rate = _D(MAKER_FEE if maker else TAKER_FEE)
        total_fee = Decimal(0)
        p = self.positions.get(symbol)
        if self.is_reducing(symbol, side):
            qty = min(amt, p["amount"])
            sign = 1 if p["side"] == "long" else -1
            pnl = (px - p["entry"]) * qty * sign
            fee = qty * px * fee_rate
            released = p["margin"] * qty / p["amount"]
            self.balance += released + pnl - fee
            self.realized_pnl += pnl - fee
            total_fee += fee
            p["margin"] -= released
            p["amount"] -= qty
            if p["amount"] <= 0:
                self.positions.pop(symbol, None)
            amt -= qty
        elif reduce_only:
            raise ExchangeError("没有可平的反向持仓（reduce_only）", 42244, rejected=True)
        if amt > 0 and not reduce_only:
            lev = int(self.config.leverage or 1)
            margin = amt * px / lev
            fee = amt * px * fee_rate
            if self.balance < margin + fee:
                raise ExchangeError("模拟合约账户可用保证金不足", 42261, rejected=True)
            self.balance -= margin + fee
            self.realized_pnl -= fee
            total_fee += fee
            new_side = "long" if side == "buy" else "short"
            cur = self.positions.get(symbol)
            if cur is None:
                self.positions[symbol] = {"side": new_side, "amount": amt, "entry": px, "margin": margin, "leverage": lev}
            else:   # 同向加仓：均价加权，保证金累加
                total = cur["amount"] + amt
                cur["entry"] = (cur["entry"] * cur["amount"] + px * amt) / total
                cur["amount"] = total
                cur["margin"] += margin
        return ExchangeOrder(exchange_order_id=f"paper-{client_order_id}", status="filled",
                             filled_amount=float(amount), filled_price=float(px), fee=float(total_fee),
                             raw={"paper": True, "futures": True}, client_order_id=client_order_id)

    def place_limit(self, client_order_id: str, symbol: str, side: str, amount: float, price: float,
                    *, reduce_only: bool = False) -> ExchangeOrder:
        code = self.precheck(symbol, side, amount, price, reduce_only=reduce_only)
        if code:
            raise ExchangeError("模拟合约账户可用保证金不足" if code == 42261 else "没有可平的持仓", code, rejected=True)
        self.open_orders[client_order_id] = {"symbol": symbol, "side": side, "amount": amount, "price": price,
                                             "reduce_only": reduce_only}
        return ExchangeOrder(exchange_order_id=f"paper-{client_order_id}", status="submitted",
                             filled_amount=0.0, filled_price=None, raw={"paper": True, "futures": True},
                             client_order_id=client_order_id)

    def match(self, symbol: str, last: float) -> list[tuple[str, ExchangeOrder]]:
        filled: list[tuple[str, ExchangeOrder]] = []
        for cid, o in list(self.open_orders.items()):
            if o["symbol"] != symbol:
                continue
            if not ((o["side"] == "buy" and last <= o["price"]) or (o["side"] == "sell" and last >= o["price"])):
                continue
            try:
                result = self.fill(cid, symbol, o["side"], o["amount"], o["price"], apply_slippage=False, maker=True,
                                   reduce_only=bool(o.get("reduce_only")))
            except ExchangeError as exc:
                result = ExchangeOrder(f"paper-{cid}", "rejected", 0.0, None, raw={"error": str(exc)}, client_order_id=cid)
            self.open_orders.pop(cid, None)
            filled.append((cid, result))
        return filled

    def cancel(self, client_order_id: str) -> bool:
        return self.open_orders.pop(client_order_id, None) is not None

    def liquidations(self, prices: dict[str, float]) -> list[dict[str, Any]]:
        """标记价穿过强平价的仓位按强平价平掉，逐仓只亏掉该仓保证金。返回被强平的仓位。"""
        out = []
        for sym, p in list(self.positions.items()):
            px = prices.get(sym)
            if px is None:
                continue
            liq = self.liq_price(p["side"], p["entry"], p["leverage"])
            if (p["side"] == "long" and _D(px) <= liq) or (p["side"] == "short" and _D(px) >= liq):
                self.realized_pnl -= p["margin"]
                self.positions.pop(sym, None)
                out.append({"symbol": sym, "side": p["side"], "amount": float(p["amount"]), "price": float(liq),
                            "loss": float(p["margin"])})
        return out

    # ---- 估值 ----
    def _upnl(self, sym: str, p: dict[str, Any], prices: dict[str, float]) -> Decimal:
        px = _D(prices.get(sym, p["entry"]))
        return (px - p["entry"]) * p["amount"] * (1 if p["side"] == "long" else -1)

    def equity(self, prices: dict[str, float]) -> Decimal:
        return self.balance + sum((p["margin"] + self._upnl(s, p, prices) for s, p in self.positions.items()), Decimal(0))

    def unrealized(self, prices: dict[str, float]) -> Decimal:
        return sum((self._upnl(s, p, prices) for s, p in self.positions.items()), Decimal(0))

    def positions_list(self, prices: dict[str, float], quote: str = "USDT") -> list[dict[str, Any]]:
        out = []
        for sym, p in self.positions.items():
            entry = float(p["entry"])
            mark = float(prices.get(sym, entry))
            pnl = float(self._upnl(sym, p, prices))
            margin = float(p["margin"])
            out.append({"id": sym, "symbol": sym, "side": p["side"], "amount": float(p["amount"]),
                        "entry_price": entry, "mark_price": mark, "unrealized_pnl": pnl,
                        "unrealized_pnl_pct": pnl / margin * 100 if margin else 0.0,   # 合约按保证金算收益率
                        "leverage": p["leverage"], "margin": margin,
                        "liquidation_price": float(self.liq_price(p["side"], p["entry"], p["leverage"])),
                        "label": f"{sym.split('/')[0]} {'多' if p['side'] == 'long' else '空'} {p['leverage']}x"})
        return out

    def balance_view(self, prices: dict[str, float] | None = None) -> dict[str, Any]:
        used = float(sum((p["margin"] for p in self.positions.values()), Decimal(0)))
        free = float(self.balance)
        return {"USDT": {"free": free, "used": used, "total": free + used}}

    def dump_positions(self) -> str:
        return json.dumps({k: {"side": p["side"], "amount": str(p["amount"]), "entry": str(p["entry"]),
                               "margin": str(p["margin"]), "leverage": p["leverage"]} for k, p in self.positions.items()})

    def dump_avg(self) -> str:
        return "{}"


def load_futures_positions(raw: str | None) -> dict[str, dict[str, Any]]:
    if not raw:
        return {}
    data = json.loads(raw)
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def is_futures_symbol(symbol: str) -> bool:
    return ":" in (symbol or "")
