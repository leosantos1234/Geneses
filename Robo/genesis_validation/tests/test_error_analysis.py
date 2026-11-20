from decimal import Decimal
import sys

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType, ValidationObservation
from genesis_validation.error_analysis import ErrorClass, analyze_errors
from genesis_validation.labeling import label_observation
from genesis_validation.tests.test_walk_forward import record


def custom(**kwargs):
    values = dict(commander_decision_id="e", timestamp_utc=record(0).observation.timestamp_utc, symbol="BTC/USD", decision_type=DecisionType.CANDIDATE_LONG, confidence="0.5", validation_horizon="H1", paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    obs = ValidationObservation(**values)
    return CalibrationRecord(obs, label_observation(obs))


def classes(item, **kwargs):
    return set(analyze_errors([item], **kwargs).records[0].error_classes)


def test_wrong_direction_long_and_short():
    assert ErrorClass.WRONG_DIRECTION in classes(custom(net_pnl="-1", return_pct="-1"))
    short = custom(commander_decision_id="s", decision_type=DecisionType.CANDIDATE_SHORT, net_pnl="1", return_pct="1")
    assert ErrorClass.WRONG_DIRECTION in classes(short)


def test_high_and_low_confidence_wrong():
    assert ErrorClass.HIGH_CONFIDENCE_ERROR in classes(custom(net_pnl="-1", return_pct="-1", confidence_band="0.80-1.00"))
    assert ErrorClass.LOW_CONFIDENCE_ERROR in classes(custom(net_pnl="-1", return_pct="-1", confidence_band="0.20-0.40"))


def test_risk_rejection_correct_and_wrong_without_blame():
    correct = custom(net_pnl=None, return_pct="1", risk_outcome="REJECT", paper_execution_outcome="REJECTED", fill_outcome="REJECTED")
    wrong = custom(commander_decision_id="w", net_pnl=None, return_pct="-1", risk_outcome="REJECT", paper_execution_outcome="REJECTED", fill_outcome="REJECTED")
    assert ErrorClass.RISK_REJECTED_CORRECT_DIRECTION in classes(correct)
    assert ErrorClass.RISK_REJECTED_WRONG_DIRECTION in classes(wrong)
    assert ErrorClass.EXECUTED_LOSS not in classes(correct)


def test_executed_loss_and_cost_eroded_edge():
    assert ErrorClass.EXECUTED_LOSS in classes(custom(net_pnl="-2", return_pct="-2"))
    assert ErrorClass.COST_ERODED_EDGE in classes(custom(net_pnl="-1", return_pct="1", gross_pnl="1", fees="1", slippage="1"))


def test_missed_opportunity_requires_explicit_marker():
    valid = custom(net_pnl=None, return_pct="1", paper_execution_outcome="CANCELLED", fill_outcome="CANCELLED", source_data_status="MISSED_OPPORTUNITY_ELIGIBLE")
    uncertain = custom(commander_decision_id="u", net_pnl=None, return_pct="1", paper_execution_outcome="CANCELLED", fill_outcome="CANCELLED")
    assert ErrorClass.MISSED_OPPORTUNITY in classes(valid)
    assert ErrorClass.MISSED_OPPORTUNITY not in classes(uncertain)


def test_strategy_regime_direction_and_benchmark_attribution():
    item = custom(strategy_id="s1", market_regime="RANGE", net_pnl="-1", return_pct="-1")
    report = analyze_errors([item], benchmark_underperformance_ids={"e"})
    assert report.records[0].strategy_id == "s1"
    assert report.records[0].market_regime == "RANGE"
    assert ErrorClass.BENCHMARK_UNDERPERFORMANCE in report.records[0].error_classes
    assert report.concentration["direction"] == (("LONG", 1),)


def test_missing_volatility_and_data_quality():
    item = custom(net_pnl=None, return_pct=None, paper_execution_outcome="DATA_NOT_AVAILABLE", fill_outcome="DATA_NOT_AVAILABLE")
    assert ErrorClass.DATA_QUALITY_FAILURE in classes(item)


def test_walk_forward_and_oos_persistence_are_preserved():
    report = analyze_errors([custom()], walk_forward_persistence={"e": "RECURRING"}, oos_persistence={"e": "PERSISTS_OOS"})
    assert report.records[0].walk_forward_persistence == "RECURRING"
    assert report.records[0].oos_persistence == "PERSISTS_OOS"


def test_error_rates_and_multilabel():
    item = custom(net_pnl="-2", return_pct="-1", confidence_band="0.80-1.00")
    report = analyze_errors([item])
    assert report.class_counts[ErrorClass.WRONG_DIRECTION.value] == 1
    assert report.class_rates[ErrorClass.WRONG_DIRECTION.value] == Decimal("1")
    assert len(report.records[0].error_classes) >= 2


def test_deterministic_repeatability_and_immutability():
    item = custom(net_pnl="-1", return_pct="-1")
    first = analyze_errors([item])
    second = analyze_errors([item])
    assert first == second
    assert item.observation.confidence == Decimal("0.5")


def test_non_candidates_are_not_trade_errors():
    obs = ValidationObservation(commander_decision_id="n", timestamp_utc=record(0).observation.timestamp_utc, symbol="BTC/USD", decision_type=DecisionType.NO_ACTION, validation_horizon="H1")
    item = CalibrationRecord(obs, label_observation(obs))
    assert analyze_errors([item]).records == ()


def test_no_opaque_or_automatic_action():
    report = analyze_errors([custom()])
    assert "score" not in str(report).lower()
    assert "live" not in str(report).lower()
