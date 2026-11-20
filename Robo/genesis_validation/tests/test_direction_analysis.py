from datetime import datetime, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType, ValidationObservation
from genesis_validation.direction_analysis import compare_long_short
from genesis_validation.direction_analysis import DirectionPerformanceSummary
from genesis_validation.labeling import label_observation


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(kind=DecisionType.CANDIDATE_LONG, net="1", ret="1", decision_id="d", horizon="H1", strategy="s1", regime="TREND", **kwargs):
    values = dict(commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD", decision_type=kind,
                  confidence="0.5", validation_horizon=horizon, strategy_id=strategy, market_regime=regime,
                  net_pnl=net, return_pct=ret, paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    observation = ValidationObservation(**values)
    return CalibrationRecord(observation, label_observation(observation))


def summary(rows, direction):
    source = type("SummaryContainer", (), {"summaries": rows})() if rows and isinstance(rows[0], DirectionPerformanceSummary) else compare_long_short(rows)
    return next(item for item in source.summaries if item.direction == direction)


def test_long_and_short_positive_negative_economic_outcomes():
    report = compare_long_short([record(net="2"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")])
    assert summary(report.summaries, "LONG").net_pnl == Decimal("2")
    assert summary(report.summaries, "SHORT").net_pnl == Decimal("-1")


def test_direction_labels_are_canonical():
    report = compare_long_short([record(net="-1", ret="-1"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")])
    assert summary(report.summaries, "LONG").wrong_direction_count == 1
    assert summary(report.summaries, "SHORT").correct_direction_count == 1


def test_horizon_isolation():
    report = compare_long_short([record(horizon="H1"), record(horizon="H2", decision_id="d2")])
    assert {(item.validation_horizon, item.direction) for item in report.summaries} == {("H1", "LONG"), ("H2", "LONG")}


def test_directional_accuracy_separate():
    rows = [record(net="1", ret="1"), record(net="-1", ret="-1", decision_id="d2"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")]
    report = compare_long_short(rows)
    assert summary(report.summaries, "LONG").directional_accuracy == Decimal("0.5")
    assert summary(report.summaries, "SHORT").directional_accuracy == Decimal("1")


def test_net_expectancy_fee_and_slippage_asymmetry():
    rows = [record(net="7", gross_pnl="10", fees="2", slippage="1"), record(DecisionType.CANDIDATE_SHORT, net="5", ret="-5", decision_id="s", gross_pnl="6", fees="0.5", slippage="0.5")]
    report = compare_long_short(rows)
    assert summary(report.summaries, "LONG").expectancy_net_pnl == Decimal("7")
    assert summary(report.summaries, "LONG").fees_paid == Decimal("2")
    assert summary(report.summaries, "SHORT").slippage_cost == Decimal("0.5")


def test_drawdown_mfe_mae_are_direction_specific():
    rows = [record(net="3", mfe="4", mae="-1"), record(net="-2", decision_id="d2", mfe="2", mae="-3"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s", mfe="1", mae="-2")]
    report = compare_long_short(rows)
    assert summary(report.summaries, "LONG").max_drawdown == Decimal("-2")
    assert summary(report.summaries, "LONG").average_mfe == Decimal("3")
    assert summary(report.summaries, "SHORT").average_mae == Decimal("-2")


def test_confidence_strategy_and_regime_matrices():
    rows = [record(strategy="a", regime="X", confidence_band="0.40-0.60"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s", strategy="b", regime="X", confidence_band="0.40-0.60")]
    report = compare_long_short(rows, confidence_band="0.40-0.60")
    assert ("a", "H1", "LONG") in report.strategy_direction_matrix
    assert ("b", "H1", "SHORT") in report.strategy_direction_matrix
    assert ("X", "H1", "LONG") in report.regime_direction_matrix
    assert ("0.40-0.60", "H1", "SHORT") in report.confidence_direction_matrix


def test_balance_statuses():
    balanced = compare_long_short([record(), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")])
    long_dominant = compare_long_short([record(), record(decision_id="d2"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")])
    short_dominant = compare_long_short([record(), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s"), record(DecisionType.CANDIDATE_SHORT, net="-2", ret="-2", decision_id="s2")])
    assert balanced.balance_status == "BALANCED"
    assert long_dominant.balance_status == "LONG_DOMINANT"
    assert short_dominant.balance_status == "SHORT_DOMINANT"


def test_asymmetry_statuses():
    long_stronger = compare_long_short([record(net="2"), record(DecisionType.CANDIDATE_SHORT, net="-1", ret="-1", decision_id="s")])
    short_stronger = compare_long_short([record(net="-3"), record(DecisionType.CANDIDATE_SHORT, net="-2", ret="-2", decision_id="s")])
    mixed = compare_long_short([record(net="2", ret="-1"), record(DecisionType.CANDIDATE_SHORT, net="1", ret="-1", decision_id="s")])
    assert long_stronger.performance_asymmetry_status == "LONG_STRONGER"
    assert short_stronger.performance_asymmetry_status == "SHORT_STRONGER"
    assert mixed.performance_asymmetry_status == "MIXED"


def test_empty_direction_is_insufficient():
    report = compare_long_short([record()])
    assert report.performance_asymmetry_status == "INSUFFICIENT_DATA"
    assert report.summaries[0].direction == "LONG"


def test_non_candidates_are_excluded():
    report = compare_long_short([record(DecisionType.NO_ACTION, net=None, ret=None)])
    assert report.summaries == ()
    assert report.long_candidate_count == 0


def test_deterministic_and_immutable():
    rows = [record()]
    first = compare_long_short(rows)
    assert first == compare_long_short(rows)
    assert rows[0].observation.decision_type is DecisionType.CANDIDATE_LONG
    assert rows[0].observation.confidence == Decimal("0.5")


def test_invalid_balance_threshold_and_no_live_path():
    with pytest.raises(ValueError):
        compare_long_short([record()], balance_threshold=Decimal("2"))
    assert "live" not in str(compare_long_short([record()])).lower()
