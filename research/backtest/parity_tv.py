import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "extensions/strategies/trend_vol_target"))
import numpy as np, pandas as pd
from bt import SPLIT, load
from engine import FEE, SLIP
from helpers.signals import Params, needs_rebalance, target_weight
from regime import perf

def replay(full: pd.Series, p: Params):
    """full 含样本外之前的历史作预热；只在样本外区间交易和计权益。"""
    vals = full.tolist(); start = int((full.index < SPLIT).sum()); c = full.iloc[start:]
    cash, qty = 1.0, 0.0; eq = []; trades = 0
    for i in range(start, len(vals)):
        px = vals[i]
        if i >= 1:
            w = target_weight(vals[max(0, i - p.warmup - 50):i], p)   # 只用到昨天收盘
            if w is not None:
                e = cash + qty * px; cur = qty * px / e
                if needs_rebalance(cur, w, p.rebalance_band):
                    d = (w - cur) * e
                    if d > 0:
                        q = d * (1 - FEE) / (px * (1 + SLIP)); qty += q; cash -= d
                    else:
                        q = min(-d / px, qty); cash += q * px * (1 - SLIP) * (1 - FEE); qty -= q
                    trades += 1
        eq.append(cash + qty * px)
    return perf(pd.Series(eq, index=c.index), 0, 0)

for band in (0.0, 0.2):
    rows = []
    for s in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]:
        d = load(s, "1d")
        rows.append(replay(d["c"], Params(rebalance_band=band)))
    print(f"调仓阈值 {band:.0%}: 年化均 {np.mean([r['cagr'] for r in rows]):.1%}  回撤最差 {min(r['mdd'] for r in rows):.1%}  "
          f"夏普均 {np.mean([r['sharpe'] for r in rows]):.2f}  盈利币 {sum(r['ret']>0 for r in rows)}/4  "
          + " ".join(f"{s[:3]}:{r['cagr']:+.0%}/{r['mdd']:.0%}" for s, r in zip(['BTC','ETH','BNB','SOL'], rows)))
