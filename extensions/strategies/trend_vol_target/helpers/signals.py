"""日线趋势 + 波动率目标仓位：纯函数决策内核。

规则（默认参数来自 2020-2023 样本内搜索，2024-01 起样本外验证）：
- 趋势开：EMA(fast) > EMA(slow) 且收盘 > EMA(slow)，否则空仓
- 目标仓位 = min(1, 目标年化波动 / 近 vol_n 日实际年化波动) × max_alloc
- 仓位偏离目标超过 rebalance_band 才调仓，减少手续费
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def ema(values: list[float], n: int) -> list[float]:
    """EMA，用前 n 根的简单均值做种子（截断窗口时比"从第一根开始"更接近全历史 EMA）。"""
    if len(values) < n:
        return [sum(values) / len(values)] * len(values) if values else []
    k = 2 / (n + 1)
    seed = sum(values[:n]) / n
    out = [seed] * n
    for v in values[n:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def realized_vol(closes: list[float], n: int, periods_per_year: float) -> float | None:
    if len(closes) < n + 1:
        return None
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - n, len(closes))]
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


@dataclass
class Params:
    fast: int = 10
    slow: int = 50
    vol_n: int = 30
    target_vol: float = 0.6
    max_alloc: float = 1.0
    rebalance_band: float = 0.2
    periods_per_year: float = 365

    @property
    def warmup(self) -> int:
        # EMA 需要约 6 倍周期的历史才与全历史值收敛到 0.1% 以内
        return max(self.slow * 6, self.vol_n + 1)


def target_weight(closes: list[float], p: Params) -> float | None:
    """返回目标持仓占权益的比例（0~max_alloc）；数据不足返回 None。"""
    if len(closes) < p.warmup:
        return None
    f, s = ema(closes, p.fast)[-1], ema(closes, p.slow)[-1]
    if not (f > s and closes[-1] > s):
        return 0.0
    vol = realized_vol(closes, p.vol_n, p.periods_per_year)
    if not vol:
        return 0.0
    return min(1.0, p.target_vol / vol) * p.max_alloc


def needs_rebalance(current: float, target: float, band: float) -> bool:
    if target == 0.0:
        return current > 0.0
    if current == 0.0:
        return True
    return abs(current - target) / target > band
