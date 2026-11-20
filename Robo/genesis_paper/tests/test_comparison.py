from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from Robo.genesis_paper import (
    ComparisonOutcomeStatus,
    DataQualityStatus,
    DecisionQuality,
    PaperClosedTrade,
    PaperEffectType,
    PaperExecutionDecision,
    PaperExecutionDecisionType,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperOrderType,
    PaperGateDecisionType,
    PaperPerformanceMetrics,
    PaperPortfolioEffect,
    PaperRiskDecision,
    PaperRiskDecisionType,
    PaperSide,
    PaperEquityPoint,
    HistoricalMarketEvent,
    MetricStatus,
    calculate_paper_performance,
    compare_shadow_decision,
    validate_comparisons,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def commander(kind="CANDIDATE_LONG", decision_id="d1", symbol="BTC/USD"):
    return SimpleNamespace(commander_decision_id=decision_id, timestamp_utc=TS, symbol=symbol, decision=kind, confidence="HIGH")


def execution(decision_id="d1", kind=PaperExecutionDecisionType.CREATE_ORDER):
    return PaperExecutionDecision(commander_decision_id=decision_id, timestamp_utc=TS, decision=kind)


def risk(decision_id="d1", kind=PaperRiskDecisionType.ALLOW, reasons=None):
    return PaperRiskDecision(commander_decision_id=decision_id, timestamp_utc=TS, decision=kind, reasons=reasons or [])


def order(decision_id="d1", status=PaperOrderStatus.CREATED, side=PaperSide.BUY):
    return PaperOrder(paper_order_id=f"o-{decision_id}", commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD", side=side, order_type=PaperOrderType.MARKET, requested_quantity="2", status=status)


def fill(decision_id="d1", side=PaperSide.BUY):
    return PaperFill(paper_fill_id=f"f-{decision_id}", paper_order_id=f"o-{decision_id}", timestamp_utc=TS, symbol="BTC/USD", side=side, quantity="2", fill_price="101", fee="2", slippage_cost="1")


def effect(decision_id="d1"):
    return PaperPortfolioEffect(paper_effect_id=f"e-{decision_id}", paper_portfolio_id="p1", paper_fill_id=f"f-{decision_id}", paper_order_id=f"o-{decision_id}", commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD", effect_type=PaperEffectType.OPEN, quantity_delta="2", cash_delta="-202")


def performance(net="10", fees="2", slippage="1"):
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.BUY, TS, TS, 100, 106.5, 2, fees, slippage)
    return calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,))


def test_no_action_is_no_paper_action():
    result = compare_shadow_decision(commander("NO_ACTION"), comparison_id="c1")
    assert result.outcome_status is ComparisonOutcomeStatus.NO_PAPER_ACTION
    assert result.decision_quality is DecisionQuality.NOT_APPLICABLE


def test_watch_is_no_paper_action():
    result = compare_shadow_decision(commander("WATCH"), comparison_id="c1")
    assert result.outcome_status is ComparisonOutcomeStatus.NO_PAPER_ACTION


def test_insufficient_data_is_data_not_available():
    result = compare_shadow_decision(commander("INSUFFICIENT_DATA"), comparison_id="c1")
    assert result.outcome_status is ComparisonOutcomeStatus.DATA_NOT_AVAILABLE
    assert result.data_quality_status is DataQualityStatus.DATA_NOT_AVAILABLE


def test_candidate_long_risk_reject_preserves_reasons():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(kind=PaperRiskDecisionType.REJECT, reasons=["MAX_POSITION_NOTIONAL_EXCEEDED"]))
    assert result.outcome_status is ComparisonOutcomeStatus.RISK_REJECTED
    assert result.paper_risk_reasons == ("MAX_POSITION_NOTIONAL_EXCEEDED",)


def test_candidate_short_risk_reject():
    result = compare_shadow_decision(commander("CANDIDATE_SHORT"), comparison_id="c1", execution_decision=execution(), risk_decision=risk(kind=PaperRiskDecisionType.REJECT, reasons=["SHORT_NOT_ALLOWED"]))
    assert result.outcome_status is ComparisonOutcomeStatus.RISK_REJECTED


def test_candidate_gate_blocked_is_distinct():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), gate_result=SimpleNamespace(decision=PaperGateDecisionType.BLOCK_PAPER_EXECUTION))
    assert result.outcome_status is ComparisonOutcomeStatus.EXECUTION_BLOCKED


def test_candidate_pending_is_order_pending():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(status=PaperOrderStatus.PENDING))
    assert result.outcome_status is ComparisonOutcomeStatus.ORDER_PENDING


def test_filled_long_contains_full_chain():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), effect=effect(), performance=performance(), reference_price=100)
    assert result.outcome_status is ComparisonOutcomeStatus.ORDER_FILLED
    assert result.paper_order_id == "o-d1" and result.paper_fill_id == "f-d1" and result.paper_position_effect == "e-d1"


def test_filled_short_contains_full_chain():
    result = compare_shadow_decision(commander("CANDIDATE_SHORT"), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(side=PaperSide.SELL), fill=fill(side=PaperSide.SELL))
    assert result.outcome_status is ComparisonOutcomeStatus.ORDER_FILLED


def test_correct_direction_long():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=performance())
    assert result.decision_quality is DecisionQuality.CORRECT_DIRECTION


def test_wrong_direction_long():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.BUY, TS, TS, 100, 90, 2, 0, 0)
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,)))
    assert result.decision_quality is DecisionQuality.WRONG_DIRECTION


def test_correct_direction_short():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.SELL, TS, TS, 100, 90, 2, 0, 0)
    result = compare_shadow_decision(commander("CANDIDATE_SHORT"), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(side=PaperSide.SELL), fill=fill(side=PaperSide.SELL), performance=calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,)))
    assert result.decision_quality is DecisionQuality.CORRECT_DIRECTION


def test_wrong_direction_short():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.SELL, TS, TS, 100, 110, 2, 0, 0)
    result = compare_shadow_decision(commander("CANDIDATE_SHORT"), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(side=PaperSide.SELL), fill=fill(side=PaperSide.SELL), performance=calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,)))
    assert result.decision_quality is DecisionQuality.WRONG_DIRECTION


def test_breakeven_is_neutral():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.BUY, TS, TS, 100, 100, 2, 0, 0)
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=calculate_paper_performance(initial_equity=1000, equity_curve=(PaperEquityPoint(TS, 1000),), closed_trades=(trade,)))
    assert result.decision_quality is DecisionQuality.NEUTRAL


def test_missing_performance_is_data_not_available():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill())
    assert result.realized_pnl.status is MetricStatus.DATA_NOT_AVAILABLE


def test_reference_and_fill_price_are_compared():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), reference_price=100)
    assert result.reference_price.value == Decimal("100")
    assert result.fill_price == Decimal("101")
    assert result.execution_slippage_pct.value == Decimal("1")


def test_missing_reference_is_not_fabricated():
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill())
    assert result.reference_price.status is MetricStatus.DATA_NOT_AVAILABLE


def test_fees_are_preserved_from_fill():
    result = compare_shadow_decision(commander(), comparison_id="c1", order=order(), fill=fill(), execution_decision=execution(), risk_decision=risk())
    assert result.fees_paid.value == Decimal("2")


def test_slippage_is_preserved_from_fill():
    result = compare_shadow_decision(commander(), comparison_id="c1", order=order(), fill=fill(), execution_decision=execution(), risk_decision=risk())
    assert result.slippage_cost.value == Decimal("1")


def test_metrics_fees_and_slippage_are_exposed_for_validation():
    result = compare_shadow_decision(commander(), comparison_id="c1", order=order(), fill=fill(), execution_decision=execution(), risk_decision=risk(), performance=performance())
    assert result.metrics_fees_paid.status is MetricStatus.VALUE
    assert result.metrics_slippage_cost.status is MetricStatus.VALUE


def test_phase9_metrics_values_are_reused_without_recalculation():
    trade = PaperClosedTrade("t1", "BTC/USD", PaperSide.BUY, TS, TS, 100, 106, 2, 2, 1)
    metrics = calculate_paper_performance(
        initial_equity=1000,
        equity_curve=(PaperEquityPoint(TS, 1000),),
        closed_trades=(trade,),
        market_events=(HistoricalMarketEvent(TS, "BTC/USD", 100), HistoricalMarketEvent(TS, "BTC/USD", 110), HistoricalMarketEvent(TS, "BTC/USD", 95)),
    )
    result = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=metrics)
    assert result.paper_return_pct.value == metrics.total_return_pct.value
    assert result.mfe.value == metrics.mfe.value
    assert result.mae.value == metrics.mae.value


def test_cancelled_and_expired_are_distinct():
    cancelled = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(status=PaperOrderStatus.CANCELLED))
    expired = compare_shadow_decision(commander("CANDIDATE_LONG", "d2"), comparison_id="c2", execution_decision=execution("d2"), risk_decision=risk("d2"), order=order("d2", PaperOrderStatus.EXPIRED))
    assert cancelled.outcome_status is ComparisonOutcomeStatus.ORDER_CANCELLED
    assert expired.outcome_status is ComparisonOutcomeStatus.ORDER_EXPIRED


def test_order_lineage_mismatch_is_rejected():
    with pytest.raises(ValueError, match="lineage"):
        compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order("other"))


def test_fill_lineage_mismatch_is_rejected():
    with pytest.raises(ValueError, match="fill lineage"):
        compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=replace(fill(), paper_order_id="other"))


def test_effect_lineage_mismatch_is_rejected():
    with pytest.raises(ValueError, match="effect lineage"):
        compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), effect=replace(effect(), paper_fill_id="other"))


def test_two_independent_comparisons_have_no_crossover():
    first = compare_shadow_decision(commander(decision_id="d1"), comparison_id="c1", execution_decision=execution("d1"), risk_decision=risk("d1"), order=order("d1"), fill=fill("d1"))
    second = compare_shadow_decision(commander(decision_id="d2"), comparison_id="c2", execution_decision=execution("d2"), risk_decision=risk("d2"), order=order("d2"), fill=fill("d2"))
    assert validate_comparisons((first, second)).comparison_lineage_crossover_count == 0


def test_duplicate_decisions_are_reported_as_crossover():
    item = compare_shadow_decision(commander(), comparison_id="c1")
    assert validate_comparisons((item, item)).comparison_lineage_crossover_count == 1


def test_fee_and_slippage_mismatch_are_reported():
    item = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=performance(fees="9", slippage="8"))
    report = validate_comparisons((item,))
    assert report.comparison_fee_mismatch_count == 1
    assert report.comparison_slippage_mismatch_count == 1


def test_matching_fee_and_slippage_are_zero_mismatch():
    item = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), performance=performance())
    report = validate_comparisons((item,))
    assert report.comparison_fee_mismatch_count == 0
    assert report.comparison_slippage_mismatch_count == 0


def test_comparison_is_repeatable_and_serializable():
    kwargs = dict(execution_decision=execution(), risk_decision=risk(), order=order(), fill=fill(), reference_price=100)
    first = compare_shadow_decision(commander(), comparison_id="c1", **kwargs)
    second = compare_shadow_decision(commander(), comparison_id="c1", **kwargs)
    assert first.to_dict() == second.to_dict()


def test_no_commander_feedback_or_model_update_surface_exists():
    from Robo.genesis_paper import comparison
    assert not hasattr(comparison, "update_commander")
    assert not hasattr(comparison, "train")
    assert not hasattr(comparison, "requests")


def test_paper_only_invariant_and_no_authority_fields():
    result = compare_shadow_decision(commander("NO_ACTION"), comparison_id="c1")
    assert result.paper_only is True
    assert "execution_authority" not in result.to_dict()


def test_execution_blocked_and_risk_rejected_do_not_collapse():
    rejected = compare_shadow_decision(commander(), comparison_id="c1", execution_decision=execution(), risk_decision=risk(kind=PaperRiskDecisionType.REJECT))
    blocked = compare_shadow_decision(commander("CANDIDATE_LONG", "d2"), comparison_id="c2", execution_decision=execution("d2"), risk_decision=risk("d2"), gate_result=SimpleNamespace(decision=PaperGateDecisionType.BLOCK_PAPER_EXECUTION))
    assert rejected.outcome_status is ComparisonOutcomeStatus.RISK_REJECTED
    assert blocked.outcome_status is ComparisonOutcomeStatus.EXECUTION_BLOCKED
