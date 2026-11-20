from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType, SampleSizeStatus, ValidationObservation
from genesis_validation.labeling import label_observation
from genesis_validation.statistical_robustness import StatisticalConfig, analyze_robustness, wilson_interval


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(net="1", ret="1", decision_id="d", horizon="H1", kind=DecisionType.CANDIDATE_LONG):
    obs = ValidationObservation(commander_decision_id=decision_id, timestamp_utc=TS + timedelta(minutes=int(decision_id[1:]) if decision_id[1:].isdigit() else 0), symbol="BTC/USD", decision_type=kind, confidence="0.5", net_pnl=net, return_pct=ret, validation_horizon=horizon, paper_execution_outcome="FILLED", fill_outcome="FILLED")
    return CalibrationRecord(obs, label_observation(obs))


def test_sample_size_categories():
    assert analyze_robustness([]).sample_size_status is SampleSizeStatus.INSUFFICIENT_SAMPLE
    assert analyze_robustness([record()]).sample_size_status is SampleSizeStatus.INSUFFICIENT_SAMPLE


def test_wilson_interval_basic_and_extremes():
    interval = wilson_interval(5, 10)
    assert interval is not None and interval[0] < Decimal("0.5") < interval[1]
    assert wilson_interval(10, 10)[1] == Decimal("1")
    assert wilson_interval(0, 10)[0] == Decimal("0")


def test_deterministic_bootstrap_and_seed_effect():
    rows = [record(str(i + 1), ret=str(i + 1), decision_id=f"d{i}") for i in range(5)]
    a = analyze_robustness(rows, config=StatisticalConfig(seed=1, resample_count=50))
    b = analyze_robustness(rows, config=StatisticalConfig(seed=1, resample_count=50))
    c = analyze_robustness(rows, config=StatisticalConfig(seed=2, resample_count=50))
    assert a == b
    assert a.intervals[2].lower_bound != c.intervals[2].lower_bound


def test_positive_negative_and_zero_crossing_bootstrap():
    positive = analyze_robustness([record("1", decision_id="d1"), record("2", decision_id="d2")], config=StatisticalConfig(resample_count=50))
    negative = analyze_robustness([record("-1", ret="-1", decision_id="d1"), record("-2", ret="-2", decision_id="d2")], config=StatisticalConfig(resample_count=50))
    mixed = analyze_robustness([record("-1", ret="-1", decision_id="d1"), record("2", decision_id="d2")], config=StatisticalConfig(resample_count=50))
    assert positive.intervals[2].interval_status == "POSITIVE_INTERVAL"
    assert negative.intervals[2].interval_status == "NEGATIVE_INTERVAL"
    assert mixed.intervals[2].interval_status == "ZERO_CROSSING"


def test_missing_values_excluded_not_zero():
    result = analyze_robustness([record(net=None, ret=None)])
    assert result.eligible_count == 0
    assert result.excluded_missing_count == 1
    assert result.intervals[2].point_estimate is None


def test_block_bootstrap_policy_is_explicit():
    result = analyze_robustness([record(decision_id="d1"), record(decision_id="d2")], config=StatisticalConfig(block_size=2, resample_count=20))
    assert result.temporal_dependence_policy == "BLOCK_BOOTSTRAP"


def test_subperiods_are_chronological_and_stable():
    result = analyze_robustness([record("1", decision_id="d1"), record("2", decision_id="d2"), record("3", decision_id="d3"), record("4", decision_id="d4")], config=StatisticalConfig(subperiod_count=2, resample_count=20))
    assert [item.index for item in result.subperiods] == [0, 1]
    assert result.sign_consistency_status == "CONSISTENT_POSITIVE"
    assert result.subperiod_stability_status == "STABLE"


def test_unstable_and_sign_consistency():
    result = analyze_robustness([record("2", decision_id="d1"), record("-2", ret="-2", decision_id="d2"), record("2", decision_id="d3"), record("-2", ret="-2", decision_id="d4")], config=StatisticalConfig(subperiod_count=2, resample_count=20))
    assert result.sign_consistency_status == "MIXED"
    assert result.subperiod_stability_status == "UNSTABLE"


def test_outlier_sensitivity():
    result = analyze_robustness([record("100", decision_id="d1"), record("1", decision_id="d2"), record("1", decision_id="d3")], config=StatisticalConfig(resample_count=20))
    assert result.outlier_sensitivity_status == "OUTLIER_SENSITIVE"


def test_median_and_non_candidate_policy():
    rows = [record("1", ret="1", decision_id="d1"), record("100", ret="100", decision_id="d2"), record(kind=DecisionType.WATCH, net=None, ret=None, decision_id="d3")]
    result = analyze_robustness(rows, config=StatisticalConfig(resample_count=20))
    assert result.sample_count == 2
    assert result.intervals[3].point_estimate == Decimal("50.5")


def test_horizon_isolation_and_segment_limit():
    with pytest.raises(ValueError, match="one validation horizon"):
        analyze_robustness([record(horizon="H1"), record(horizon="H2", decision_id="d2")])


def test_source_immutability_and_config_validation():
    source = record()
    analyze_robustness([source], config=StatisticalConfig(resample_count=20))
    assert source.observation.confidence == Decimal("0.5")
    with pytest.raises(ValueError):
        StatisticalConfig(confidence_level=Decimal("0.97"))


def test_no_unsupported_significance_or_automation_fields():
    result = analyze_robustness([record()], config=StatisticalConfig(resample_count=20))
    assert "significant" not in str(result).lower()
    assert "live" not in str(result).lower()
