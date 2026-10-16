"""内置策略：决策内核单元测试 + 用替身上下文跑通实盘策略的完整生命周期。"""
from __future__ import annotations

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2] / "extensions" / "strategies"


def _load(pkg: str, mod: str):
    """按注册中心的方式加载：helpers/ 只对本包可见。"""
    for name in [n for n in sys.modules if n == "helpers" or n.startswith("helpers.")]:
        sys.modules.pop(name)
    sys.path.insert(0, str(ROOT / pkg))
    try:
        spec = importlib.util.spec_from_file_location(f"t_{pkg}_{mod}", ROOT / pkg / f"{mod}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = m   # dataclass 需要模块已注册
        spec.loader.exec_module(m)
        return m
    finally:
        sys.path.remove(str(ROOT / pkg))


# ---------------------------------------------------------------- trend_pullback 内核
def test_pullback_indicators():
    sig = _load("trend_pullback", "helpers/signals")
    closes = [float(i) for i in range(1, 31)]
    e = sig.ema(closes, 5)
    assert len(e) == 30 and e[4] == pytest.approx(3.0)       # SMA 种子
    r = sig.rsi(closes, 2)
    assert r[-1] == 100.0                                       # 只涨不跌
    down = list(reversed(closes))
    assert sig.rsi(down, 2)[-1] == pytest.approx(0.0)
    a = sig.atr([c + 1 for c in closes], [c - 1 for c in closes], closes, 14)
    assert a[-1] == pytest.approx(2.0, rel=0.05)


def test_pullback_entry_exit_rules():
    sig = _load("trend_pullback", "helpers/signals")
    p = sig.Params(trend=20, slope_n=3, rsi_n=2, rsi_in=10, exit_ema=10, atr_n=5)
    up = [100 * 1.01 ** i for i in range(p.warmup)]             # 每根涨 1%，趋势均线远在价格下方
    dip = up[:-2] + [up[-3] * 0.97, up[-3] * 0.93]              # 上升趋势里两根急跌 3%、7%
    hi, lo = [c + 1 for c in dip], [c - 1 for c in dip]
    s = sig.snapshot(hi, lo, dip, p)
    assert s.up and s.rsi < 10 and sig.should_enter(s, p)
    flat = [100.0] * p.warmup
    s2 = sig.snapshot(flat, flat, flat, p)
    assert not sig.should_enter(s2, p)                          # 没有趋势不开仓
    assert sig.should_exit(s2, p, took_tp1=False, bars_held=1) == "趋势失效"


def test_pullback_position_sizing_by_capital():
    sig = _load("trend_pullback", "helpers/signals")
    p = sig.Params()
    # 止损距离 = 3 × ATR / 价 = 3 × 1000 / 50000 = 6%；1% 风险 → 投入权益的 1/6
    q = sig.position_quote(10_000, 10_000, 50_000, 1_000, p, risk_pct=0.01, max_alloc=1.0, min_notional=5)
    assert q == pytest.approx(10_000 * 0.01 / 0.06)
    # 单笔最多投入上限生效
    assert sig.position_quote(10_000, 10_000, 50_000, 10, p, risk_pct=0.01, max_alloc=0.5, min_notional=5) == 5_000
    # 小资金：算出的仓位不够两倍最小下单额 → 跳过
    assert sig.position_quote(30, 30, 50_000, 1_000, p, risk_pct=0.01, max_alloc=1.0, min_notional=5) == 0
    # 大资金：线性放大，不受最小下单额影响
    big = sig.position_quote(5_000_000, 5_000_000, 50_000, 1_000, p, risk_pct=0.01, max_alloc=1.0, min_notional=5)
    assert big == pytest.approx(5_000_000 * 0.01 / 0.06)


# ---------------------------------------------------------------- trend_vol_target 内核
def test_vol_target_weight():
    sig = _load("trend_vol_target", "helpers/signals")
    p = sig.Params()
    n = p.warmup + 5
    # 平稳上涨、日波动约 1% → 年化约 19% < 目标 60% → 满仓（受 max_alloc 限制）
    calm = [100 * (1.002 ** i) * (1 + 0.01 * (-1) ** i) for i in range(n)]
    assert sig.target_weight(calm, p) == pytest.approx(1.0)
    # 剧烈上涨、日波动约 6% → 年化波动远高于 60% → 仓位按比例缩小
    wild = [100 * (1.004 ** i) * (1 + 0.06 * (-1) ** i) for i in range(n)]
    w = sig.target_weight(wild, p)
    vol = sig.realized_vol(wild, p.vol_n, 365)
    assert vol > 0.6 and 0 < w < 1 and w == pytest.approx(0.6 / vol)   # 高波动 → 自动减仓
    # 下跌趋势 → 空仓
    assert sig.target_weight(list(reversed(calm)), p) == 0.0
    assert sig.target_weight(calm[:10], p) is None
    assert sig.needs_rebalance(0.5, 0.55, 0.2) is False and sig.needs_rebalance(0.3, 0.55, 0.2) is True
    assert sig.needs_rebalance(0.1, 0.0, 0.2) is True


# ---------------------------------------------------------------- 实盘策略生命周期（替身上下文）
class FakeState:
    def __init__(self):
        self.d = {}

    async def get(self, k, default=None):
        return self.d.get(k, default)

    async def set(self, k, v):
        self.d[k] = v

    async def delete(self, k):
        self.d.pop(k, None)


class FakeCtx:
    """模拟交易所：市价单按当前价全部成交，手续费 0.1%。"""

    def __init__(self, params, klines, cash=10_000.0):
        self.params, self.klines, self.symbol = params, klines, "BTC/USDT"
        self.state, self.cash, self.qty, self.price, self.logs, self.orders = FakeState(), cash, 0.0, 0.0, [], []

    async def get_klines(self, timeframe="4h", limit=100):
        return self.klines[-limit:]

    async def fetch_balance(self):
        return {"USDT": {"free": self.cash, "total": self.cash}, "BTC": {"free": self.qty, "total": self.qty}}

    def get_ticker(self, symbol=None):
        return {"price": self.price}

    async def _fill(self, side, amount):
        from bamy.strategy import OrderView

        if side == "buy":
            self.cash -= amount * self.price; self.qty += amount * 0.999
        else:
            amount = min(amount, self.qty); self.qty -= amount; self.cash += amount * self.price * 0.999
        self.orders.append((side, amount, self.price))
        return OrderView(side=side, amount=amount, filled_amount=amount, filled_price=self.price, status="filled")

    async def buy(self, amount, price=None, type_="market"):
        return await self._fill("buy", amount)

    async def sell(self, amount, price=None, type_="market"):
        return await self._fill("sell", amount)

    def log_info(self, m):
        self.logs.append(m)

    log_warn = log_error = log_info


async def test_trend_pullback_live_cycle(monkeypatch):
    mod = _load("trend_pullback", "strategy")
    from bamy.strategy import Tick

    tf = 14400
    now = 2_000_000_000 // tf * tf
    # 构造 4h K 线：长期上涨，最后两根急跌（触发入场），时间戳以毫秒计、最后一根已收盘
    n = 700
    closes = [20_000 * (1.0015 ** i) for i in range(n)]
    closes[-2] *= 0.97; closes[-1] = closes[-2] * 0.97
    kl = [[(now - (n - i) * tf) * 1000, c, c * 1.004, c * 0.996, c, 1.0] for i, c in enumerate(closes)]
    params = {"risk_pct": 1.0, "max_alloc_pct": 100, "timeframe": "4h", "trend_period": 200, "rsi_period": 2,
              "rsi_entry": 10, "stop_atr": 3.0, "tp1_atr": 2.5, "tp1_fraction": 50, "exit_ema": 50, "max_hold_bars": 60}
    ctx = FakeCtx(params, kl)
    s = mod.STRATEGY(); s.ctx = ctx
    monkeypatch.setattr(mod.time, "time", lambda: now + 10)
    await s.on_start()
    ctx.price = closes[-1]
    await s.on_tick(Tick(symbol="BTC/USDT", price=ctx.price))
    assert s.pos and ctx.orders[-1][0] == "buy", ctx.logs
    entry, stop, tp1 = s.pos["entry_price"], s.pos["stop"], s.pos["tp1"]
    # 按风险定仓：亏到止损约损失 1% 权益
    loss_at_stop = s.pos["amount"] * (entry - stop)
    assert loss_at_stop == pytest.approx(100, rel=0.05)
    # 涨到第一目标：卖一半、止损抬到保本
    ctx.price = tp1 * 1.001
    await s.on_tick(Tick(symbol="BTC/USDT", price=ctx.price))
    assert s.pos["took_tp1"] and s.pos["stop"] >= entry and ctx.orders[-1][0] == "sell"
    left = s.pos["amount"]
    # 回落到保本价：剩余全部卖出，不亏本金
    ctx.price = s.pos["stop"] * 0.999
    await s.on_tick(Tick(symbol="BTC/USDT", price=ctx.price))
    assert s.pos is None and ctx.orders[-1][0] == "sell" and ctx.orders[-1][1] <= left + 1e-12
    assert ctx.qty == pytest.approx(0, abs=1e-9)
    assert ctx.cash > 10_000 * 0.999, ctx.cash   # 整笔扣掉手续费后不亏
    assert await ctx.state.get("tp.pos") is None


async def test_trend_pullback_skips_when_capital_too_small(monkeypatch):
    mod = _load("trend_pullback", "strategy")
    from bamy.strategy import Tick

    tf, now, n = 14400, 2_000_000_000 // 14400 * 14400, 700
    closes = [20_000 * (1.0015 ** i) for i in range(n)]
    closes[-2] *= 0.97; closes[-1] = closes[-2] * 0.97
    kl = [[(now - (n - i) * tf) * 1000, c, c * 1.004, c * 0.996, c, 1.0] for i, c in enumerate(closes)]
    params = {"risk_pct": 1.0, "max_alloc_pct": 100, "timeframe": "4h", "trend_period": 200, "rsi_period": 2,
              "rsi_entry": 10, "stop_atr": 3.0, "tp1_atr": 2.5, "tp1_fraction": 50, "exit_ema": 50, "max_hold_bars": 60}
    ctx = FakeCtx(params, kl, cash=20.0)
    s = mod.STRATEGY(); s.ctx = ctx
    monkeypatch.setattr(mod.time, "time", lambda: now + 10)
    await s.on_start()
    ctx.price = closes[-1]
    await s.on_tick(Tick(symbol="BTC/USDT", price=ctx.price))
    assert s.pos is None and not ctx.orders
    assert any("跳过" in m for m in ctx.logs)


def test_strategies_declare_suitability():
    import yaml

    for pkg in ("trend_pullback", "trend_vol_target", "dca_strategy"):
        meta = yaml.safe_load((ROOT / pkg / "strategy.yaml").read_text(encoding="utf-8"))
        suit = meta.get("suitability") or {}
        assert suit.get("capital", {}).get("min_usdt") and suit.get("market") and suit.get("symbols"), pkg
        assert (meta.get("backtest") or {}).get("summary"), pkg
