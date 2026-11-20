from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DATA_NOT_AVAILABLE, DecisionType, ValidationObservation
from genesis_validation.labeling import label_observation
from genesis_validation.regime_analysis import CanonicalRegimePoint, RegimeAnalysisConfig, analyze_regimes, resolve_canonical_regime


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(regime="TREND", net="1", ret="1", strategy="s1", horizon="H1", kind=DecisionType.CANDIDATE_LONG, decision_id="d", **kwargs):
    values = dict(commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD", decision_type=kind,
                  confidence="0.5", strategy_id=strategy, market_regime=regime, validation_horizon=horizon,
                  net_pnl=net, return_pct=ret, paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    observation = ValidationObservation(**values)
    return CalibrationRecord(observation, label_observation(observation))


def test_one_regime_positive_and_negative_status():
    assert analyze_regimes([record(net="1")]).summaries[0].performance_status == "POSITIVE"
    assert analyze_regimes([record(net="-1")]).summaries[0].performance_status == "NEGATIVE"


def test_two_regimes_are_independent():
    report = analyze_regimes([record(regime="TREND"), record(regime="RANGE", decision_id="d2")])
    assert {item.market_regime for item in report.summaries} == {"TREND", "RANGE"}


def test_missing_regime_is_preserved_and_not_defaulted():
    report = analyze_regimes([record(regime=None)])
    assert report.missing_regime_count == 1
    assert report.summaries == ()
    assert report.observations_without_regime == 1


def test_canonical_regime_identity_and_as_of_resolution():
    points = [CanonicalRegimePoint("BTC/USD", TS - timedelta(minutes=1), "RANGE"), CanonicalRegimePoint("BTC/USD", TS + timedelta(minutes=1), "TREND")]
    assert resolve_canonical_regime(record().observation, points) == "RANGE"


def test_future_only_regime_is_not_used():
    points = [CanonicalRegimePoint("BTC/USD", TS + timedelta(minutes=1), "TREND")]
    assert resolve_canonical_regime(record().observation, points) == DATA_NOT_AVAILABLE


def test_horizon_isolation():
    report = analyze_regimes([record(horizon="H1"), record(horizon="H2", decision_id="d2")])
    assert {(item.market_regime, item.validation_horizon) for item in report.summaries} == {("TREND", "H1"), ("TREND", "H2")}


def test_strategy_regime_matrix():
    report = analyze_regimes([record(strategy="a"), record(strategy="b", decision_id="d2")])
    assert {(item.strategy_id, item.market_regime) for item in report.summaries} == {("a", "TREND"), ("b", "TREND")}


def test_long_short_regime_segmentation():
    rows = [record(kind=DecisionType.CANDIDATE_LONG), record(kind=DecisionType.CANDIDATE_SHORT, decision_id="d2")]
    assert analyze_regimes(rows, direction_segment="LONG").summaries[0].sample_count == 1
    assert analyze_regimes(rows, direction_segment="SHORT").summaries[0].sample_count == 1


def test_confidence_band_regime_slice_without_redefinition():
    report = analyze_regimes([record(confidence_band="0.40-0.60")], confidence_band="0.40-0.60")
    assert report.summaries[0].confidence_band == "0.40-0.60"


def test_metrics_directional_economic_and_mfe_mae():
    item = record(net="2", ret="2", gross_pnl="4", fees="1", slippage="1", mfe="3", mae="-2")
    summary = analyze_regimes([item]).summaries[0]
    assert summary.directional_accuracy == Decimal("1")
    assert summary.net_pnl == Decimal("2")
    assert summary.expectancy == Decimal("2")
    assert summary.gross_pnl == Decimal("4")
    assert summary.fees_paid == Decimal("1")
    assert summary.slippage_cost == Decimal("1")
    assert summary.average_mfe == Decimal("3")
    assert summary.average_mae == Decimal("-2")


def test_profit_factor_and_drawdown_are_chronological():
    report = analyze_regimes([record(net="3"), record(net="-2", decision_id="d2")])
    summary = report.summaries[0]
    assert summary.profit_factor == Decimal("1.5")
    assert summary.max_drawdown == Decimal("-2")


def test_sample_size_and_insufficient_data():
    report = analyze_regimes([record(net=None, ret=None)])
    summary = report.summaries[0]
    assert summary.sample_size_status.value == "INSUFFICIENT_SAMPLE"
    assert summary.performance_status == "INSUFFICIENT_DATA"


def test_mixed_status():
    report = analyze_regimes([record(net="2"), record(net="-1", decision_id="d2")])
    assert report.summaries[0].performance_status == "MIXED"


def test_empty_segment_has_no_fabricated_metrics():
    assert analyze_regimes([]).summaries == ()


def test_non_candidates_are_not_trade_metrics():
    assert analyze_regimes([record(kind=DecisionType.WATCH, net=None, ret=None)]).summaries == ()


def test_deterministic_comparison_and_no_opaque_score():
    rows = [record(regime="A"), record(regime="B", decision_id="d2", net="2")]
    first = analyze_regimes(rows)
    assert first == analyze_regimes(rows)
    assert not hasattr(first, "opaque_score")


def test_source_immutability_and_no_live_path():
    source = record()
    analyze_regimes([source])
    assert source.observation.market_regime == "TREND"
    assert source.observation.confidence == Decimal("0.5")
    assert "live" not in str(analyze_regimes([source])).lower()


def test_invalid_direction_segment():
    with pytest.raises(ValueError):
        analyze_regimes([record()], direction_segment="INVALID")
