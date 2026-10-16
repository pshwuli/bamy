"""逐根 K 线事件回测：支持分批止盈、移动止损、限价网格。与上线策略同一套决策函数对接。

约定：策略在第 i 根收盘后决策，市价单按第 i+1 根开盘成交；止损/止盈/限价单在第 i+1 根盘中按价位触发。
成本：手续费 0.1%/边，市价单额外滑点 0.05%/边，限价单不计滑点。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FEE, SLIP = 0.001, 0.0005


@dataclass
class Book:
    cash: float = 1.0
    qty: float = 0.0
    cost: float = 0.0            # 当前持仓的含费成本
    trades: list = field(default_factory=list)   # 每笔"开仓→全平"的收益率
    legs: list = field(default_factory=list)     # 每次卖出的收益率（分批止盈计入）

    def buy(self, px: float, quote: float) -> None:
        quote = min(quote, self.cash)
        if quote <= 0:
            return
        q = quote * (1 - FEE) / px
        self.qty += q; self.cash -= quote; self.cost += quote

    def sell(self, px: float, frac: float = 1.0) -> None:
        if self.qty <= 0:
            return
        q = self.qty * frac
        proceeds = q * px * (1 - FEE)
        basis = self.cost * frac
        self.legs.append(proceeds / basis - 1)
        self.cash += proceeds; self.qty -= q; self.cost -= basis
        if frac >= 0.999:
            self.qty, self.cost = 0.0, 0.0

    def equity(self, px: float) -> float:
        return self.cash + self.qty * px


def stats(eq: pd.Series, trades: list[float], bars_per_year: float, price: pd.Series, exposure: float) -> dict:
    rets = eq.pct_change().fillna(0)
    years = len(eq) / bars_per_year
    tr = np.array(trades) if trades else np.array([0.0])
    wins, losses = tr[tr > 0].sum(), -tr[tr < 0].sum()
    bh = price / price.iloc[0]
    return {
        "trades": len(trades), "win": float((tr > 0).mean()) if trades else 0.0,
        "pf": float(wins / losses) if losses > 0 else float("inf"),
        "ret": float(eq.iloc[-1] - 1), "cagr": float(eq.iloc[-1] ** (1 / years) - 1),
        "mdd": float((eq / eq.cummax() - 1).min()),
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else 0.0,
        "expo": exposure, "avg": float(tr.mean()),
        "bh": float(bh.iloc[-1] - 1), "bh_mdd": float((bh / bh.cummax() - 1).min()),
    }


def fmt(s: dict) -> str:
    return (f"交易{s['trades']:4d} 胜率{s['win']:5.1%} PF{s['pf']:5.2f} 收益{s['ret']:8.1%} 年化{s['cagr']:7.1%} "
            f"回撤{s['mdd']:6.1%} 夏普{s['sharpe']:5.2f} 持仓{s['expo']:5.1%} 均{s['avg']:6.2%} | 持有 {s['bh']:7.1%} 回撤{s['bh_mdd']:6.1%}")
