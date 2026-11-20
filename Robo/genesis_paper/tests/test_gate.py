import sys
from dataclasses import replace
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "Robo")

from genesis_paper import (  # noqa: E402
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperExecutionGateConfig,
    PaperGateDecisionType,
    PaperOrder,
    PaperOrderType,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperSimulatorConfig,
    execute_paper_gate,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def sim_config():
    return PaperSimulatorConfig(
        fee_bps="10",
        slippage_bps="25",
        max_position_notional="10000",
        max_portfolio_exposure="20000",
        allow_short=False,
        allow_fractional_quantity=True,
    )


def context(execution=PaperExecutionDecisionType.CREATE_ORDER, risk=PaperRiskDecisionType.ALLOW, order=True):
    execution_decision = PaperExecutionDecision(commander_decision_id="decision-1", decision=execution)
    risk_decision = PaperRiskDecision(commander_decision_id="decision-1", decision=risk)
    candidate = None if not order else PaperOrder(
        paper_order_id="order-1", commander_decision_id="decision-1", symbol="BTC/USD",
        side=PaperSide.BUY, order_type=PaperOrderType.MARKET, requested_quantity="2",
    )
    return execution_decision, risk_decision, candidate


def gate(**changes):
    execution, risk, order = context()
    values = {
        "commander_decision_id": "decision-1",
        "execution_decision": execution,
        "risk_decision": risk,
        "order": order,
        "gate_config": PaperExecutionGateConfig(paper_execution_enabled=True),
        "simulator_config": sim_config(),
        "timestamp_utc": TS,
        "reference_price": "100",
    }
    values.update(changes)
    return execute_paper_gate(**values)


def test_default_gate_disabled_and_no_fill():
    execution, risk, order = context()
    result = execute_paper_gate(
        commander_decision_id="decision-1", execution_decision=execution, risk_decision=risk, order=order,
        gate_config=PaperExecutionGateConfig(), simulator_config=sim_config(), timestamp_utc=TS, reference_price="100",
    )
    assert result.decision is PaperGateDecisionType.BLOCK_PAPER_EXECUTION
    assert result.reason == "PAPER_EXECUTION_DISABLED"
    assert result.fill is None


def test_explicit_enable_valid_create_allow_executes_simulator():
    result = gate()
    assert result.decision is PaperGateDecisionType.ALLOW_PAPER_EXECUTION
    assert result.fill is not None
    assert result.fill.paper_only is True


@pytest.mark.parametrize("risk,reason", [
    (PaperRiskDecisionType.REJECT, "RISK_NOT_ALLOWED"),
    (PaperRiskDecisionType.INSUFFICIENT_DATA, "RISK_NOT_ALLOWED"),
])
def test_risk_reject_or_insufficient_blocks(risk, reason):
    execution, risk_decision, order = context(risk=risk)
    result = gate(execution_decision=execution, risk_decision=risk_decision, order=order)
    assert result.reason == reason
    assert result.fill is None


@pytest.mark.parametrize("execution", [
    PaperExecutionDecisionType.NO_ACTION,
    PaperExecutionDecisionType.REJECTED,
    PaperExecutionDecisionType.INSUFFICIENT_DATA,
])
def test_non_create_execution_decisions_block(execution):
    execution_decision, risk, order = context(execution=execution)
    result = gate(execution_decision=execution_decision, risk_decision=risk, order=order)
    assert result.reason == "INVALID_EXECUTION_DECISION"
    assert result.fill is None


def test_missing_order_market_data_and_duplicate_block():
    execution, risk, order = context(order=False)
    assert gate(execution_decision=execution, risk_decision=risk, order=order).reason == "MISSING_ORDER"
    assert gate(reference_price=None).reason == "MISSING_MARKET_DATA"
    assert gate(duplicate_execution_exists=True).reason == "DUPLICATE_EXECUTION"


def test_limit_may_remain_pending_legitimately():
    execution, risk, _ = context()
    limit = PaperOrder(
        paper_order_id="limit-1", commander_decision_id="decision-1", symbol="BTC/USD",
        order_type=PaperOrderType.LIMIT, requested_price="98", requested_quantity="1",
    )
    result = gate(execution_decision=execution, risk_decision=risk, order=limit, current_price="99")
    assert result.updated_order.status.value == "PENDING"
    assert result.fill is None


def test_invalid_invariant_and_invalid_config_fail_closed():
    execution, risk, order = context()
    with pytest.raises(ValueError):
        replace(execution, paper_only=False)
    assert gate(simulator_config=object()).reason == "INVALID_SIMULATOR_CONFIG"


def test_commander_cannot_override_rejection_and_result_is_repeatable():
    execution, risk, order = context(risk=PaperRiskDecisionType.REJECT)
    first = gate(execution_decision=execution, risk_decision=risk, order=order)
    second = gate(execution_decision=execution, risk_decision=risk, order=order)
    assert first.to_dict() == second.to_dict()
    assert first.fill is None
