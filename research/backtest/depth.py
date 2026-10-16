"""实测币安现货盘口：在 0.05% / 0.1% / 0.3% 滑点内一次市价单最多能成交多少 USDT（买单吃卖盘）。取 5 次中位数。"""
import json, statistics, time, urllib.request
out = {}
for sym in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]:
    caps = {0.0005: [], 0.001: [], 0.003: []}
    for _ in range(5):
        d = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/depth?symbol={sym}&limit=5000", timeout=15))
        asks = [(float(p), float(q)) for p, q in d["asks"]]
        best = asks[0][0]
        for tol in caps:
            caps[tol].append(sum(p * q for p, q in asks if p <= best * (1 + tol)))
        time.sleep(1)
    out[sym] = {k: statistics.median(v) for k, v in caps.items()}
    print(sym, "  ".join(f"{k*100:.2f}%内≈{v/1000:,.0f}k USDT" for k, v in out[sym].items()))
