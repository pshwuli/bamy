"""用上线内核出最终报告：4 币 × (1%/2% 风险) × 资金规模；资金规模影响的是交易所最小下单额是否卡住信号。"""
import numpy as np
from bt import SPLIT, load
from engine import fmt
from parity import replay
from helpers.signals import Params

SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
p = Params()
full = {s: load(s, "4h") for s in SYMS}
for risk in (0.01, 0.02):
    rows = []
    for s in SYMS:
        r = replay(full[s], p, risk_pct=risk, start=int((full[s].index < SPLIT).sum()))
        rows.append(r); print(f"risk {risk:.0%} {s:8s} {fmt(r)}")
    print(f"  ==> 平均 胜率{np.mean([r['win'] for r in rows]):.1%} PF{np.mean([r['pf'] for r in rows]):.2f} "
          f"年化{np.mean([r['cagr'] for r in rows]):.1%} 最大回撤(最差币){min(r['mdd'] for r in rows):.1%} "
          f"夏普{np.mean([r['sharpe'] for r in rows]):.2f} 盈利币{sum(r['ret']>0 for r in rows)}/4\n")
print("资金规模对信号的影响（risk 1%，BTC；最小下单额 5 USDT×2）")
for cap in (20, 50, 100, 500, 10000):
    s = "BTCUSDT"
    r = replay(full[s], p, risk_pct=0.01, min_notional=5.0 / cap, start=int((full[s].index < SPLIT).sum()))
    print(f"  本金 {cap:>6} USDT: 实际成交 {r['trades']:3d} 笔（满额 76 笔）年化 {r['cagr']:.1%}")
