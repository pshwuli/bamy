#!/usr/bin/env python3
"""入场信号边际检验：信号出现后固定持有 N 分钟，看扣费前的收益分布。

用法:
    python edge_study.py all [进程数]     # 自动按核数分片并行跑完，再合并成 $EDGE_OUT/edge_merged.npz
    python edge_study.py <k> <K>          # 只跑第 k 片(共 K 片)，可分发到多台机器，之后用 merge 合并
    python edge_study.py merge <分片文件...>   # 把若干 edge_shard_*.npz 合并
环境变量: EDGE_DATA(1m npz 目录,默认 /root/bamy-data/um_1m) EDGE_OUT(输出目录,默认 /tmp/edge_out)
          EDGE_LIMIT(只跑前 N 个币，仅用于冒烟测试)
口径: 信号用已收盘 1m K 线（收盘价/窗口首根开盘价、距窗口最高价、滚动 24h 成交额）；
      入场 = 下一根开盘价；退出 = 持有 H 分钟后的收盘价。同币信号至少间隔 REFRACT 分钟（去重叠）。
只检验做多；不含手续费/滑点（分析脚本里再扣）。槽位限制、止损止盈不在这一步。
"""
import datetime as dt
import glob
import os
import sys
import time

import numpy as np

DATA = os.environ.get("EDGE_DATA", "/root/bamy-data/um_1m")
OUT = os.environ.get("EDGE_OUT", "/tmp/edge_out")
HOR = (15, 60, 240, 480)                          # 持有分钟数
GRID = {60: (5, 8, 12, 20), 240: (5, 10, 15, 20, 30), 1440: (10, 20, 30, 50)}   # 窗口分钟 -> 涨幅阈值%
DISTS = (-1, -3, -5, -10)                         # 距窗口高点 >= 该值%
VOLS = (500e4, 2000e4)                            # 24h 成交额下限（USDT）
REFRACT = 240
D = 1200                                          # 日期桶数（2024-01-01 起）
DAY0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp()) // 86400
NC = 18                                           # cnt, sum*4, sumsq*4, clipsum*4, wins*4, gapsum


def rmax(x, w):
    """滚动最大值(van Herk)：返回 out[k] = max(x[k..k+w-1])，对应窗口结束位置 j=k+w-1。"""
    n = len(x)
    pad = (-n) % w
    b = np.concatenate([x, np.full(pad, -np.inf)]).reshape(-1, w)
    pre = np.maximum.accumulate(b, axis=1).ravel()
    suf = np.maximum.accumulate(b[:, ::-1], axis=1)[:, ::-1].ravel()
    k = np.arange(0, n - w + 1)
    return np.maximum(suf[k], pre[k + w - 1])


def dedupe(idx, gap):
    out, k, n = [], 0, len(idx)
    while k < n:
        out.append(idx[k])
        k = int(np.searchsorted(idx, idx[k] + gap, "left"))
    return np.asarray(out, dtype=np.int64)


def add(acc, key, day, rets, gap):
    a = acc.setdefault(key, np.zeros((D, NC)))
    bc = lambda wts: np.bincount(day, weights=wts, minlength=D)[:D]
    a[:, 0] += bc(None)
    for h in range(len(HOR)):
        r = rets[:, h]
        a[:, 1 + h] += bc(r)
        a[:, 5 + h] += bc(r * r)
        a[:, 9 + h] += bc(np.clip(r, -0.3, 0.3))
        a[:, 13 + h] += bc((r > 0).astype(float))
    a[:, 17] += bc(gap)


def process(path, acc, base):
    z = np.load(path)
    t = z["t"].astype("i8"); o = z["o"].astype("f8"); h = z["h"].astype("f8")
    c = z["c"].astype("f8"); qv = z["qv"].astype("f8")
    n, mh = len(t), max(HOR)
    if n < 3000:
        return
    cs = np.concatenate([[0.0], np.cumsum(qv)])
    q24 = np.full(n, np.nan)
    q24[1439:] = cs[1440:] - cs[:n - 1439]
    last = n - 1 - mh                                     # 最后一个能算完 480 分钟的信号位置
    def fwd(idx):
        return np.stack([c[idx + H] / o[idx + 1] - 1 for H in HOR], 1)
    bi = np.arange(1439, last, 60)
    bi = bi[q24[bi] >= 500e4]
    if bi.size:
        r = fwd(bi); day = t[bi] // 86400 - DAY0
        b = base.setdefault("base", np.zeros((D, 9)))
        b[:, 0] += np.bincount(day, minlength=D)[:D]
        for k in range(4):
            b[:, 1 + k] += np.bincount(day, weights=r[:, k], minlength=D)[:D]
            b[:, 5 + k] += np.bincount(day, weights=r[:, k] ** 2, minlength=D)[:D]
    for w, ths in GRID.items():
        j = np.arange(w - 1, n)
        chg = (c[j] / o[j - w + 1] - 1) * 100
        dist = (c[j] / rmax(h, w) - 1) * 100
        m0 = (j >= 1439) & (j <= last) & (chg >= min(ths)) & (dist >= min(DISTS)) & (q24[j] >= min(VOLS))
        jj, cc, dd, qq = j[m0], chg[m0], dist[m0], q24[j][m0]
        for th in ths:
            for d in DISTS:
                for v in VOLS:
                    idx = jj[(cc >= th) & (dd >= d) & (qq >= v)]
                    if idx.size == 0:
                        continue
                    idx = dedupe(idx, REFRACT)
                    add(acc, (w, th, d, int(v)), t[idx] // 86400 - DAY0, fwd(idx), o[idx + 1] / c[idx] - 1)


def run_shard(k, K):
    files = sorted(glob.glob(f"{DATA}/*.npz"))
    if os.environ.get("EDGE_LIMIT"):
        files = files[:int(os.environ["EDGE_LIMIT"])]
    files = files[k::K]
    acc, base, skipped, t0 = {}, {}, [], time.time()
    for i, f in enumerate(files, 1):
        try:
            process(f, acc, base)
        except Exception as e:  # noqa: BLE001
            skipped.append(os.path.basename(f))
            print("跳过", os.path.basename(f), repr(e), flush=True)
        if i % 20 == 0:
            print(f"shard{k}/{K}: {i}/{len(files)} {time.time() - t0:.0f}s", flush=True)
    keys = list(acc)
    os.makedirs(OUT, exist_ok=True)
    path = f"{OUT}/edge_shard_{k}of{K}.npz"
    np.savez(path, keys=np.array(keys, dtype="f8").reshape(-1, 4), base=base.get("base", np.zeros((D, 9))),
             data=np.stack([acc[x] for x in keys]) if keys else np.zeros((0, D, NC)),
             nfiles=len(files), skipped=np.array(skipped, dtype=str))
    print(f"shard{k}/{K} 完成: {len(files)} 个币, {len(keys)} 个组合, {time.time() - t0:.0f}s -> {path}", flush=True)
    return path


def merge_shards(paths, out):
    base, tot, nfiles, skipped = np.zeros((D, 9)), {}, 0, []
    for p in paths:
        z = np.load(p)
        base += z["base"]; nfiles += int(z["nfiles"]); skipped += z["skipped"].tolist()
        for key, arr in zip(map(tuple, z["keys"].tolist()), z["data"]):
            tot[key] = tot.get(key, 0) + arr
    keys = sorted(tot)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    np.savez(out, keys=np.array(keys, dtype="f8"), base=base, data=np.stack([tot[x] for x in keys]),
             nfiles=nfiles, skipped=np.array(skipped, dtype=str))
    print(f"合并完成: {len(paths)} 个分片, {nfiles} 个币, {len(keys)} 个组合, 跳过 {len(skipped)} 个 -> {out}")
    return out


if __name__ == "__main__":
    import multiprocessing as mp
    a = sys.argv[1:]
    if a and a[0] == "merge":
        merge_shards(a[1:], f"{OUT}/edge_merged.npz")
    elif a and a[0] == "all":
        K = int(a[1]) if len(a) > 1 else (os.cpu_count() or 2)
        os.makedirs(OUT, exist_ok=True)
        ps = [mp.Process(target=run_shard, args=(k, K)) for k in range(K)]
        [p.start() for p in ps]; [p.join() for p in ps]
        if any(p.exitcode for p in ps):
            sys.exit("有分片异常退出，未合并；看上面的日志")
        merge_shards([f"{OUT}/edge_shard_{k}of{K}.npz" for k in range(K)], f"{OUT}/edge_merged.npz")
    elif len(a) == 2:
        run_shard(int(a[0]), int(a[1]))
    else:
        sys.exit(__doc__)
