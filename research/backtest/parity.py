"""一致性校验：用上线策略的决策内核（extensions/.../helpers/signals.py）逐根重放，结果应与研究回测一致。"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "extensions/strategies/trend_pullback"))

import numpy as np
import pandas as pd

from bt import SPLIT, load
from engine import FEE, SLIP, Book, stats
from helpers.signals import Params, position_quote, should_enter, should_exit, snapshot
from strat_v2 import run_e


def replay(df: pd.DataFrame, p: Params, risk_pct=0.0, max_alloc=1.0, min_notional=0.0, start=0):
    H, L, C, O = (df[k].tolist() for k in "hlco")
    b = Book(); eq = np.full(len(df), np.nan); in_bars = 0
    pending = None; stop = tp1 = None; took = False; entry_bar = 0; t0 = 1.0; last_atr = None
    for i in range(start, len(df)):
        if pending == "buy" and b.qty == 0:
            px = O[i] * (1 + SLIP)
            q = position_quote(b.equity(px), b.cash, px, last_atr, p, risk_pct=risk_pct, max_alloc=max_alloc,
                               min_notional=min_notional)
            if q > 0:
                t0 = b.equity(px); b.buy(px, q)
                stop, tp1, took, entry_bar = px - p.stop_atr * last_atr, px + p.tp1_atr * last_atr, False, i
        elif pending == "sell" and b.qty > 0:
            b.sell(O[i] * (1 - SLIP)); b.trades.append(b.equity(O[i]) / t0 - 1)
        pending = None
        if b.qty > 0:
            if L[i] <= stop:
                b.sell(min(O[i], stop) * (1 - SLIP)); b.trades.append(b.equity(C[i]) / t0 - 1)
            elif not took and H[i] >= tp1:
                b.sell(max(O[i], tp1), p.tp1_frac); took = True; stop = max(stop, b.cost / b.qty)
        if b.qty > 0:
            in_bars += 1
        eq[i] = b.equity(C[i])
        lo = max(0, i + 1 - (p.warmup + 50))   # 与实盘相同：取 warmup+50 根
        s = snapshot(H[lo:i + 1], L[lo:i + 1], C[lo:i + 1], p)
        if s is None:
            continue
        if b.qty > 0:
            if should_exit(s, p, took_tp1=took, bars_held=i - entry_bar):
                pending = "sell"
        elif should_enter(s, p):
            pending, last_atr = "buy", s.atr
    sl = slice(start, None)
    return stats(pd.Series(eq[sl], index=df.index[sl]), b.trades, 6 * 365, df["c"].iloc[sl], in_bars / (len(df) - start))


if __name__ == "__main__":
    from engine import fmt

    p = Params()
    for sym in ["BTCUSDT", "ETHUSDT"]:
        full = load(sym, "4h"); d = full[full.index >= SPLIT]
        a = run_e(d, trend=200, rsi_n=2, rsi_in=10, stop_atr=3.0, tp1_atr=2.5, exit_ema=50, max_hold=60)
        b = replay(full, p, start=int((full.index < SPLIT).sum()))
        print(f"{sym} 研究版 {fmt(a)}\n{sym} 上线版 {fmt(b)}")
