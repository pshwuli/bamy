from decimal import Decimal

import pytest

from bamy.engine.adapters import ExchangeError
from bamy.engine.paper import FuturesBook, PaperConfig, load_futures_positions


@pytest.fixture
def book() -> FuturesBook:
    return FuturesBook(Decimal("10000"), {}, Decimal("0"), PaperConfig(leverage=5, slippage=0))


def test_hedge_legs_are_independent_and_restore(book: FuturesBook) -> None:
    symbol = "BTC/USDT:USDT"
    book.fill("open-long", symbol, "buy", 1, 100, apply_slippage=False, position_side="long")
    book.fill("open-short", symbol, "sell", 2, 100, apply_slippage=False, position_side="short")

    assert {p["position_side"] for p in book.positions_list({symbol: 100})} == {"long", "short"}
    book.fill("close-long", symbol, "sell", 1, 110, apply_slippage=False,
              position_side="long", reduce_only=True)

    positions = book.positions_list({symbol: 110})
    assert len(positions) == 1
    assert positions[0]["position_side"] == "short"
    assert positions[0]["amount"] == 2
    restored = load_futures_positions(book.dump_positions())
    assert set(restored) == {"BTC/USDT:USDT|short"}


def test_reduce_only_rejects_excess_amount(book: FuturesBook) -> None:
    symbol = "BTC/USDT:USDT"
    book.fill("open-long", symbol, "buy", 1, 100, apply_slippage=False, position_side="long")

    with pytest.raises(ExchangeError) as exc:
        book.fill("over-close", symbol, "sell", 1.1, 100, apply_slippage=False,
                  position_side="long", reduce_only=True)

    assert exc.value.code == 42244
    assert book.positions["BTC/USDT:USDT|long"]["amount"] == Decimal("1")


def test_explicit_close_does_not_reverse_position(book: FuturesBook) -> None:
    symbol = "BTC/USDT:USDT"
    book.fill("open-long", symbol, "buy", 1, 100, apply_slippage=False, position_side="long")
    result = book.fill("close-long", symbol, "sell", 1, 110, apply_slippage=False, position_side="long")

    assert result.filled_amount == 1
    assert book.positions == {}


def test_legacy_position_without_side_defaults_to_long() -> None:
    raw = '{"BTC/USDT:USDT":{"symbol":"BTC/USDT:USDT","amount":"1","entry":"100","margin":"20"}}'

    positions = load_futures_positions(raw)

    assert positions["BTC/USDT:USDT|long"]["side"] == "long"
