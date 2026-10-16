"""只在样本内（2020-2023）搜参数；按"四个币平均夏普，且每个币 PF>1"排序。样本外只用来验证前几名。"""
import itertools
import sys

import numpy as np

from bt import SPLIT, load
from engine import fmt
from strat_v2 import run_e, run_f

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
TF = sys.argv[1] if len(sys.argv) > 1 else "4h"
WHICH = sys.argv[2] if len(sys.argv) > 2 else "e"
BPY = {"1h": 24 * 365, "4h": 6 * 365, "1d": 365}[TF]
data = {s: load(s, TF) for s in SYMS}
ins = {s: d[d.index < SPLIT] for s, d in data.items()}
oos = {s: d[d.index >= SPLIT] for s, d in data.items()}

if WHICH == "e":
    grid = dict(trend=[100, 200], rsi_n=[2, 3, 5], rsi_in=[10, 20, 30], stop_atr=[2.0, 3.0],
                tp1_atr=[1.0, 1.5, 2.5], exit_ema=[10, 20, 50])
    fn = run_e
else:
    grid = dict(entry_n=[20, 55, 100], exit_n=[10, 20], stop_atr=[1.5, 2.0, 3.0], tp1_r=[1.0, 2.0],
                chand=[2.5, 3.5], trend=[100, 200])
    fn = run_f

rows = []
keys = list(grid)
for vals in itertools.product(*grid.values()):
    p = dict(zip(keys, vals))
    res = [fn(ins[s], bars_per_year=BPY, **p) for s in SYMS]
    if min(r["pf"] for r in res) <= 1.0 or min(r["trades"] for r in res) < 20:
        continue
    rows.append((np.mean([r["sharpe"] for r in res]), np.mean([r["win"] for r in res]), p, res))

rows.sort(key=lambda x: -x[0])
print(f"{TF} {WHICH}: 合格组合 {len(rows)} / {np.prod([len(v) for v in grid.values()])}")
for sh, win, p, res in rows[:6]:
    print(f"\n夏普均值 {sh:.2f} 胜率均值 {win:.1%}  参数 {p}")
    for s in SYMS:
        print(f"  {s:8s} 样本内 {fmt(fn(ins[s], bars_per_year=BPY, **p))}")
        print(f"  {s:8s} 样本外 {fmt(fn(oos[s], bars_per_year=BPY, **p))}")
