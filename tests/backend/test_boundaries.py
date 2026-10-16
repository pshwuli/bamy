"""贯穿全局的原则与边界（《DESIGN》第四、六节）。"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_plugin_has_no_order_api():
    """原则 2：框架不向插件提供任何下单接口。"""
    from bamy.plugin import PluginContext

    names = {n for n, _ in inspect.getmembers(PluginContext)}
    forbidden = {"buy", "sell", "place_order", "place_protection", "close_position", "close_positions",
                 "close_all_positions", "cancel", "cancel_all", "set_leverage"}
    assert not names & forbidden


def test_framework_has_no_business_words():
    """原则 1：框架代码里不出现业务词（杠杆/网格/止损由策略声明）。"""
    hits = []
    for f in (ROOT / "backend/bamy").rglob("*.py"):
        if f.name in {"strategy.py"}:  # 策略基类允许出现能力名
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            code = re.sub(r"(\"\"\"|''').*", "", code)
            if re.search(r"网格|grid_|止损", code):
                hits.append(f"{f.relative_to(ROOT)}:{i}")
    assert not hits, hits


def test_routes_user_admin_separated():
    """边界 5：用户接口与管理员接口物理分离——/api/admin 与 /api/super 路由全部挂 admin/super_admin 依赖。"""
    from bamy.main import app

    bad = []
    for r in app.routes:
        path = getattr(r, "path", "")
        if not path.startswith(("/api/admin", "/api/super")):
            continue
        deps = {getattr(d.call, "__name__", "") for d in r.dependant.dependencies}
        need = "super_admin" if path.startswith("/api/super") else "admin"
        if need not in deps and not (need == "admin" and "super_admin" in deps):
            bad.append(path)
    assert not bad, bad


async def test_crash_recovery(client, user_h):
    """原则 4：进程重启后，enabled=true 的实例自动恢复运行。"""
    from bamy.engine.runtime import engine

    r = await client.post("/api/instances", headers=user_h, json={
        "name": "recover-me", "strategy_id": "dca_strategy", "symbol": "ETH/USDT", "paper": True,
        "paper_balance": 1000, "params": {"quote_amount": 10, "interval": 3600}})
    iid = r.json()["data"]["id"]
    await client.post(f"/api/instances/{iid}/start", headers=user_h)
    for _ in range(50):
        if iid in engine.running:
            break
        await asyncio.sleep(0.1)
    assert iid in engine.running
    # 模拟崩溃：内存清空但数据库仍是 running / enabled
    run = engine.running.pop(iid)
    from bamy.engine.market import market

    market.unsubscribe(run.symbol, f"inst:{iid}")
    summary = await engine.recover()
    assert iid in summary["recovered"], summary
    d = (await client.get(f"/api/instances/{iid}", headers=user_h)).json()["data"]
    assert d["status"] == "running"
    await client.post(f"/api/instances/{iid}/stop", headers=user_h)


async def test_audit_source_distinguished(client, user_h):
    """边界 7：审计区分策略触发与人工触发。"""
    items = (await client.get("/api/user/audit-logs", headers=user_h, params={"page_size": 200})).json()["data"]["items"]
    sources = {a.get("source") for a in items}
    assert "manual" in sources
