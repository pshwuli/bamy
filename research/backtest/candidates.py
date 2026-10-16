"""候选策略：信号定义。每个函数返回 (entry, exit, stop, size)，参数全部显式。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bt import atr, ema, rsi, sma


# ---------------------------------------------------------------- 现有策略（基线）
def old_ma_cross(df, fast=7, slow=25):
    f, s = sma(df["c"], fast), sma(df["c"], slow)
    return (f > s) & (f.shift() <= s.shift()), (f < s) & (f.shift() >= s.shift()), None, None


# ---------------------------------------------------------------- A 趋势回调（RSI 超卖 + 长期趋势过滤）
def trend_pullback(df, trend=200, rsi_n=2, rsi_in=10, exit_ma=5, atr_n=14, stop_atr=3.0):
    """长期均线之上才做多；短周期 RSI 超卖时买回调，价格收回短均线之上卖出；ATR 硬止损兜底。"""
    c = df["c"]
    up = c > sma(c, trend)
    r = rsi(c, rsi_n)
    entry = up & (r < rsi_in)
    exit_ = (c > sma(c, exit_ma)) | (~up)
    stop = c - stop_atr * atr(df, atr_n)
    stop = stop.where(entry).ffill()   # 入场时锁定止损价
    return entry, exit_, stop, None


# ---------------------------------------------------------------- B 唐奇安突破 + ATR 吊灯止损（经典海龟变体）
def donchian_trend(df, entry_n=55, exit_n=20, atr_n=20, chand=3.0, trend=200):
    c = df["c"]
    hi = df["h"].rolling(entry_n).max().shift()
    lo = df["l"].rolling(exit_n).min().shift()
    entry = (c > hi) & (c > sma(c, trend))
    a = atr(df, atr_n)
    chandelier = df["h"].rolling(exit_n).max() - chand * a
    exit_ = (c < lo) | (c < chandelier)
    return entry, exit_, None, None


# ---------------------------------------------------------------- C 双均线趋势 + 波动率目标仓位
def trend_voltarget(df, fast=50, slow=200, vol_n=30, target=0.5, bars_per_year=6 * 365, atr_n=14, stop_atr=4.0):
    """快均线在慢均线之上且价格在快均线之上持有；仓位 = 目标年化波动 / 实际年化波动（上限 100%）。"""
    c = df["c"]
    f, s = ema(c, fast), ema(c, slow)
    regime = (f > s) & (c > f)
    entry = regime & ~regime.shift().fillna(False).astype(bool)
    exit_ = (c < f) | (f < s)
    vol = c.pct_change().rolling(vol_n).std() * np.sqrt(bars_per_year)
    size = (target / vol).clip(upper=1.0)
    stop = (c - stop_atr * atr(df, atr_n)).where(entry).ffill()
    return entry, exit_, stop, size


# ---------------------------------------------------------------- D 布林带均值回归（带趋势过滤）
def bb_revert(df, n=20, k=2.0, trend=200, atr_n=14, stop_atr=2.5):
    c = df["c"]
    m, sd = sma(c, n), c.rolling(n).std()
    entry = (c < m - k * sd) & (c > sma(c, trend))
    exit_ = c > m
    stop = (c - stop_atr * atr(df, atr_n)).where(entry).ffill()
    return entry, exit_, stop, None
