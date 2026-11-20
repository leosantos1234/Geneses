import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperSide,
    ROUNDING_POLICY,
    calculate_equity,
    calculate_exposure,
    close_position,
    create_portfolio,
    mark_position,
    open_position,
    position_unrealized_pnl,
    reduce_position,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_portfolio_initialization_and_cash_invariants():
    portfolio = create_portfolio("1000", paper_portfolio_id="p1")
    assert portfolio.initial_cash == Decimal("1000.00000000")
    assert portfolio.available_cash == portfolio.equity == Decimal("1000.00000000")
    assert portfolio.paper_only is True


@pytest.mark.parametrize("value", ["0", "-1"])
def test_invalid_initial_cash(value):
    with pytest.raises(ValueError):
        create_portfolio(value)


def test_open_increase_long_and_weighted_average_entry():
    portfolio = create_portfolio("1000")
    portfolio = open_position(portfolio, "BTC/USD", PaperSide.BUY, "2", "100", timestamp=TS)
    portfolio = open_position(portfolio, "BTC/USD", PaperSide.BUY, "1", "130", timestamp=TS)
    position = portfolio.positions["BTC/USD"]
    assert position.quantity == Decimal("3.00000000")
    assert position.average_entry_price == Decimal("110.00000000")
    assert portfolio.available_cash == Decimal("670.00000000")


def test_mark_price_above_and_below_entry():
    portfolio = open_position(create_portfolio("1000"), "BTC/USD", PaperSide.BUY, "2", "100", timestamp=TS)
    assert position_unrealized_pnl(portfolio.positions["BTC/USD"], "120") == Decimal("40.00000000")
    portfolio = mark_position(portfolio, "BTC/USD", "80")
    assert portfolio.unrealized_pnl == Decimal("-40.00000000")
    assert portfolio.equity == Decimal("960.00000000")


def test_partial_close_realizes_pnl_and_full_close():
    portfolio = open_position(create_portfolio("1000"), "BTC/USD", PaperSide.BUY, "2", "100", timestamp=TS)
    partial = reduce_position(portfolio, "BTC/USD", "1", "120", timestamp=TS)
    assert partial.realized_pnl == Decimal("20.00000000")
    assert partial.positions["BTC/USD"].quantity == Decimal("1.00000000")
    closed = close_position(partial, "BTC/USD", "80", timestamp=TS)
    assert closed.positions == {}
    assert closed.realized_pnl == Decimal("0.00000000")
    assert closed.available_cash == Decimal("1000.00000000")


def test_short_accounting_and_exposure():
    portfolio = open_position(create_portfolio("1000"), "ETH/USD", PaperSide.SELL, "2", "100", timestamp=TS)
    assert portfolio.available_cash == Decimal("1200.00000000")
    portfolio = mark_position(portfolio, "ETH/USD", "80")
    assert portfolio.positions["ETH/USD"].unrealized_pnl == Decimal("40.00000000")
    assert portfolio.equity == Decimal("1040.00000000")
    gross, net = calculate_exposure(portfolio.positions)
    assert gross == Decimal("160.00000000")
    assert net == Decimal("-160.00000000")


def test_multiple_positions_equity_and_repeatability():
    def build():
        portfolio = create_portfolio("10000", paper_portfolio_id="fixed")
        portfolio = open_position(portfolio, "BTC/USD", PaperSide.BUY, "1", "100", timestamp=TS)
        return mark_position(portfolio, "BTC/USD", "125")

    first, second = build(), build()
    assert first.to_dict() == second.to_dict()
    assert calculate_equity(first) == Decimal("10025.00000000")
    assert calculate_exposure(first.positions) == (Decimal("125.00000000"), Decimal("125.00000000"))
    assert ROUNDING_POLICY == "Decimal quantized to 8 places using ROUND_HALF_EVEN"


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_reject_zero_or_negative_quantity(quantity):
    with pytest.raises(ValueError):
        open_position(create_portfolio("100"), "BTC/USD", PaperSide.BUY, quantity, "10")


@pytest.mark.parametrize("price", ["0", "-1"])
def test_reject_zero_or_negative_price(price):
    with pytest.raises(ValueError):
        open_position(create_portfolio("100"), "BTC/USD", PaperSide.BUY, "1", price)


def test_reject_negative_cash_and_decimal_determinism():
    portfolio = create_portfolio("100")
    with pytest.raises(ValueError):
        from dataclasses import replace
        from genesis_paper.accounting import validate_cash
        validate_cash(replace(portfolio, available_cash=Decimal("-0.01")))
    one = open_position(create_portfolio("1000"), "BTC/USD", PaperSide.BUY, "0.333333333", "10.123456789", timestamp=TS)
    two = open_position(create_portfolio("1000"), "BTC/USD", PaperSide.BUY, "0.333333333", "10.123456789", timestamp=TS)
    assert one.to_dict() == two.to_dict()
