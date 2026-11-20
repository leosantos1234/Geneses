import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperExecutionAuthority,
    PaperOrder,
    PaperOrderStatus,
    PaperOrderType,
    PaperSide,
    PaperSimulatorConfig,
    cancel_order,
    expire_order,
    simulate_order,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def config(**changes):
    values = {
        "fee_bps": "10",
        "slippage_bps": "25",
        "max_position_notional": "100000",
        "max_portfolio_exposure": "200000",
        "allow_short": False,
        "allow_fractional_quantity": True,
    }
    values.update(changes)
    return PaperSimulatorConfig(**values)


def order(side=PaperSide.BUY, order_type=PaperOrderType.MARKET, **kwargs):
    return PaperOrder(
        paper_order_id=kwargs.pop("paper_order_id", "order-1"),
        commander_decision_id="decision-1",
        symbol="BTC/USD",
        side=side,
        order_type=order_type,
        requested_quantity=kwargs.pop("requested_quantity", "2"),
        requested_price=kwargs.pop("requested_price", None),
        **kwargs,
    )


def test_market_buy_fill_slippage_fee_and_linkage():
    result = simulate_order(order(), config(), timestamp_utc=TS, reference_price="100")
    assert result.updated_order.status is PaperOrderStatus.FILLED
    assert result.fill.paper_order_id == result.updated_order.paper_order_id
    assert result.fill.fill_price == Decimal("100.25000000")
    assert result.fill.fee == Decimal("0.20050000")
    assert result.fill.slippage_cost == Decimal("0.50000000")
    assert result.fill.paper_only is True
    assert result.updated_order.execution_authority is PaperExecutionAuthority.NONE


def test_market_sell_fill_slippage_formula():
    result = simulate_order(order(PaperSide.SELL), config(), timestamp_utc=TS, reference_price="100")
    assert result.fill.fill_price == Decimal("99.75000000")
    assert result.fill.slippage_cost == Decimal("0.50000000")


def test_limit_buy_and_sell_cross_only_at_limit():
    buy = order(order_type=PaperOrderType.LIMIT, requested_price="98")
    assert simulate_order(buy, config(), timestamp_utc=TS, reference_price="100", current_price="99").updated_order.status is PaperOrderStatus.PENDING
    buy_result = simulate_order(buy, config(), timestamp_utc=TS, reference_price="100", current_price="98")
    assert buy_result.fill.fill_price == Decimal("98.00000000")
    assert buy_result.fill.slippage_cost == Decimal("0.00000000")

    sell = order(PaperSide.SELL, PaperOrderType.LIMIT, requested_price="102")
    assert simulate_order(sell, config(), timestamp_utc=TS, reference_price="100", current_price="101").updated_order.status is PaperOrderStatus.PENDING
    assert simulate_order(sell, config(), timestamp_utc=TS, reference_price="100", current_price="102").fill.fill_price == Decimal("102.00000000")


def test_no_future_data_or_missing_limit_price_is_used():
    pending = simulate_order(order(order_type=PaperOrderType.LIMIT, requested_price="98"), config(), timestamp_utc=TS, reference_price="100", current_price="99")
    assert pending.reason == "LIMIT_NOT_CROSSED"
    rejected = simulate_order(order(order_type=PaperOrderType.LIMIT, requested_price="98"), config(), timestamp_utc=TS, reference_price="100")
    assert rejected.updated_order.status is PaperOrderStatus.REJECTED
    assert rejected.reason == "LIMIT_CURRENT_PRICE_REQUIRED"


@pytest.mark.parametrize("quantity", ["0", "-1"])
def test_invalid_quantity_rejected_by_contract(quantity):
    with pytest.raises(ValueError):
        order(requested_quantity=quantity)


def test_invalid_reference_price_rejected():
    with pytest.raises(ValueError):
        simulate_order(order(), config(), timestamp_utc=TS, reference_price="0")


def test_short_policy_fractional_policy_and_limits():
    short = simulate_order(order(PaperSide.SELL), config(), timestamp_utc=TS, reference_price="100", opening_short=True)
    assert short.reason == "SHORT_NOT_ALLOWED"
    assert simulate_order(order(PaperSide.SELL), config(allow_short=True), timestamp_utc=TS, reference_price="100", opening_short=True).fill is not None
    fractional = simulate_order(order(requested_quantity="1.5"), config(allow_fractional_quantity=False), timestamp_utc=TS, reference_price="100")
    assert fractional.reason == "FRACTIONAL_QUANTITY_NOT_ALLOWED"
    assert simulate_order(order(requested_quantity="1.5"), config(), timestamp_utc=TS, reference_price="100").fill is not None
    assert simulate_order(order(requested_quantity="11"), config(max_position_notional="1000"), timestamp_utc=TS, reference_price="100").reason == "MAX_POSITION_NOTIONAL_EXCEEDED"
    assert simulate_order(order(), config(max_portfolio_exposure="100"), timestamp_utc=TS, reference_price="100", current_portfolio_exposure="1").reason == "MAX_PORTFOLIO_EXPOSURE_EXCEEDED"


def test_cancel_and_expire_pending_only():
    pending = simulate_order(order(order_type=PaperOrderType.LIMIT, requested_price="98"), config(), timestamp_utc=TS, reference_price="100", current_price="99").updated_order
    assert cancel_order(pending).updated_order.status is PaperOrderStatus.CANCELLED
    assert expire_order(pending).updated_order.status is PaperOrderStatus.EXPIRED
    filled = simulate_order(order(), config(), timestamp_utc=TS, reference_price="100").updated_order
    with pytest.raises(ValueError):
        cancel_order(filled)
    with pytest.raises(ValueError):
        expire_order(filled)


def test_terminal_and_invalid_transitions_rejected():
    pending = simulate_order(order(order_type=PaperOrderType.LIMIT, requested_price="98"), config(), timestamp_utc=TS, reference_price="100", current_price="99").updated_order
    with pytest.raises(ValueError):
        simulate_order(cancel_order(pending).updated_order, config(), timestamp_utc=TS, reference_price="100")


def test_repeatability_and_deterministic_serialization():
    first = simulate_order(order(), config(), timestamp_utc=TS, reference_price="100")
    second = simulate_order(order(), config(), timestamp_utc=TS, reference_price="100")
    assert first.to_dict() == second.to_dict()
    assert config().to_dict() == config().to_dict()

