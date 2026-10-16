"""用币安真实公共行情，跑三个内置策略的模拟盘实例（无 API Key，不下实盘单）。

用法：python live_paper_smoke.py <base_url>
"""
import asyncio
import sys
import time

import httpx

BASE = sys.argv[1]
SUFFIX = time.strftime("%H%M%S")  # 可重复运行：实例名唯一


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        r = await c.post("/api/setup", json={"username": "smoke", "password": "Smoke12345"})
        print("setup", r.status_code)
        r = await c.post("/api/auth/login", json={"username": "smoke", "password": "Smoke12345"})
        h = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
        price = (await c.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": "BTCUSDT"})).json()
        last = float(price["price"])
        specs = [
            (f"smoke-dca-{SUFFIX}", "dca_strategy", {"quote_amount": 50, "interval": 3600}),
            (f"smoke-grid-{SUFFIX}", "grid_strategy", {"grid_num": 6, "upper_price": round(last * 1.01, 2),
                                             "lower_price": round(last * 0.99, 2), "amount_per_grid": 0.0005}),
            (f"smoke-ma-{SUFFIX}", "ma_cross", {"timeframe": "1m", "fast_period": 3, "slow_period": 8, "amount": 0.0005,
                                      "check_interval": 5}),
        ]
        ids = {}
        for name, sid, params in specs:
            r = await c.post("/api/instances", headers=h, json={"name": name, "strategy_id": sid, "symbol": "BTC/USDT",
                                                                "paper": True, "paper_balance": 10000, "params": params})
            assert r.status_code == 201, r.text
            ids[name] = r.json()["data"]["id"]
            r = await c.post(f"/api/instances/{ids[name]}/start", headers=h)
            print("start", name, r.json()["code"], r.json().get("message"))
        await asyncio.sleep(20)
        ok = True
        for name, iid in ids.items():
            d = (await c.get(f"/api/instances/{iid}", headers=h)).json()["data"]
            orders = (await c.get(f"/api/instances/{iid}/orders", headers=h)).json()["data"]["items"]
            logs = (await c.get(f"/api/instances/{iid}/logs", headers=h)).json()["data"]["items"]
            errs = [l for l in logs if l["level"] == "error"]
            acct = (await c.get(f"/api/instances/{iid}/paper-account", headers=h)).json()["data"]
            stat = {}
            for o in orders:
                stat[o["status"]] = stat.get(o["status"], 0) + 1
            print(f"{name}: status={d['status']} orders={stat} equity={acct.get('equity')} errors={len(errs)}")
            for l in errs[:3]:
                print("   ERR", l["message"][:200])
            ok &= d["status"] == "running" and not errs
            r = await c.post(f"/api/instances/{iid}/stop", headers=h)
            print("   stop", r.json()["data"]["status"])
        print("RESULT", "PASS" if ok else "FAIL")


asyncio.run(main())
