import json
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_paper.contracts import (
    PaperExecutionAuthority,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperOrderType,
    PaperPortfolio,
    PaperPosition,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_valid_construction_of_all_contracts():
    order = PaperOrder(commander_decision_id="decision-1", symbol="BTC/USD", requested_quantity="0.5", requested_price="100")
    fill = PaperFill(paper_order_id=order.paper_order_id, symbol="BTC/USD", quantity="0.5", fill_price="100")
    position = PaperPosition(symbol="BTC/USD", quantity="0.5", average_entry_price="100", mark_price="101")
    portfolio = PaperPortfolio(paper_portfolio_id="portfolio-1", initial_cash="1000", available_cash="1000", equity="1000", positions={"BTC/USD": position})
    execution = PaperExecutionDecision(commander_decision_id="decision-1", decision=PaperExecutionDecisionType.CREATE_ORDER)
    risk = PaperRiskDecision(commander_decision_id="decision-1", decision=PaperRiskDecisionType.ALLOW)

    assert all(item.to_dict()["paper_only"] is True for item in (order, fill, position, portfolio, execution, risk))
    assert execution.execution_authority is PaperExecutionAuthority.NONE


@pytest.mark.parametrize("field,value", [
    ("side", "BUY"),
    ("order_type", "MARKET"),
    ("status", "CREATED"),
])
def test_invalid_order_enum_rejected(field, value):
    kwargs = {field: value, "commander_decision_id": "decision-1", "symbol": "BTC/USD", "requested_quantity": 1}
    with pytest.raises(ValueError):
        PaperOrder(**kwargs)


@pytest.mark.parametrize("factory", [
    lambda: PaperOrder(commander_decision_id="d", symbol="BTC/USD", requested_quantity=1, paper_only=False),
    lambda: PaperFill(paper_order_id="o", symbol="BTC/USD", quantity=1, fill_price=1, paper_only=False),
    lambda: PaperPosition(symbol="BTC/USD", quantity=1, average_entry_price=1, paper_only=False),
    lambda: PaperPortfolio(paper_portfolio_id="p", paper_only=False),
    lambda: PaperExecutionDecision(commander_decision_id="d", paper_only=False),
    lambda: PaperRiskDecision(commander_decision_id="d", paper_only=False),
])
def test_paper_only_cannot_be_false(factory):
    with pytest.raises(ValueError, match="paper_only"):
        factory()


def test_execution_authority_is_none_only():
    with pytest.raises(ValueError):
        PaperOrder(commander_decision_id="d", symbol="BTC/USD", requested_quantity=1, execution_authority="LIVE")


def test_required_ids_symbols_quantities_and_prices_are_validated():
    with pytest.raises(ValueError):
        PaperOrder(commander_decision_id="", symbol="BTC/USD", requested_quantity=1)
    with pytest.raises(ValueError):
        PaperOrder(commander_decision_id="d", symbol="", requested_quantity=1)
    with pytest.raises(ValueError):
        PaperOrder(commander_decision_id="d", symbol="BTC/USD", requested_quantity=0)
    with pytest.raises(ValueError):
        PaperFill(paper_order_id="o", symbol="BTC/USD", quantity=1, fill_price=0)
    with pytest.raises(ValueError):
        PaperPosition(symbol="BTC/USD", quantity=-1, average_entry_price=1)


def test_timestamp_must_be_utc_aware():
    with pytest.raises(ValueError):
        PaperOrder(commander_decision_id="d", symbol="BTC/USD", requested_quantity=1, timestamp_utc=datetime.now())


def test_serialization_roundtrip_is_deterministic():
    order = PaperOrder(
        paper_order_id="order-1",
        commander_decision_id="decision-1",
        timestamp_utc=TS,
        symbol="BTC/USD",
        side=PaperSide.SELL,
        order_type=PaperOrderType.LIMIT,
        requested_quantity=Decimal("0.125"),
        requested_price=Decimal("123.4500"),
        status=PaperOrderStatus.PENDING,
    )
    first = json.dumps(order.to_dict(), sort_keys=True)
    second = json.dumps(order.to_dict(), sort_keys=True)
    assert first == second
    assert json.loads(first)["requested_price"] == "123.4500"


def test_contracts_expose_no_live_authority_or_execution_imports():
    import genesis_paper.contracts as contracts

    names = set(vars(contracts))
    forbidden = {"exchange", "submit_order", "cancel_order", "open_position", "close_position", "leverage"}
    assert not any(name in names for name in forbidden)
