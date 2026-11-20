import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType, ValidationObservation
from genesis_validation.labeling import label_observation
from genesis_validation.strategy_performance import analyze_strategy_performance


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(net="1", ret="1", strategy="s1", horizon="H1", kind=DecisionType.CANDIDATE_LONG, decision_id="d", **kwargs):
    values = dict(commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD", decision_type=kind,
                  confidence="0.5", strategy_id=strategy, strategy_name=("Alpha" if strategy else None),
                  validation_horizon=horizon, net_pnl=net, return_pct=ret,
                  paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    observation = ValidationObservation(**values)
    return CalibrationRecord(observation, label_observation(observation))


def summary(rows):
    return analyze_strategy_performance(rows).summaries[0]


def test_single_strategy_winning_trades():
    result = summary([record("2", "2"), record("3", "3", decision_id="d2")])
    assert result.winning_trade_count == 2
    assert result.net_pnl == Decimal("5")
    assert result.performance_status == "POSITIVE"


def test_single_strategy_losing_trades():
    result = summary([record("-2", "-2"), record("-3", "-3", decision_id="d2")])
    assert result.losing_trade_count == 2
    assert result.performance_status == "NEGATIVE"


def test_mixed_strategy_state():
    assert summary([record("2"), record("-1", decision_id="d2")]).performance_status == "MIXED"


def test_no_eligible_trades_are_not_fabricated():
    report = analyze_strategy_performance([record(kind=DecisionType.NO_ACTION, net=None, ret=None)])
    assert report.summaries == ()


def test_two_strategies_are_independent():
    report = analyze_strategy_performance([record(strategy="s1"), record(strategy="s2", decision_id="d2")])
    assert {(item.strategy_id, item.validation_horizon) for item in report.summaries} == {("s1", "H1"), ("s2", "H1")}


def test_no_cross_strategy_attribution():
    report = analyze_strategy_performance([record("2", strategy="s1"), record("-5", strategy="s2", decision_id="d2")])
    by_id = {item.strategy_id: item for item in report.summaries}
    assert by_id["s1"].net_pnl == Decimal("2")
    assert by_id["s2"].net_pnl == Decimal("-5")


def test_horizon_isolation():
    report = analyze_strategy_performance([record(horizon="H1"), record(horizon="H2", decision_id="d2")])
    assert {(item.strategy_id, item.validation_horizon) for item in report.summaries} == {("s1", "H1"), ("s1", "H2")}


def test_long_short_segmentation():
    rows = [record(kind=DecisionType.CANDIDATE_LONG), record(kind=DecisionType.CANDIDATE_SHORT, decision_id="d2")]
    assert analyze_strategy_performance(rows, direction_segment="LONG").summaries[0].sample_count == 1
    assert analyze_strategy_performance(rows, direction_segment="SHORT").summaries[0].sample_count == 1


def test_confidence_band_slice():
    report = analyze_strategy_performance([record(confidence_band="0.40-0.60")], confidence_band="0.40-0.60")
    assert report.summaries[0].confidence_band == "0.40-0.60"


def test_missing_strategy_is_separate_and_no_default():
    report = analyze_strategy_performance([record(strategy=None)])
    assert report.summaries[0].strategy_id == "DATA_NOT_AVAILABLE"
    assert report.missing_strategy_count == 1


def test_directional_accuracy_and_rates():
    result = summary([record("1", "1"), record("-1", "-1", decision_id="d2"), record("0", "0", decision_id="d3")])
    assert result.directional_accuracy == Decimal("0.5")
    assert result.win_rate == Decimal(1) / Decimal(3)
    assert result.loss_rate == Decimal(1) / Decimal(3)
    assert result.breakeven_rate == Decimal(1) / Decimal(3)


def test_gross_net_fees_and_slippage():
    result = summary([record("7", "7", gross_pnl="10", fees="2", slippage="1")])
    assert result.gross_pnl == Decimal("10")
    assert result.fees_paid == Decimal("2")
    assert result.slippage_cost == Decimal("1")
    assert result.net_pnl == Decimal("7")


def test_expectancy_and_profit_factor():
    result = summary([record("2"), record("-1", decision_id="d2")])
    assert result.expectancy_net_pnl == Decimal("0.5")
    assert result.profit_factor == Decimal("2")


def test_profit_factor_no_losses_is_infinity():
    assert summary([record("2")]).profit_factor == Decimal("Infinity")


def test_drawdown_uses_chronological_input():
    result = summary([record("5"), record("-3", decision_id="d2"), record("-4", decision_id="d3")])
    assert result.max_drawdown == Decimal("-7")


def test_return_median_and_mfe_mae():
    result = summary([record("1", "1", mfe="3", mae="-1"), record("2", "3", decision_id="d2", mfe="5", mae="-3")])
    assert result.average_return_pct == Decimal("2")
    assert result.median_return_pct == Decimal("2")
    assert result.average_mfe == Decimal("4")
    assert result.average_mae == Decimal("-2")


def test_exposure_and_turnover_are_aggregated_when_source_provides_them():
    result = summary([record(gross_exposure="10", net_exposure="5", turnover="12")])
    assert result.gross_exposure == Decimal("10")
    assert result.net_exposure == Decimal("5")
    assert result.turnover == Decimal("12")


def test_sample_status_and_insufficient_state():
    result = summary([record(net=None, ret=None)])
    assert result.sample_size_status.value == "INSUFFICIENT_SAMPLE"
    assert result.performance_status == "INSUFFICIENT_DATA"


def test_deterministic_lexicographic_ranking():
    rows = [record("1", strategy="a"), record("3", strategy="b", decision_id="d2")]
    report = analyze_strategy_performance(rows)
    assert report.ranking[0] == ("b", "H1")
    assert report == analyze_strategy_performance(rows)


def test_non_candidates_not_in_trade_metrics():
    report = analyze_strategy_performance([record(kind=DecisionType.WATCH, net=None, ret=None)])
    assert report.summaries == ()


def test_source_identity_confidence_and_strategy_unchanged():
    source = record()
    analyze_strategy_performance([source])
    assert source.observation.strategy_id == "s1"
    assert source.observation.confidence == Decimal("0.5")


def test_invalid_direction_segment_rejected():
    with pytest.raises(ValueError):
        analyze_strategy_performance([record()], direction_segment="UNKNOWN")


def test_no_automatic_winner_or_live_path():
    report = analyze_strategy_performance([record()])
    assert not hasattr(report, "best_strategy_for_live")
    assert "live" not in str(report).lower()
