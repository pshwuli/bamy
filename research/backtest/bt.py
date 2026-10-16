"""BAMY 策略研究回测器：币安现货 1h K 线（data.binance.vision），只做多，按收盘价成交。

成本：每边手续费 0.1%（币安现货默认），每边滑点 0.05%。信号在 K 线收盘计算、下一根开盘成交（无前视）。
切分：2020-01 ~ 2023-12 为样本内（调参），2024-01 ~ 数据末为样本外（只验证，不调参）。
"""
from __future__ import annotations

import glob
import io
import zipfile
from dataclasses import dataclass

import numpy as np
import pandas as pd

FEE = 0.001
SLIP = 0.0005
COST = FEE + SLIP
SPLIT = pd.Timestamp("2024-01-01")


def load(sym: str, tf: str = "4h") -> pd.DataFrame:
    frames = []
    for f in sorted(glob.glob(f"/tmp/bt/data/{sym}-1h-*.zip")):
        with zipfile.ZipFile(f) as z:
            raw = z.read(z.namelist()[0]).decode()
        df = pd.read_csv(io.StringIO(raw), header=None, usecols=range(6),
                         names=["t", "o", "h", "l", "c", "v"])
        if df["t"].iloc[0] > 1e14:   # 2025 起为微秒
            df["t"] //= 1000
        frames.append(df)
    df = pd.concat(frames).drop_duplicates("t").sort_values("t")
    df.index = pd.to_datetime(df["t"], unit="ms")
    df = df[["o", "h", "l", "c", "v"]].astype(float)
    if tf != "1h":
        df = df.resample(tf).agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    return df


# ---------------------------------------------------------------- 指标
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()


def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr(df, n):
    pc = df["c"].shift()
    tr = pd.concat([df["h"] - df["l"], (df["h"] - pc).abs(), (df["l"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


# ---------------------------------------------------------------- 回测内核
@dataclass
class Result:
    trades: int
    win_rate: float
    pf: float
    ret: float
    cagr: float
    mdd: float
    sharpe: float
    exposure: float
    avg_trade: float
    bh_ret: float
    bh_mdd: float

    def row(self) -> str:
        return (f"交易{self.trades:4d} 胜率{self.win_rate:5.1%} 盈亏比PF{self.pf:5.2f} 总收益{self.ret:8.1%} "
                f"年化{self.cagr:7.1%} 最大回撤{self.mdd:6.1%} 夏普{self.sharpe:5.2f} 持仓时间{self.exposure:5.1%} "
                f"单笔均{self.avg_trade:6.2%} | 持有不动 {self.bh_ret:8.1%} 回撤{self.bh_mdd:6.1%}")


def run(df: pd.DataFrame, entry: pd.Series, exit_: pd.Series, *, stop: pd.Series | None = None,
        size: pd.Series | None = None, bars_per_year: float = 6 * 365) -> Result:
    """entry/exit 为收盘时计算的布尔信号，下一根开盘成交。stop 为每根的止损价（盘中触发按止损价成交）。
    size 为每次开仓投入的权益比例（0~1），默认全仓。"""
    o, h, l, c = (df[k].to_numpy() for k in "ohlc")
    en, ex = entry.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
    st = stop.to_numpy() if stop is not None else None
    sz = size.clip(0, 1).fillna(1).to_numpy() if size is not None else np.ones(len(df))
    eq = np.empty(len(df)); cash, units, frac = 1.0, 0.0, 0.0
    entry_px, trades, in_bars = 0.0, [], 0
    pending_in = pending_out = False
    for i in range(len(df)):
        if pending_out and units > 0:
            px = o[i] * (1 - SLIP); cash += units * px * (1 - FEE)
            trades.append(px * (1 - FEE) / entry_px - 1); units = 0.0
        if pending_in and units == 0:
            frac = sz[i - 1]
            px = o[i] * (1 + SLIP); spend = cash * frac
            units = spend * (1 - FEE) / px; cash -= spend; entry_px = px / (1 - FEE)
        pending_in = pending_out = False
        if units > 0 and st is not None and not np.isnan(st[i - 1]) and l[i] <= st[i - 1]:
            px = min(o[i], st[i - 1]) * (1 - SLIP); cash += units * px * (1 - FEE)
            trades.append(px * (1 - FEE) / entry_px - 1); units = 0.0
        if units > 0:
            in_bars += 1
        eq[i] = cash + units * c[i]
        if units > 0 and ex[i]:
            pending_out = True
        elif units == 0 and en[i]:
            pending_in = True
    eq_s = pd.Series(eq, index=df.index)
    rets = eq_s.pct_change().fillna(0)
    years = len(df) / bars_per_year
    tr = np.array(trades) if trades else np.array([0.0])
    wins, losses = tr[tr > 0].sum(), -tr[tr < 0].sum()
    bh = c / c[0]
    return Result(
        trades=len(trades), win_rate=float((tr > 0).mean()) if trades else 0.0,
        pf=float(wins / losses) if losses > 0 else float("inf"),
        ret=float(eq[-1] - 1), cagr=float(eq[-1] ** (1 / years) - 1) if years > 0 else 0.0,
        mdd=float((eq_s / eq_s.cummax() - 1).min()),
        sharpe=float(rets.mean() / rets.std() * np.sqrt(bars_per_year)) if rets.std() > 0 else 0.0,
        exposure=in_bars / len(df), avg_trade=float(tr.mean()),
        bh_ret=float(bh[-1] - 1), bh_mdd=float((pd.Series(bh) / pd.Series(bh).cummax() - 1).min()),
    )
