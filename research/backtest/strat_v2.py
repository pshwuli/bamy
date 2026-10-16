"""第二轮候选：用事件引擎跑，支持分批止盈 + 保本移动止损。

E 趋势回调分批（高胜率目标）：
  - 日线级趋势过滤：收盘 > EMA(trend) 且 EMA(trend) 向上
  - 入场：4h RSI(n) 跌破 rsi_in（趋势中的回调）
  - 止损：入场价 - stop_atr × ATR
  - 第一目标：入场价 + tp1_atr × ATR，卖出 tp1_frac；随后止损上移到保本
  - 剩余仓位：收盘跌破 EMA(exit_ema) 或趋势失效时卖出
F 唐奇安突破 + 分批：突破入场，1R 先止盈一半并保本，剩余吊灯止损
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bt import atr, ema, rsi, sma
from engine import FEE, SLIP, Book, stats


def run_e(df: pd.DataFrame, *, trend=200, slope_n=10, rsi_n=3, rsi_in=20, stop_atr=2.5, tp1_atr=1.5,
          tp1_frac=0.5, exit_ema=20, atr_n=14, risk=None, bars_per_year=6 * 365, max_hold=60):
    c = df["c"]
    t = ema(c, trend)
    up = (c > t) & (t > t.shift(slope_n))
    r = rsi(c, rsi_n)
    a = atr(df, atr_n)
    ex = ema(c, exit_ema)
    O, H, L, C = (df[k].to_numpy() for k in "ohlc")
    UP, R, A, EX = up.to_numpy(), r.to_numpy(), a.to_numpy(), ex.to_numpy()
    b = Book(); eq = np.empty(len(df)); in_bars = 0
    stop = tp1 = None; took_tp1 = False; entry_bar = 0; pending = None; trade_start_eq = 1.0
    for i in range(len(df)):
        # 1) 挂单/市价单在本根成交
        if pending == "buy" and b.qty == 0:
            px = O[i] * (1 + SLIP)
            if risk is None:
                quote = b.cash
            else:
                # 按风险定仓：亏到止损时损失 risk × 权益
                dist = stop_atr * A[i - 1] / px
                quote = min(b.cash, b.equity(px) * risk / max(dist, 1e-6))
            trade_start_eq = b.equity(px)
            b.buy(px, quote)
            stop = px - stop_atr * A[i - 1]; tp1 = px + tp1_atr * A[i - 1]; took_tp1 = False; entry_bar = i
        elif pending == "sell" and b.qty > 0:
            b.sell(O[i] * (1 - SLIP)); b.trades.append(b.equity(O[i]) / trade_start_eq - 1)
        pending = None
        # 2) 盘中止损 / 第一目标
        if b.qty > 0:
            if L[i] <= stop:
                b.sell(min(O[i], stop) * (1 - SLIP)); b.trades.append(b.equity(C[i]) / trade_start_eq - 1)
            elif not took_tp1 and H[i] >= tp1:
                b.sell(max(O[i], tp1), tp1_frac)   # 限价止盈，不计滑点
                took_tp1 = True; stop = max(stop, b.cost / b.qty if b.qty else stop)  # 保本
        if b.qty > 0:
            in_bars += 1
        eq[i] = b.equity(C[i])
        # 3) 收盘决策
        if b.qty > 0:
            if (took_tp1 and C[i] < EX[i]) or not UP[i] or (i - entry_bar) >= max_hold:
                pending = "sell"
        elif UP[i] and R[i] < rsi_in and not np.isnan(A[i]):
            pending = "buy"
    return stats(pd.Series(eq, index=df.index), b.trades, bars_per_year, c, in_bars / len(df))


def run_f(df: pd.DataFrame, *, entry_n=55, exit_n=20, atr_n=20, stop_atr=2.0, tp1_r=1.0, tp1_frac=0.5,
          chand=3.0, trend=200, bars_per_year=6 * 365):
    c = df["c"]
    hi = df["h"].rolling(entry_n).max().shift()
    lo = df["l"].rolling(exit_n).min().shift()
    a = atr(df, atr_n)
    tr = sma(c, trend)
    O, H, L, C = (df[k].to_numpy() for k in "ohlc")
    HI, LO, A, TR = hi.to_numpy(), lo.to_numpy(), a.to_numpy(), tr.to_numpy()
    hh = df["h"].rolling(exit_n).max().to_numpy()
    b = Book(); eq = np.empty(len(df)); in_bars = 0
    stop = tp1 = None; took = False; pending = None; t0 = 1.0
    for i in range(len(df)):
        if pending == "buy" and b.qty == 0:
            px = O[i] * (1 + SLIP); t0 = b.equity(px); b.buy(px, b.cash)
            r1 = stop_atr * A[i - 1]; stop = px - r1; tp1 = px + tp1_r * r1; took = False
        elif pending == "sell" and b.qty > 0:
            b.sell(O[i] * (1 - SLIP)); b.trades.append(b.equity(O[i]) / t0 - 1)
        pending = None
        if b.qty > 0:
            if L[i] <= stop:
                b.sell(min(O[i], stop) * (1 - SLIP)); b.trades.append(b.equity(C[i]) / t0 - 1)
            elif not took and H[i] >= tp1:
                b.sell(max(O[i], tp1), tp1_frac); took = True; stop = max(stop, b.cost / b.qty)
        if b.qty > 0:
            in_bars += 1
            stop = max(stop, hh[i] - chand * A[i]) if took else stop
        eq[i] = b.equity(C[i])
        if b.qty > 0 and C[i] < LO[i]:
            pending = "sell"
        elif b.qty == 0 and C[i] > HI[i] and C[i] > TR[i]:
            pending = "buy"
    return stats(pd.Series(eq, index=df.index), b.trades, bars_per_year, c, in_bars / len(df))
