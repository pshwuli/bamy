"""跑全部候选：4 个币 × 样本内 / 样本外，打印胜率、盈亏比、收益、回撤、夏普。"""
import sys

import pandas as pd

import candidates as C
from bt import SPLIT, load, run

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
TF = sys.argv[1] if len(sys.argv) > 1 else "4h"
BPY = {"1h": 24 * 365, "4h": 6 * 365, "1d": 365}[TF]

STRATS = {
    "旧-均线交叉7/25": lambda d: C.old_ma_cross(d),
    "A-趋势回调RSI2": lambda d: C.trend_pullback(d),
    "B-唐奇安突破": lambda d: C.donchian_trend(d),
    "C-趋势+波动率仓位": lambda d: C.trend_voltarget(d, bars_per_year=BPY),
    "D-布林回归+趋势": lambda d: C.bb_revert(d),
}

data = {s: load(s, TF) for s in SYMS}
for name, fn in STRATS.items():
    print(f"\n==== {name}  ({TF})")
    for s, df in data.items():
        for tag, part in (("样本内", df[df.index < SPLIT]), ("样本外", df[df.index >= SPLIT])):
            if len(part) < 300:
                continue
            e, x, st, sz = fn(part)
            r = run(part, e, x, stop=st, size=sz, bars_per_year=BPY)
            print(f"  {s:8s} {tag} {r.row()}")
