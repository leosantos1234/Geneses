import sys
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperRiskConfig,
    PaperRiskDecisionType,
    PaperSide,
    evaluate_paper_risk,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def cfg(**changes):
    values = {
        "max_position_notional": "1000",
        "max_portfolio_exposure": "5000",
        "max_symbol_exposure": "2000",
        "allow_short": False,
        "allow_fractional_quantity": True,
    }
    values.update(changes)
    return PaperRiskConfig(**values)


def evaluate(**changes):
    values = {
        "commander_decision_id": "decision-1",
        "symbol": "BTC/USD",
        "side": PaperSide.BUY,
        "quantity": "2",
        "reference_price": "100",
        "current_symbol_exposure": "100",
        "current_portfolio_exposure": "500",
        "available_cash": "1000",
        "config": cfg(),
        "timestamp_utc": TS,
    }
    values.update(changes)
    return evaluate_paper_risk(**values)


def test_valid_long_allow_and_paper_invariants():
    decision = evaluate()
    assert decision.decision is PaperRiskDecisionType.ALLOW
    assert decision.reasons == []
    assert decision.risk_flags == []
    assert decision.paper_only is True


@pytest.mark.parametrize("field", ["quantity", "symbol", "reference_price"])
def test_invalid_inputs_reject(field):
    values = {"quantity": "0", "symbol": "", "reference_price": "0"}
    decision = evaluate(**{field: values[field]})
    assert decision.decision is PaperRiskDecisionType.REJECT


def test_exposure_and_cash_limits():
    assert "MAX_POSITION_NOTIONAL_EXCEEDED" in evaluate(quantity="11").reasons
    assert "MAX_PORTFOLIO_EXPOSURE_EXCEEDED" in evaluate(current_portfolio_exposure="4900").reasons
    assert "MAX_SYMBOL_EXPOSURE_EXCEEDED" in evaluate(current_symbol_exposure="1900").reasons
    assert evaluate(available_cash="100").reasons == ["INSUFFICIENT_CASH"]


def test_short_policy_and_explicit_collateral():
    assert evaluate(side=PaperSide.SELL, opening_short=True).reasons == ["SHORT_NOT_ALLOWED"]
    allowed = evaluate(side=PaperSide.SELL, opening_short=True, config=cfg(allow_short=True), short_collateral_available="500")
    assert allowed.decision is PaperRiskDecisionType.ALLOW
    missing = evaluate(side=PaperSide.SELL, opening_short=True, config=cfg(allow_short=True))
    assert missing.decision is PaperRiskDecisionType.INSUFFICIENT_DATA
    insufficient = evaluate(side=PaperSide.SELL, opening_short=True, config=cfg(allow_short=True), short_collateral_available="1")
    assert insufficient.reasons == ["INSUFFICIENT_SHORT_COLLATERAL"]


def test_fractional_duplicate_and_missing_data_policies():
    assert evaluate(quantity="1.5", config=cfg(allow_fractional_quantity=False)).reasons == ["FRACTIONAL_QUANTITY_NOT_ALLOWED"]
    assert evaluate(commander_execution_exists=True).reasons == ["DUPLICATE_COMMANDER_EXECUTION"]
    missing = evaluate(current_symbol_exposure=None)
    assert missing.decision is PaperRiskDecisionType.INSUFFICIENT_DATA
    assert missing.reasons == ["MISSING_REQUIRED_DATA"]


def test_multiple_violations_have_stable_order():
    decision = evaluate(quantity="20", current_portfolio_exposure="4900", current_symbol_exposure="1990", available_cash="1", commander_execution_exists=True)
    assert decision.decision is PaperRiskDecisionType.REJECT
    assert decision.reasons == [
        "DUPLICATE_COMMANDER_EXECUTION",
        "MAX_POSITION_NOTIONAL_EXCEEDED",
        "MAX_PORTFOLIO_EXPOSURE_EXCEEDED",
        "MAX_SYMBOL_EXPOSURE_EXCEEDED",
        "INSUFFICIENT_CASH",
    ]


def test_repeatability_and_immutability():
    first, second = evaluate(), evaluate()
    assert first.to_dict() == second.to_dict()
    with pytest.raises(FrozenInstanceError):
        first.decision = PaperRiskDecisionType.REJECT


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        cfg(max_position_notional="0")
    with pytest.raises(ValueError):
        cfg(allow_short="yes")
