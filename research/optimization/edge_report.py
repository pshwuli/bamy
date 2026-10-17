#!/usr/bin/env python3
"""汇总 edge_study 的合并结果：样本内/外、扣费后收益、按日聚类的 t 值、多重比较校正。

用法: python edge_report.py <edge_merged.npz> [--split 2025-07-01] [--cost 0.2] [--min-n 300]
  --cost   一来一回的总成本(%)，默认 0.2（手续费 0.1 + 滑点 0.1）；暴涨小币建议再跑 0.4 看敏感性
  --split  样本内/外分界日（含起点算样本外）
只看"信号出现后固定持有 H 分钟"的收益，不含止损止盈、槽位限制。做多(long)与反向做空(fade=把多头收益取负)都列出。
"""
import argparse
import csv
import datetime as dt
from statistics import NormalDist

import numpy as np

HOR = (15, 60, 240, 480)
DAY0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp()) // 86400


def stats(a, h, cost, sl):
    """a: (D,18) 日汇总。返回 n, 总体毛均值, 净均值(多头), 日聚类 t(多头), 胜率(毛)。"""
    cnt = a[sl, 0]; s = a[sl, 1 + h]
    n = cnt.sum()
    if n < 2:
        return n, np.nan, np.nan, np.nan, np.nan
    gross = s.sum() / n
    net_day = s - cnt * cost / 100                     # 每天的净收益之和
    days = cnt > 0
    per_day = net_day[days]
    t = np.nan
    if days.sum() > 2 and per_day.std(ddof=1) > 0:     # 按日聚类：把每天当作一个样本，防同日多币信号相关
        t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(days.sum())) * 1.0
    return n, gross, gross - cost / 100, t, a[sl, 13 + h].sum() / n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("npz"); ap.add_argument("--split", default="2025-07-01")
    ap.add_argument("--cost", type=float, default=0.2); ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--top", type=int, default=15)
    o = ap.parse_args()
    z = np.load(o.npz)
    keys, data, base = z["keys"], z["data"], z["base"]
    cut = int(dt.datetime.fromisoformat(o.split).replace(tzinfo=dt.UTC).timestamp()) // 86400 - DAY0
    IS, OOS = slice(0, cut), slice(cut, None)
    print(f"币数 {int(z['nfiles'])}，跳过 {len(z['skipped'])}，组合 {len(keys)}；样本内 < {o.split} <= 样本外；总成本 {o.cost}%")
    print("基线(无条件，每60分钟采样一次、24h成交额>=500万)的毛均值:")
    for h, H in enumerate(HOR):
        bi = base[IS, 1 + h].sum() / max(base[IS, 0].sum(), 1); bo = base[OOS, 1 + h].sum() / max(base[OOS, 0].sum(), 1)
        print(f"  持有{H:>3}分钟: 样本内 {bi*100:+.4f}%  样本外 {bo*100:+.4f}%")
    rows, m = [], len(keys) * len(HOR) * 2
    zcrit = NormalDist().inv_cdf(1 - 0.05 / 2 / m)
    for key, a in zip(keys, data):
        w, th, d, v = int(key[0]), key[1], key[2], int(key[3] / 1e4)
        for h, H in enumerate(HOR):
            ni, gi, neti, ti, _ = stats(a, h, o.cost, IS)
            no, go, neto, to, wo = stats(a, h, o.cost, OOS)
            rows.append(dict(win=w, chg=th, dist=d, vol_wan=v, hold=H, n_is=int(ni), gross_is=gi, n_oos=int(no),
                             gross_oos=go, net_long_oos=neto, net_fade_oos=-go - o.cost / 100, t_long=to, win_oos=wo,
                             net_long_is=neti, t_long_is=ti))
    path = o.npz.replace(".npz", "_summary.csv")
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0])) if rows else None
        if wr: wr.writeheader(); wr.writerows(rows)
    ok = [r for r in rows if r["n_oos"] >= o.min_n and r["n_is"] >= o.min_n]
    print(f"\n共检验 {m} 个假设(组合×持有期×多空)；Bonferroni 5% 的 |t| 门槛 ≈ {zcrit:.2f}；样本量够的行 {len(ok)}；明细 {path}")
    for name, col, tcol in (("做多", "net_long_oos", "t_long"),):
        print(f"\n== {name}：样本外净收益 Top{o.top}（样本内外都 ≥{o.min_n} 笔；* = 过校正门槛）==")
        print("窗口 涨幅% 距高% 额(万) 持有 | 样本内n 内毛% 内净% | 样本外n 外毛% 外净% 胜率 t")
        for r in sorted(ok, key=lambda r: -(r[col] if r[col] == r[col] else -9))[:o.top]:
            star = "*" if r[tcol] == r[tcol] and abs(r[tcol]) >= zcrit else " "
            print(f"{r['win']:>4} {r['chg']:>5g} {r['dist']:>5g} {r['vol_wan']:>5} {r['hold']:>3} | "
                  f"{r['n_is']:>6} {r['gross_is']*100:+6.2f} {r['net_long_is']*100:+6.2f} | "
                  f"{r['n_oos']:>6} {r['gross_oos']*100:+6.2f} {r[col]*100:+6.2f} {r['win_oos']*100:4.0f}% {r[tcol]:+5.1f}{star}")
    pos = [r for r in ok if r["net_long_oos"] > 0 and r["net_long_is"] > 0]
    print(f"\n样本内外净收益都为正的做多组合: {len(pos)} / {len(ok)}；做多净收益样本外为正且 t 过校正门槛: "
          f"{sum(1 for r in ok if r['net_long_oos'] > 0 and r['t_long'] == r['t_long'] and r['t_long'] >= zcrit)}")
    print(f"反向做空(fade)样本外净收益为正的组合: {sum(1 for r in ok if r['net_fade_oos'] > 0)} / {len(ok)}")


if __name__ == "__main__":
    main()
