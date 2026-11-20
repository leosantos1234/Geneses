from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from genesis_commander.contract import DecisionType
from genesis_paper.candidate_pipeline import route_candidate_to_paper
from genesis_paper.contracts import PaperRiskDecisionType
from genesis_paper.risk import PaperRiskConfig
from genesis_paper.simulator import PaperSimulatorConfig


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def config():
    return PaperRiskConfig(10000, 20000, 10000, True, True)


def simulator():
    return PaperSimulatorConfig(0, 0, 10000, 20000, True, True)


def decision(kind, *, strategy_id="strategy-1", strategy_name="BREAKOUT"):
    return SimpleNamespace(
        commander_decision_id=f"decision-{kind}", timestamp_utc=TS, symbol="BTC/USD",
        decision=kind, strategy_id=strategy_id, strategy_name=strategy_name,
    )


def route(kind, **kwargs):
    return route_candidate_to_paper(
        decision(kind), strategy_id=kwargs.pop("strategy_id", "strategy-1"),
        strategy_name=kwargs.pop("strategy_name", "BREAKOUT"), quantity=1,
        reference_price=100, current_price=100, current_symbol_exposure=0,
        current_portfolio_exposure=0, available_cash=1000, paper_risk_config=config(),
        simulator_config=simulator(), paper_enabled=True,
        record_provenance=kwargs.pop("record_provenance", "ACCEPTANCE"), **kwargs,
    )


def test_candidate_long_routes_to_risk_and_fills():
    result = route(DecisionType.CANDIDATE_LONG)
    assert result is not None
    assert result.risk.decision is PaperRiskDecisionType.ALLOW
    assert result.order is not None
    assert result.fill is not None
    assert result.effect is not None
    assert result.order.commander_decision_id == result.commander_decision_id
    assert result.fill.paper_order_id == result.order.paper_order_id
    assert result.effect.commander_decision_id == result.commander_decision_id


def test_candidate_short_routes_to_risk():
    result = route(DecisionType.CANDIDATE_SHORT)
    assert result is not None
    assert result.risk.decision is PaperRiskDecisionType.ALLOW
    assert result.order is not None


def test_non_candidates_do_not_route_to_paper():
    for kind in (DecisionType.NO_ACTION, DecisionType.WATCH, DecisionType.INSUFFICIENT_DATA):
        assert route(kind) is None


def test_missing_strategy_identity_is_not_inferred():
    result = route(DecisionType.CANDIDATE_LONG, strategy_id=None, strategy_name=None)
    assert result is not None
    assert result.risk.decision is PaperRiskDecisionType.INSUFFICIENT_DATA
    assert result.order is None


def test_rejected_candidate_creates_no_order():
    result = route_candidate_to_paper(
        decision(DecisionType.CANDIDATE_LONG), strategy_id="s", strategy_name="BREAKOUT",
        quantity=1000, reference_price=100, current_price=100,
        current_symbol_exposure=0, current_portfolio_exposure=0, available_cash=1000,
        paper_risk_config=config(), simulator_config=simulator(), paper_enabled=True,
        record_provenance="TEST",
    )
    assert result.risk.decision is PaperRiskDecisionType.REJECT
    assert result.order is None


def test_provenance_is_preserved_across_paper_artifacts():
    result = route(DecisionType.CANDIDATE_LONG, record_provenance="ACCEPTANCE")
    assert result.record_provenance == "ACCEPTANCE"
    assert result.order.record_provenance == "ACCEPTANCE"
    assert result.fill.record_provenance == "ACCEPTANCE"
    assert result.effect.record_provenance == "ACCEPTANCE"
