"""最终对比：同一样本外窗口（2024-01 起）、同一成本，新旧策略逐币对比。新策略参数全部来自样本内。"""
import numpy as np

from bt import SPLIT, load, run
import candidates as Cd
from engine import fmt
from regime import regime_single, perf
from strat_v2 import run_e

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
E = dict(trend=200, rsi_n=2, rsi_in=10, stop_atr=3.0, tp1_atr=2.5, exit_ema=50)

rows = {"旧 均线交叉7/25(4h)": [], "旧 网格(无趋势过滤)": [], "新 趋势回调分批(4h)": [], "新 趋势回调分批 1%风险": [],
        "新 日线趋势+波动率仓位": [], "持有不动": []}
for s in SYMS:
    d4 = load(s, "4h"); d4 = d4[d4.index >= SPLIT]
    d1 = load(s, "1d"); c1 = d1["c"][d1.index >= SPLIT]
    e, x, st, sz = Cd.old_ma_cross(d4); r = run(d4, e, x)
    rows["旧 均线交叉7/25(4h)"].append(dict(win=r.win_rate, pf=r.pf, cagr=r.cagr, mdd=r.mdd, sharpe=r.sharpe, trades=r.trades))
    rows["新 趋势回调分批(4h)"].append(run_e(d4, **E))
    rows["新 趋势回调分批 1%风险"].append(run_e(d4, risk=0.01, **E))
    eq, n, w = regime_single(c1, 10, 50, target=0.6); p = perf(eq, n, w); p["pf"] = float("nan")
    rows["新 日线趋势+波动率仓位"].append(p)
    bh = c1 / c1.iloc[0]
    rows["持有不动"].append(dict(win=np.nan, pf=np.nan, cagr=bh.iloc[-1] ** (365 / len(bh)) - 1, mdd=(bh / bh.cummax() - 1).min(),
                              sharpe=(bh.pct_change().mean() / bh.pct_change().std() * np.sqrt(365)), trades=0))

print(f"{'策略':26s} {'胜率':>6s} {'PF':>5s} {'年化':>7s} {'最大回撤':>8s} {'夏普':>5s} {'盈利币':>5s}   （4 币平均，样本外 2024-01 起）")
for k, v in rows.items():
    if not v:
        continue
    f = lambda key: np.nanmean([x[key] for x in v])
    print(f"{k:26s} {f('win'):6.1%} {f('pf'):5.2f} {f('cagr'):7.1%} {min(x['mdd'] for x in v):8.1%} {f('sharpe'):5.2f} "
          f"{sum(x['cagr'] > 0 for x in v)}/4")
