from types import SimpleNamespace

import pytest

from bamy.engine.adapters import BinanceFuturesAdapter, ExchangeError, _parse_order


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    async def create_order(self, *args):
        self.calls.append(args)
        return {"id": "ex-1", "status": "closed", "filled": 1, "average": 100}


@pytest.mark.asyncio
async def test_hedge_order_sends_target_position_side() -> None:
    adapter = BinanceFuturesAdapter.__new__(BinanceFuturesAdapter)
    adapter.client = FakeClient()

    result = await adapter.create_order(
        "BTC/USDT:USDT", "sell", 1, None,
        client_order_id="cid-1", position_mode="hedge", position_side="long",
        reduce_only=True,
    )

    params = adapter.client.calls[0][-1]
    assert params["positionSide"] == "LONG"
    assert "reduceOnly" not in params
    assert result.status == "filled"


@pytest.mark.asyncio
async def test_oneway_order_rejects_position_side() -> None:
    adapter = BinanceFuturesAdapter.__new__(BinanceFuturesAdapter)
    adapter.client = FakeClient()

    with pytest.raises(ExchangeError) as exc:
        await adapter.create_order(
            "BTC/USDT:USDT", "buy", 1, None,
            position_mode="oneway", position_side="long",
        )

    assert exc.value.code == 42224
    assert not adapter.client.calls


@pytest.mark.asyncio
async def test_unknown_position_mode_is_rejected_before_network_call() -> None:
    adapter = BinanceFuturesAdapter.__new__(BinanceFuturesAdapter)
    adapter.client = FakeClient()

    with pytest.raises(ExchangeError) as exc:
        await adapter.create_order("BTC/USDT:USDT", "buy", 1, None, position_mode=None)

    assert exc.value.code == 42224
    assert not adapter.client.calls


def test_unknown_exchange_order_status_is_not_treated_as_success() -> None:
    result = _parse_order({"id": "ex-2", "status": "mystery", "filled": 0}, "cid-2")

    assert result.status == "unknown"
    assert result.filled_amount == 0
