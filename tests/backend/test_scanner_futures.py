"""全市场扫描 + U 本位合约模拟盘。"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import conftest
from bamy.engine.paper import FuturesBook, PaperConfig


def body(r):
    return r.json()


# ---------------------------------------------------------------- 合约模拟账户算术
def test_futures_book_long_short_and_liq():
    b = FuturesBook(Decimal(1000), {}, Decimal(0), PaperConfig(slippage=0, leverage=5))
    b.fill("a", "BTC/USDT:USDT", "sell", 0.08, 50000)         # 开空：名义 4000，保证金 800，手续费 2
    p = b.positions["BTC/USDT:USDT"]
    assert p["side"] == "short" and float(p["margin"]) == 800
    assert abs(float(b.balance) - 198) < 1e-9
    liq = float(FuturesBook.liq_price("short", Decimal(50000), 5))
    assert abs(liq - 50000 * 1.195) < 1e-6
    # 跌 10%：空单赚 400
    assert abs(float(b.equity({"BTC/USDT:USDT": 45000})) - (1000 - 2 + 400)) < 1e-6
    b.fill("b", "BTC/USDT:USDT", "buy", 0.08, 45000, reduce_only=True)
    assert "BTC/USDT:USDT" not in b.positions
    assert abs(float(b.balance) - (1000 - 2 + 400 - 1.8)) < 1e-6
    # 同向加仓均价、反手
    b.fill("c", "ETH/USDT:USDT", "buy", 1, 3000)
    b.fill("d", "ETH/USDT:USDT", "buy", 1, 3100)
    assert abs(float(b.positions["ETH/USDT:USDT"]["entry"]) - 3050) < 1e-9
    b.fill("e", "ETH/USDT:USDT", "sell", 3, 3050)               # 平 2 张多再开 1 张空
    assert b.positions["ETH/USDT:USDT"]["side"] == "short" and float(b.positions["ETH/USDT:USDT"]["amount"]) == 1


def test_futures_book_liquidation():
    b = FuturesBook(Decimal(1000), {}, Decimal(0), PaperConfig(slippage=0, leverage=10))
    b.fill("a", "ETH/USDT:USDT", "buy", 1, 3000)             # 多 1 张，保证金 300
    assert b.liquidations({"ETH/USDT:USDT": 2800}) == []
    hits = b.liquidations({"ETH/USDT:USDT": 2700})           # 强平价 3000*(1-0.1+0.005)=2715
    assert hits and hits[0]["side"] == "long" and abs(hits[0]["loss"] - 300) < 1e-6
    assert not b.positions
    assert abs(float(b.equity({})) - (1000 - 300 - 1.5)) < 1e-6


def test_futures_reduce_only_without_position_rejected():
    from bamy.engine.adapters import ExchangeError

    b = FuturesBook(Decimal(100), {}, Decimal(0), PaperConfig(slippage=0, leverage=2))
    try:
        b.fill("x", "BTC/USDT:USDT", "sell", 0.001, 50000, reduce_only=True)
        raise AssertionError("应拒绝")
    except ExchangeError as exc:
        assert exc.code == 42244


# ---------------------------------------------------------------- 接口：合约实例
async def test_futures_instance_open_short_and_close(client, user_h):
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "fut-dca", "strategy_id": "market_scanner", "symbol": "", "market_type": "futures",
        "paper": True, "paper_balance": 1000, "paper_config": {"leverage": 3},
        "params": {"direction": "short", "min_change_pct": 10, "max_from_high_pct": -5, "min_quote_volume": 100,
                   "scan_seconds": 10, "slots": 1, "position_pct": 50, "leverage": 3}})
    assert body(r)["code"] == 0, r.text
    d = body(r)["data"]
    assert d["symbol"] == "ALL/USDT:USDT" and d["market_type"] == "futures"
    # 杠杆超过风控默认上限 5 倍被拒
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "fut-hi", "strategy_id": "market_scanner", "market_type": "futures", "paper": True,
        "paper_balance": 1000, "paper_config": {"leverage": 10}, "params": {}})
    assert body(r)["code"] == 42247
    # 现货策略不支持合约
    r = await client.post("/api/instances", headers=user_h, json={
        "name": "fut-bad", "strategy_id": "dca_strategy", "symbol": "BTC/USDT", "market_type": "futures",
        "paper": True, "paper_balance": 1000, "params": {"quote_amount": 10, "interval": 3600}})
    assert body(r)["code"] == 42224


async def test_scanner_spot_buys_and_stops_out(client, user_h):
    from bamy.engine.market import market

    conftest.WINDOW["PUMP/USDT"] = {"change_4h": 25.0, "high_4h": 1.02, "quote_volume": 2e7}
    conftest.PRICES["PUMP/USDT"] = 1.0
    market.scan_interval = 0.1
    try:
        r = await client.post("/api/instances", headers=user_h, json={
            "name": "scan-spot", "strategy_id": "market_scanner", "paper": True, "paper_balance": 1000,
            "params": {"scan_seconds": 10, "slots": 1, "position_pct": 40, "min_quote_volume": 500}})
        assert body(r)["code"] == 0, r.text
        iid = body(r)["data"]["id"]
        assert body(r)["data"]["symbol"] == "ALL/USDT"
        assert body(await client.post(f"/api/instances/{iid}/start", headers=user_h))["code"] == 0
        pos = []
        for _ in range(100):
            pos = body(await client.get(f"/api/instances/{iid}/positions", headers=user_h))["data"]["positions"]
            if pos:
                break
            await asyncio.sleep(0.05)
        assert pos and pos[0]["symbol"] == "PUMP/USDT", pos
        logs = body(await client.get(f"/api/instances/{iid}/logs", headers=user_h))["data"]["items"]
        assert any("粗筛：" in x["message"] and "命中 PUMP/USDT" in x["message"] for x in logs), [x["message"] for x in logs]
        # 跌 5% → 止损卖出
        conftest.PRICES["PUMP/USDT"] = 0.95
        for _ in range(100):
            pos = body(await client.get(f"/api/instances/{iid}/positions", headers=user_h))["data"]["positions"]
            if not pos:
                break
            await asyncio.sleep(0.05)
        assert not pos
        orders = body(await client.get(f"/api/instances/{iid}/orders", headers=user_h))["data"]["items"]
        assert {o["side"] for o in orders if o["status"] == "filled"} == {"buy", "sell"}
        st = body(await client.get(f"/api/instances/{iid}/status", headers=user_h))["data"]
        assert st["strategy_status"] and "粗筛" in st["strategy_status"]["summary"]
        await client.post(f"/api/instances/{iid}/stop", headers=user_h, json={"action": "keep"})
    finally:
        conftest.WINDOW["PUMP/USDT"] = {"change_4h": 0.0, "high_4h": 1.0, "quote_volume": 1e8}
        conftest.PRICES["PUMP/USDT"] = 1.0
        market.scan_interval = 5.0


async def test_scanner_futures_short_shows_liq(client, user_h):
    from bamy.engine.market import market

    conftest.WINDOW["PUMP/USDT:USDT"] = {"change_4h": -30.0, "high_4h": 1.5, "low_4h": 0.99, "quote_volume": 2e7}
    market.scan_interval = 0.1
    try:
        r = await client.post("/api/instances", headers=user_h, json={
            "name": "scan-fut", "strategy_id": "market_scanner", "market_type": "futures", "paper": True,
            "paper_balance": 1000, "paper_config": {"leverage": 2},
            "params": {"direction": "short", "scan_seconds": 10, "position_pct": 20, "leverage": 2}})
        iid = body(r)["data"]["id"]
        assert body(await client.post(f"/api/instances/{iid}/start", headers=user_h))["code"] == 0, "start"
        pos = []
        for _ in range(100):
            pa = body(await client.get(f"/api/instances/{iid}/paper-account", headers=user_h))["data"]
            pos = pa["positions"]
            if pos:
                break
            await asyncio.sleep(0.05)
        assert pos and pos[0]["side"] == "short" and pos[0]["leverage"] == 2, pos
        assert pos[0]["liquidation_price"] > pos[0]["entry_price"]
        # 手动平仓：走策略的平仓（买入平空）
        r = await client.post(f"/api/instances/{iid}/positions/PUMP%2FUSDT%3AUSDT/close", headers=user_h, json={})
        assert body(r)["code"] == 0, r.text
        pa = body(await client.get(f"/api/instances/{iid}/paper-account", headers=user_h))["data"]
        assert not pa["positions"]
        orders = body(await client.get(f"/api/instances/{iid}/orders", headers=user_h))["data"]["items"]
        assert [o["side"] for o in orders if o["status"] == "filled"] == ["buy", "sell"]   # 新的在前
        await client.post(f"/api/instances/{iid}/stop", headers=user_h, json={"action": "keep"})
    finally:
        conftest.WINDOW["PUMP/USDT:USDT"] = {"change_4h": 0.0, "high_4h": 1.0, "quote_volume": 1e8}
        market.scan_interval = 5.0
