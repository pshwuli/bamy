"""第三轮：日线趋势状态持有（单币）与多币动量轮动（组合）。全部样本外 2024-01 起验证，参数只用 2020-2023 选。"""
import itertools

import numpy as np
import pandas as pd

from bt import SPLIT, ema, load, sma
from engine import FEE, SLIP

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
D = {s: load(s, "1d") for s in SYMS}
C = pd.DataFrame({s: d["c"] for s, d in D.items()}).dropna()


def perf(eq: pd.Series, n_trades: int, wins: int, bpy=365):
    r = eq.pct_change().fillna(0); yrs = len(eq) / bpy
    return dict(ret=eq.iloc[-1] - 1, cagr=eq.iloc[-1] ** (1 / yrs) - 1, mdd=(eq / eq.cummax() - 1).min(),
                sharpe=r.mean() / r.std() * np.sqrt(bpy) if r.std() > 0 else 0, trades=n_trades,
                win=wins / n_trades if n_trades else 0)


def regime_single(c: pd.Series, fast=20, slow=100, vol_n=30, target=None):
    """日线：EMA(fast) > EMA(slow) 且收盘 > EMA(slow) 持有，否则空仓。可选波动率目标仓位。"""
    on = (ema(c, fast) > ema(c, slow)) & (c > ema(c, slow))
    w = on.astype(float)
    if target:
        vol = c.pct_change().rolling(vol_n).std() * np.sqrt(365)
        w = w * (target / vol).clip(upper=1.0)
    w = w.shift().fillna(0)                      # 次日生效
    ret = c.pct_change().fillna(0) * w - w.diff().abs().fillna(0) * (FEE + SLIP)
    eq = (1 + ret).cumprod()
    # 交易统计：一次 0→>0→0 为一笔
    seg = (w > 0).astype(int).diff().fillna(0)
    starts, ends = list(c.index[seg == 1]), list(c.index[seg == -1])
    pnl = [c[e] / c[s] - 1 - 2 * (FEE + SLIP) for s, e in zip(starts, ends)]
    return eq, len(pnl), sum(p > 0 for p in pnl)


def rotation(C: pd.DataFrame, look=60, top=1, abs_ma=100, rebalance=7):
    """每 rebalance 天：在 4 币里选过去 look 天收益最高的 top 个，且自身在 SMA(abs_ma) 之上才持有，否则现金。"""
    mom = C / C.shift(look) - 1
    ok = C > sma(C, abs_ma)
    w = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    cur = pd.Series(0.0, index=C.columns)
    for i, t in enumerate(C.index):
        if i % rebalance == 0 and not mom.loc[t].isna().any():
            m = mom.loc[t].where(ok.loc[t])
            pick = m.dropna().sort_values(ascending=False).head(top)
            cur = pd.Series(0.0, index=C.columns)
            if len(pick):
                cur[pick.index] = 1.0 / top
        w.loc[t] = cur
    w = w.shift().fillna(0)
    ret = (C.pct_change().fillna(0) * w).sum(axis=1) - w.diff().abs().sum(axis=1).fillna(0) * (FEE + SLIP)
    return (1 + ret).cumprod()


def show(name, eq, n=0, w=0):
    p = perf(eq, n, w)
    print(f"  {name:34s} 收益{p['ret']:8.1%} 年化{p['cagr']:7.1%} 回撤{p['mdd']:6.1%} 夏普{p['sharpe']:5.2f}"
          + (f" 交易{p['trades']:3d} 胜率{p['win']:5.1%}" if n else ""))


if __name__ == "__main__":
    print("== 单币日线趋势状态（样本内选参）")
    best = {}
    for fast, slow in itertools.product([10, 20, 50], [50, 100, 200]):
        if fast >= slow:
            continue
        sh = np.mean([perf(*regime_single(C[s][C.index < SPLIT], fast, slow))["sharpe"] for s in SYMS])
        best[(fast, slow)] = sh
    fs = max(best, key=best.get)
    print(f"  样本内最优 EMA{fs[0]}/{fs[1]}  夏普均值 {best[fs]:.2f}；全部组合：" +
          ", ".join(f"{k[0]}/{k[1]}:{v:.2f}" for k, v in sorted(best.items(), key=lambda x: -x[1])))
    for s in SYMS:
        oc = C[s][C.index >= SPLIT]
        show(f"{s} 样本外 趋势状态 EMA{fs[0]}/{fs[1]}", *regime_single(oc, *fs))
        show(f"{s} 样本外 同上+波动率目标60%", *regime_single(oc, *fs, target=0.6))
        show(f"{s} 样本外 持有不动", oc / oc.iloc[0])

    print("\n== 多币动量轮动（样本内选参）")
    res = {}
    for look, top, ma, rb in itertools.product([20, 30, 60, 90], [1, 2], [50, 100, 200], [7, 14]):
        res[(look, top, ma, rb)] = perf(rotation(C[C.index < SPLIT], look, top, ma, rb), 0, 0)["sharpe"]
    for k in sorted(res, key=lambda k: -res[k])[:5]:
        oc = C[C.index >= SPLIT]
        print(f"  参数 look{k[0]} top{k[1]} ma{k[2]} rb{k[3]}  样本内夏普 {res[k]:.2f}")
        show("    样本外", rotation(oc, *k))
    oc = C[C.index >= SPLIT]
    show("等权持有 4 币（对照）", (oc / oc.iloc[0]).mean(axis=1))
