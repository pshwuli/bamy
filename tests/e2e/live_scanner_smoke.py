"""部署后冒烟：用币安真实行情跑全市场扫描（现货 + U 本位合约，模拟盘），确认日志出现"粗筛 N → M"。

用法：python live_scanner_smoke.py <base_url> <用户名> <密码>
（用户不存在会先注册；只建模拟盘实例，结束后停止并删除。）
"""
import asyncio
import sys
import time

import httpx

BASE, USER, PWD = sys.argv[1], sys.argv[2], sys.argv[3]
SUFFIX = time.strftime("%H%M%S")


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        await c.post("/api/auth/register", json={"username": USER, "password": PWD})
        r = await c.post("/api/auth/login", json={"username": USER, "password": PWD})
        h = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
        specs = [
            (f"scan-spot-{SUFFIX}", "spot", {}),
            (f"scan-fut-{SUFFIX}", "futures", {"direction": "both", "leverage": 2}),
        ]
        ids = {}
        for name, mt, params in specs:
            body = {"name": name, "strategy_id": "market_scanner", "market_type": mt, "paper": True,
                    "paper_balance": 1000, "params": params}
            if mt == "futures":
                body["paper_config"] = {"leverage": 2}
            r = await c.post("/api/instances", headers=h, json=body)
            assert r.status_code == 201, r.text
            ids[name] = r.json()["data"]["id"]
            print("create", name, r.json()["data"]["symbol"])
            r = await c.post(f"/api/instances/{ids[name]}/start", headers=h)
            print("start", name, r.json()["code"], r.json().get("message"))
        ok = True
        for name, iid in ids.items():
            found = None
            for _ in range(30):
                await asyncio.sleep(3)
                logs = (await c.get(f"/api/instances/{iid}/logs", headers=h)).json()["data"]["items"]
                found = next((x["message"] for x in logs if "粗筛：" in x["message"]), None)
                if found:
                    break
            st = (await c.get(f"/api/instances/{iid}/status", headers=h)).json()["data"]
            print(name, "status", st["status"], "| 日志:", found)
            print("   界面策略状态:", (st.get("strategy_status") or {}).get("summary"))
            ok = ok and bool(found)
        for iid in ids.values():
            await c.post(f"/api/instances/{iid}/stop", headers=h, json={"action": "keep"})
            await c.delete(f"/api/instances/{iid}", headers=h)
        print("OK" if ok else "FAIL")
        sys.exit(0 if ok else 1)


asyncio.run(main())
