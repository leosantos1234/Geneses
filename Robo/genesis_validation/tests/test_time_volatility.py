from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DATA_NOT_AVAILABLE, DecisionType, ValidationObservation
from genesis_validation.labeling import label_observation
from genesis_validation.time_volatility import CanonicalVolatilityPoint, TimeVolatilityConfig, VolatilityBand, analyze_time_volatility, resolve_canonical_volatility


TS = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
CONFIG = TimeVolatilityConfig(volatility_bands=(VolatilityBand("LOW", Decimal("0"), Decimal("1")), VolatilityBand("HIGH", Decimal("1"), Decimal("2"), True)))


def record(timestamp=TS, net="1", volatility=None, volatility_band=None, strategy="s1", regime="TREND", horizon="H1", decision_id="d", **kwargs):
    values = dict(commander_decision_id=decision_id, timestamp_utc=timestamp, symbol="BTC/USD", decision_type=DecisionType.CANDIDATE_LONG,
                  confidence="0.5", net_pnl=net, return_pct=net, volatility=volatility, volatility_band=volatility_band,
                  strategy_id=strategy, market_regime=regime, validation_horizon=horizon,
                  paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    observation = ValidationObservation(**values)
    return CalibrationRecord(observation, label_observation(observation))


def test_utc_hour_and_decision_timestamp():
    report = analyze_time_volatility([record(timestamp=TS.replace(hour=10))], dimension="time", config=CONFIG)
    assert report.summaries[0].segment == "UTC_10_10"
    assert report.observations_with_segment == 1


def test_exact_hour_boundary_and_day_of_week():
    monday = record(timestamp=TS.replace(hour=0))
    assert monday.observation.timestamp_utc.weekday() == 0
    assert analyze_time_volatility([monday], dimension="time").summaries[0].segment == "UTC_00_00"


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        record(timestamp=datetime(2026, 1, 5, 10))


def test_session_is_not_heuristically_inferred():
    item = record(session=None)
    assert item.observation.session is None


def test_canonical_volatility_preserved_and_missing_is_explicit():
    assert analyze_time_volatility([record(volatility=Decimal("0.5"))], dimension="volatility", config=CONFIG).summaries[0].segment == "LOW"
    missing = analyze_time_volatility([record(volatility=None)], dimension="volatility", config=CONFIG)
    assert missing.observations_without_segment == 1


def test_no_default_volatility_band():
    report = analyze_time_volatility([record(volatility=None)], dimension="volatility", config=TimeVolatilityConfig())
    assert report.summaries == ()


def test_future_volatility_is_not_used():
    points = [CanonicalVolatilityPoint("BTC/USD", TS + timedelta(minutes=1), Decimal("1"))]
    assert resolve_canonical_volatility(record().observation, points) is None


def test_as_of_volatility_resolution():
    points = [CanonicalVolatilityPoint("BTC/USD", TS - timedelta(minutes=1), Decimal("0.5")), CanonicalVolatilityPoint("BTC/USD", TS + timedelta(minutes=1), Decimal("1.5"))]
    assert resolve_canonical_volatility(record().observation, points) == Decimal("0.5")


def test_explicit_volatility_bands_and_boundaries():
    assert analyze_time_volatility([record(volatility=Decimal("1"))], dimension="volatility", config=CONFIG).summaries[0].segment == "HIGH"
    with pytest.raises(ValueError):
        TimeVolatilityConfig(volatility_bands=(VolatilityBand("A", Decimal("0"), Decimal("1")), VolatilityBand("B", Decimal("2"), Decimal("3"))))


def test_horizon_isolation():
    report = analyze_time_volatility([record(horizon="H1"), record(horizon="H2", decision_id="d2")], config=CONFIG)
    assert {(item.segment, item.validation_horizon) for item in report.summaries} == {("UTC_10_10", "H1"), ("UTC_10_10", "H2")}


def test_time_metrics_and_chronological_drawdown():
    rows = [record(net="3"), record(net="-2", decision_id="d2")]
    summary = analyze_time_volatility(rows, config=CONFIG).summaries[0]
    assert summary.directional_accuracy == Decimal("0.5")
    assert summary.net_pnl == Decimal("1")
    assert summary.expectancy == Decimal("0.5")
    assert summary.max_drawdown == Decimal("-2")


def test_volatility_metrics():
    rows = [record(volatility=Decimal("0.5"), net="2"), record(volatility=Decimal("1.5"), net="-1", decision_id="d2")]
    report = analyze_time_volatility(rows, dimension="volatility", config=CONFIG)
    by_segment = {item.segment: item for item in report.summaries}
    assert by_segment["LOW"].net_pnl == Decimal("2")
    assert by_segment["HIGH"].expectancy == Decimal("-1")


def test_strategy_time_volatility_and_direction_filters():
    rows = [record(strategy="a", volatility=Decimal("0.5")), record(strategy="b", volatility=Decimal("1.5"), decision_id="d2")]
    assert analyze_time_volatility(rows, strategy_id="a", config=CONFIG).summaries[0].sample_count == 1
    assert analyze_time_volatility(rows, dimension="volatility", strategy_id="b", config=CONFIG).summaries[0].segment == "HIGH"
    short = record(decision_type=DecisionType.CANDIDATE_SHORT, decision_id="s", volatility=Decimal("0.5"))
    assert analyze_time_volatility([short], direction="SHORT", config=CONFIG).summaries[0].sample_count == 1


def test_regime_and_confidence_slices():
    rows = [record(confidence_band="0.40-0.60", regime="TREND")]
    report = analyze_time_volatility(rows, confidence_band="0.40-0.60", config=CONFIG)
    assert report.summaries[0].sample_count == 1
    assert rows[0].observation.market_regime == "TREND"


def test_dependency_status_and_empty_segments():
    sensitive = analyze_time_volatility([record(net="1", timestamp=TS), record(net="-1", timestamp=TS.replace(hour=11), decision_id="d2")], config=CONFIG)
    assert sensitive.dependency_status == "TIME_SENSITIVE"
    assert analyze_time_volatility([], config=CONFIG).summaries == ()


def test_deterministic_no_opaque_score_and_immutability():
    rows = [record()]
    first = analyze_time_volatility(rows, config=CONFIG)
    assert first == analyze_time_volatility(rows, config=CONFIG)
    assert rows[0].observation.volatility is None
    assert "score" not in str(first).lower()


def test_invalid_dimension_and_bucket_policy():
    with pytest.raises(ValueError):
        analyze_time_volatility([record()], dimension="invalid")
    with pytest.raises(ValueError):
        TimeVolatilityConfig(hour_bucket_size=5)
