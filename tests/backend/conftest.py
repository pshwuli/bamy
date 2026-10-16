"""后端集成测试夹具：独立 SQLite、离线行情、进程内 ASGI。

设置必须在导入 bamy 之前完成（bamy.core.config 在导入时读取环境变量）。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="bamy-test-"))
os.environ.update({
    "ENV_FILE": str(TMP / "none.env"),
    "APP_ENV": "dev",
    "SECRET_KEY": "test-secret-key-0123456789abcdef0123456789abcdef",
    "ENCRYPT_KEY": "test-encrypt-key-fedcba9876543210fedcba9876543210",
    "DATABASE_URL": f"sqlite+aiosqlite:///{TMP / 'test.db'}",
    "LOG_DIR": str(TMP / "logs"),
    "ENABLE_LIVE_TRADING": "false",
    "AUTO_RECOVER_ON_BOOT": "true",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

PRICES = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0, "BTC/USDT:USDT": 50000.0, "ETH/USDT:USDT": 3000.0,
          "PUMP/USDT": 1.0, "PUMP/USDT:USDT": 1.0}
# 扫描测试用：近 4 小时涨幅、窗口最高/最低价、24h 成交额（测试里可直接改）
WINDOW = {s: {"change_4h": 0.0, "high_4h": p, "quote_volume": 1e8} for s, p in PRICES.items()}
MARKETS = {s: {"symbol": s, "spot": ":" not in s, "swap": ":" in s, "active": True,
               "precision": {"amount": 6, "price": 2}, "limits": {"amount": {"min": 0.0}, "cost": {"min": 0.0}}}
           for s in PRICES}


async def offline_source(symbol: str) -> dict:
    import time

    w = WINDOW.get(symbol, {})
    return {"symbol": symbol, "price": PRICES[symbol], "bid": PRICES[symbol], "ask": PRICES[symbol],
            "volume": 1.0, "ts": int(time.time() * 1000), **w,
            "high": w.get("high_4h", PRICES[symbol]), "low": w.get("low_4h", PRICES[symbol])}


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
async def app():
    from bamy.core.ratelimit import limiter
    from bamy.engine.market import market

    market.set_source(offline_source, MARKETS)
    market.poll_interval = 0.05
    limiter.enabled = False
    from bamy.main import app as fastapi_app, shutdown, startup

    await startup()
    yield fastapi_app
    await shutdown()


@pytest.fixture(scope="session")
async def client(app):
    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client, username: str, password: str) -> dict:
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    tok = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="session")
async def admin_h(client):
    r = await client.post("/api/setup", json={"username": "root", "password": "Admin12345"})
    assert r.status_code in (200, 201), r.text
    return await login(client, "root", "Admin12345")


@pytest.fixture(scope="session")
async def user_h(client, admin_h):
    r = await client.post("/api/auth/register", json={"username": "alice", "password": "Alice12345"})
    assert r.status_code in (200, 201), r.text
    return await login(client, "alice", "Alice12345")


@pytest.fixture(scope="session")
async def other_h(client, admin_h):
    r = await client.post("/api/auth/register", json={"username": "bob", "password": "Bob1234567"})
    assert r.status_code in (200, 201), r.text
    return await login(client, "bob", "Bob1234567")
