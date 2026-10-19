import asyncio
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from bamy.db.models import Instance, Order, Plugin, PluginBinding, PluginHealth, User
from bamy.db.session import init_db, session_scope
from bamy.engine.adapters import ExchangeOrder
from bamy.engine.pluginhub import plugin_hub
from bamy.engine.runtime import engine as runtime_engine


@pytest_asyncio.fixture
async def db():
    await init_db()
    yield


async def _seed_binding() -> PluginBinding:
    async with session_scope() as s:
        user = User(username=f"u{id(s)}", password_hash="x")
        plugin = Plugin(id=f"p{id(s) % 10**6}", name="p", version="1")
        s.add_all([user, plugin])
        await s.flush()
        b = PluginBinding(plugin_id=plugin.id, plugin_version="1", scope="global",
                          owner_id=user.id)
        s.add(b)
        await s.commit()
        await s.refresh(b)
        return b


@pytest.mark.asyncio
async def test_concurrent_health_records_keep_every_count(db) -> None:
    b = await _seed_binding()
    await asyncio.gather(*[plugin_hub._record(b, cost_ms=10) for _ in range(10)])
    await asyncio.gather(*[plugin_hub._record(b, error="boom", kind="event") for _ in range(3)])

    async with session_scope() as s:
        rows = (await s.execute(select(PluginHealth).where(PluginHealth.plugin_id == b.plugin_id))).scalars().all()
    assert len(rows) == 2                                  # 绑定级 + 全局级各一条
    for row in rows:
        assert row.invoke_count == 13
        assert row.checkpoint_calls == 10
        assert row.event_calls == 3
        assert row.error_count == 3


async def _seed_order(status: str, filled: str = "0") -> int:
    async with session_scope() as s:
        user = User(username=f"o{id(s)}", password_hash="x")
        s.add(user)
        await s.flush()
        inst = Instance(owner_id=user.id, name="i", strategy_id="s", strategy_version="1",
                        symbol="BTC/USDT", status="running", paper=True)
        s.add(inst)
        await s.flush()
        o = Order(client_order_id=f"c{id(s)}", instance_id=inst.id, owner_id=user.id, symbol="BTC/USDT",
                  side="buy", type="limit", amount=Decimal("2"), filled_amount=Decimal(filled), status=status)
        s.add(o)
        await s.commit()
        return o.id


async def _order(order_id: int) -> Order:
    async with session_scope() as s:
        return await s.get(Order, order_id)


@pytest.mark.asyncio
async def test_partial_then_cancel_keeps_fill_and_never_regresses(db) -> None:
    oid = await _seed_order("submitted")
    await runtime_engine._apply_result(oid, ExchangeOrder("x1", "partial", 1.0, 100.0))
    await runtime_engine._apply_result(oid, ExchangeOrder("x1", "canceled", 1.0, 100.0))
    await runtime_engine._apply_result(oid, ExchangeOrder("x1", "submitted", 0.0, None))   # 迟到的旧事件
    o = await _order(oid)
    assert o.status == "canceled"
    assert o.filled_amount == Decimal("1")


@pytest.mark.asyncio
async def test_late_full_fill_upgrades_canceled_and_caps_overfill(db) -> None:
    oid = await _seed_order("canceled", "1")
    await runtime_engine._apply_result(oid, ExchangeOrder("x1", "canceled", 5.0, 100.0))
    o = await _order(oid)
    assert o.filled_amount == Decimal("2")                 # 不超过订单量
    assert o.status == "filled"


@pytest.mark.asyncio
async def test_unknown_status_is_persisted_as_unknown(db) -> None:
    oid = await _seed_order("submitting")
    await runtime_engine._apply_result(oid, ExchangeOrder("x1", "weird", 0.0, None))
    assert (await _order(oid)).status == "unknown"
