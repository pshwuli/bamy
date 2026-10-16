"""稳健性：最优参数邻域的样本外表现 + 滚动前进（每年用之前全部数据选参，下一年验证）。"""
import itertools

import numpy as np
import pandas as pd

from bt import SPLIT, load
from engine import fmt
from strat_v2 import run_e, run_f

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
BPY = 6 * 365
data = {s: load(s, "4h") for s in SYMS}
oos = {s: d[d.index >= SPLIT] for s, d in data.items()}

print("== E 邻域（trend200, rsi2<10, exit_ema50 固定；止损/止盈变动）样本外")
for st, tp in itertools.product([2.0, 2.5, 3.0, 3.5], [1.5, 2.0, 2.5, 3.0, 4.0]):
    res = [run_e(oos[s], trend=200, rsi_n=2, rsi_in=10, stop_atr=st, tp1_atr=tp, exit_ema=50, bars_per_year=BPY) for s in SYMS]
    print(f"  stop{st} tp{tp}: 胜率{np.mean([r['win'] for r in res]):5.1%} PF最差{min(r['pf'] for r in res):4.2f} "
          f"夏普均{np.mean([r['sharpe'] for r in res]):5.2f} 年化均{np.mean([r['cagr'] for r in res]):6.1%} "
          f"回撤最差{min(r['mdd'] for r in res):6.1%} 盈利币数{sum(r['ret'] > 0 for r in res)}/4")

print("\n== E 入场阈值邻域（stop3 tp2.5 固定）样本外")
for rn, ri, ee in itertools.product([2, 3], [5, 10, 15, 20], [20, 50, 100]):
    res = [run_e(oos[s], trend=200, rsi_n=rn, rsi_in=ri, stop_atr=3.0, tp1_atr=2.5, exit_ema=ee, bars_per_year=BPY) for s in SYMS]
    print(f"  rsi{rn}<{ri} exit_ema{ee}: 胜率{np.mean([r['win'] for r in res]):5.1%} PF最差{min(r['pf'] for r in res):4.2f} "
          f"夏普均{np.mean([r['sharpe'] for r in res]):5.2f} 盈利币数{sum(r['ret'] > 0 for r in res)}/4")

print("\n== E 风险定仓（每笔亏到止损只损失权益的 x%）样本外")
for risk in [None, 0.02, 0.01]:
    for s in SYMS:
        r = run_e(oos[s], trend=200, rsi_n=2, rsi_in=10, stop_atr=3.0, tp1_atr=2.5, exit_ema=50, risk=risk, bars_per_year=BPY)
        print(f"  risk={risk} {s:8s} {fmt(r)}")

print("\n== 滚动前进：每年 1 月用之前全部数据从小网格选参，验证当年")
grid = list(itertools.product([2, 3], [10, 20], [2.5, 3.0], [2.0, 2.5, 3.0], [20, 50]))
for s in SYMS:
    d = data[s]; line = []
    for y in range(2022, 2027):
        tr, te = d[d.index < f"{y}-01-01"], d[(d.index >= f"{y}-01-01") & (d.index < f"{y + 1}-01-01")]
        if len(te) < 200:
            continue
        best = max(grid, key=lambda g: run_e(tr, trend=200, rsi_n=g[0], rsi_in=g[1], stop_atr=g[2], tp1_atr=g[3],
                                             exit_ema=g[4], bars_per_year=BPY)["sharpe"])
        r = run_e(te, trend=200, rsi_n=best[0], rsi_in=best[1], stop_atr=best[2], tp1_atr=best[3], exit_ema=best[4], bars_per_year=BPY)
        bh = te["c"].iloc[-1] / te["c"].iloc[0] - 1
        line.append(f"{y}:{r['ret']:+.0%}(持有{bh:+.0%},胜{r['win']:.0%})")
    print(f"  {s:8s} " + "  ".join(line))
