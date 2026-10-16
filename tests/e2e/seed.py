"""给 UI 冒烟准备数据：管理员、一个账号（直接写库，绕开币安验证）、一个运行过的模拟盘实例、一个分享链接。

用法：python seed.py <base_url> <sqlite_path>；输出 "instance_id account_id share_token"。
"""
import asyncio
import sqlite3
import sys
import time

import httpx

BASE, DB = sys.argv[1], sys.argv[2]


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        await c.post("/api/setup", json={"username": "admin", "password": "Admin12345"})
        tok = (await c.post("/api/auth/login", json={"username": "admin", "password": "Admin12345"})).json()["data"]
        h = {"Authorization": "Bearer " + tok["access_token"]}
        sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[2] / 'backend'))
        from bamy.core.crypto import encrypt

        con = sqlite3.connect(DB)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        con.execute("INSERT INTO accounts (owner_id,name,api_key_enc,api_secret_enc,exchange,env,market_type,readonly,status,"
                    "created_at,updated_at) VALUES (1,'演示账号',?,?,'binance','testnet','spot',1,'offline',?,?)",
                    (encrypt("A" * 64), encrypt("B" * 64), now, now))
        con.commit()
        acc_id = con.execute("SELECT max(id) FROM accounts").fetchone()[0]
        con.close()
        r = await c.post("/api/instances", headers=h, json={
            "name": "演示定投", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "paper": True,
            "paper_balance": 10000, "params": {"quote_amount": 50, "interval": 3600}})
        iid = r.json()["data"]["id"]
        await c.post(f"/api/instances/{iid}/start", headers=h)
        await asyncio.sleep(6)
        await c.post("/api/instances", headers=h, json={
            "name": "演示网格", "strategy_id": "ma_cross", "symbol": "ETH/USDT", "paper": True, "paper_balance": 5000})
        r = await c.post(f"/api/instances/{iid}/share-tokens", headers=h, json={
            "expires_in_days": 7, "show_equity": True, "show_positions": True, "show_orders": True,
            "show_params": True, "show_logs": True})
        token = r.json()["data"]["token"]
        me = (await c.get("/api/auth/me", headers=h)).json()["data"]
        await c.post("/api/plugin-bindings", headers=h, json={
            "plugin_id": "audit_export", "scope": "user", "scope_id": me["id"], "settings": {}})
        print(iid, acc_id, token)


asyncio.run(main())
