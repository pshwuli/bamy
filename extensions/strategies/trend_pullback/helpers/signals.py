"""趋势回调分批止盈：纯函数决策内核（实盘策略与回测共用同一份代码）。

规则（参数默认值来自 2020-2023 样本内搜索，2024-01 起样本外验证）：
- 趋势：收盘 > EMA(trend) 且 EMA(trend) 高于 slope_n 根之前（长期趋势向上才做）
- 入场：RSI(rsi_n) < rsi_in，即上升趋势里的短期超卖回调
- 初始止损：入场价 − stop_atr × ATR(atr_n)
- 第一目标：入场价 + tp1_atr × ATR，卖出 tp1_frac；之后止损抬到保本（含费成本）
- 剩余仓位：收盘跌破 EMA(exit_ema)、趋势失效或持仓超过 max_hold 根时卖出
- 仓位：risk_pct > 0 时按风险定仓（亏到止损只损失权益的 risk_pct），否则按 max_alloc 比例投入
"""
from __future__ import annotations

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


def rsi(values: list[float], n: int) -> list[float | None]:
    """Wilder RSI。前 n 根为 None。"""
    out: list[float | None] = [None] * len(values)
    if len(values) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0); losses += max(-d, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(closes)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


@dataclass
class Params:
    trend: int = 200
    slope_n: int = 10
    rsi_n: int = 2
    rsi_in: float = 10.0
    atr_n: int = 14
    stop_atr: float = 3.0
    tp1_atr: float = 2.5
    tp1_frac: float = 0.5
    exit_ema: int = 50
    max_hold: int = 60

    @property
    def warmup(self) -> int:
        # EMA 需要约 3 倍周期的历史才与全历史值足够接近
        return max(self.trend * 3 + self.slope_n, self.exit_ema * 3, self.atr_n * 5, self.rsi_n * 10)


@dataclass
class Snapshot:
    """最近一根已收盘 K 线的指标。"""
    close: float
    up: bool
    rsi: float | None
    atr: float | None
    exit_ema: float


def snapshot(highs: list[float], lows: list[float], closes: list[float], p: Params) -> Snapshot | None:
    if len(closes) < p.warmup:
        return None
    t = ema(closes, p.trend)
    up = closes[-1] > t[-1] and t[-1] > t[-1 - p.slope_n]
    return Snapshot(close=closes[-1], up=up, rsi=rsi(closes, p.rsi_n)[-1], atr=atr(highs, lows, closes, p.atr_n)[-1],
                    exit_ema=ema(closes, p.exit_ema)[-1])


def should_enter(s: Snapshot, p: Params) -> bool:
    return s.up and s.rsi is not None and s.rsi < p.rsi_in and s.atr is not None and s.atr > 0


def should_exit(s: Snapshot, p: Params, *, took_tp1: bool, bars_held: int) -> str | None:
    """返回退出原因或 None（止损与第一目标在盘中按价位处理，不在这里）。"""
    if not s.up:
        return "趋势失效"
    if took_tp1 and s.close < s.exit_ema:
        return f"跌破 EMA{p.exit_ema}"
    if bars_held >= p.max_hold:
        return f"持仓超过 {p.max_hold} 根"
    return None


def position_quote(equity: float, cash: float, price: float, atr_value: float, p: Params, *,
                   risk_pct: float, max_alloc: float, min_notional: float) -> float:
    """本次买入要花多少计价币。返回 0 表示金额不够交易所最小下单额，跳过本次信号。"""
    cap = min(cash, equity * max_alloc)
    if risk_pct > 0:
        stop_dist = p.stop_atr * atr_value / price
        cap = min(cap, equity * risk_pct / max(stop_dist, 1e-9))
    # 分批止盈要求卖出的一半也满足最小下单额，所以这里按两倍检查
    need = min_notional * (2 if p.tp1_frac < 1 else 1)
    return cap if cap >= need else 0.0
