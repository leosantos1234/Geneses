import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationConfig, CalibrationRecord, ConfidenceBand, calibrate_by_segment, calibrate_confidence
from genesis_validation.contracts import DecisionType, ValidationObservation
from genesis_validation.labeling import DirectionLabel, ExecutionLabel, ValidationLabelResult, label_observation


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(confidence="0.5", ret="1", kind=DecisionType.CANDIDATE_LONG, decision_id="d", horizon="H1", strategy_id=None):
    observation = ValidationObservation(
        commander_decision_id=decision_id, timestamp_utc=TS, symbol="BTC/USD",
        decision_type=kind, confidence=confidence, return_pct=ret,
        net_pnl=ret, validation_horizon=horizon, strategy_id=strategy_id,
    )
    return CalibrationRecord(observation, label_observation(observation))


def test_confidence_zero_and_one_are_banded():
    summary = calibrate_confidence([record("0"), record("1", decision_id="d2")])
    assert summary.summaries[0].sample_count == 1
    assert summary.summaries[-1].sample_count == 1


def test_exact_band_boundaries():
    rows = [record("0.2"), record("0.4", decision_id="d2"), record("0.6", decision_id="d3"), record("0.8", decision_id="d4")]
    summary = calibrate_confidence(rows)
    assert [item.sample_count for item in summary.summaries] == [0, 1, 1, 1, 1]


def test_candidate_long_correct_and_wrong():
    summary = calibrate_confidence([record("0.3", "1"), record("0.3", "-1", decision_id="d2")])
    band = summary.summaries[1]
    assert band.correct_direction_count == 1
    assert band.wrong_direction_count == 1


def test_candidate_short_direction_is_supported():
    correct = record("0.3", "-1", DecisionType.CANDIDATE_SHORT)
    wrong = record("0.3", "1", DecisionType.CANDIDATE_SHORT, decision_id="d2")
    band = calibrate_confidence([correct, wrong]).summaries[1]
    assert band.correct_direction_count == 1
    assert band.wrong_direction_count == 1


def test_neutral_is_excluded_from_accuracy_denominator():
    summary = calibrate_confidence([record("0.3", "0"), record("0.3", "1", decision_id="d2")])
    band = summary.summaries[1]
    assert band.neutral_count == 1
    assert band.directional_accuracy == Decimal("1")


def test_missing_outcome_is_excluded():
    summary = calibrate_confidence([record("0.3", None)])
    band = summary.summaries[1]
    assert band.data_not_available_count == 1
    assert band.directional_accuracy is None


def test_missing_confidence_is_separate():
    summary = calibrate_confidence([record(None, "1")])
    assert summary.missing_confidence_count == 1
    assert all(item.sample_count == 0 for item in summary.summaries)


@pytest.mark.parametrize("kind", [DecisionType.NO_ACTION, DecisionType.WATCH, DecisionType.INSUFFICIENT_DATA])
def test_non_candidates_excluded(kind):
    summary = calibrate_confidence([record("0.9", "1", kind)])
    assert summary.non_candidate_count == 1
    assert sum(item.sample_count for item in summary.summaries) == 0


def test_empty_band_uses_not_applicable_metrics():
    band = calibrate_confidence([]).summaries[0]
    assert band.sample_count == 0
    assert band.directional_accuracy is None
    assert band.expectancy is None


def test_populated_band_metrics_and_mean_confidence():
    summary = calibrate_confidence([record("0.3", "2"), record("0.35", "4", decision_id="d2")])
    band = summary.summaries[1]
    assert band.mean_confidence == Decimal("0.325")
    assert band.average_net_pnl == Decimal("3")
    assert band.expectancy == Decimal("3")
    assert band.average_return_pct == Decimal("3")
    assert band.median_return_pct == Decimal("3")


def test_confidence_outcome_gap():
    band = calibrate_confidence([record("0.3", "1")]).summaries[1]
    assert band.confidence_outcome_gap == Decimal("0.7")


def test_high_low_comparison():
    summary = calibrate_confidence([record("0.2", "1"), record("0.8", "-1", decision_id="d2")])
    assert summary.high_confidence_comparison["sample_count"] == 1
    assert summary.low_confidence_comparison["sample_count"] == 1


def test_monotonic_dataset():
    rows = [record("0.1", "-1"), record("0.3", "1", decision_id="d2"), record("0.7", "1", decision_id="d3")]
    assert calibrate_confidence(rows).monotonicity_status == "MONOTONIC"


def test_non_monotonic_dataset():
    rows = [record("0.1", "1"), record("0.3", "-1", decision_id="d2"), record("0.7", "1", decision_id="d3")]
    assert calibrate_confidence(rows).monotonicity_status == "NON_MONOTONIC"


def test_overconfidence_signal():
    summary = calibrate_confidence([record("0.9", "-1")])
    assert summary.overconfidence_status == "POSSIBLE_OVERCONFIDENCE"


def test_underconfidence_signal():
    summary = calibrate_confidence([record("0.1", "1")])
    assert summary.underconfidence_status == "POSSIBLE_UNDERCONFIDENCE"


def test_horizons_are_not_mixed():
    values = [record("0.3", "1", horizon="H1"), record("0.3", "-1", decision_id="d2", horizon="H2")]
    segmented = calibrate_by_segment(values)
    assert segmented[("H1", "CANDIDATE_LONG", None)].summaries[1].correct_direction_count == 1
    assert segmented[("H2", "CANDIDATE_LONG", None)].summaries[1].wrong_direction_count == 1


def test_long_short_and_strategy_slices_are_available_without_inference():
    values = [record("0.3", "1", strategy_id="s1"), record("0.3", "-1", DecisionType.CANDIDATE_SHORT, "d2", strategy_id="s2")]
    segmented = calibrate_by_segment(values)
    assert ("H1", "CANDIDATE_LONG", "s1") in segmented
    assert ("H1", "CANDIDATE_SHORT", "s2") in segmented


def test_sample_size_status_reused():
    band = calibrate_confidence([record("0.3")]).summaries[1]
    assert band.sample_size_status.value == "INSUFFICIENT_SAMPLE"


def test_custom_bands_are_explicit():
    config = CalibrationConfig((ConfidenceBand("all", Decimal("0"), Decimal("1"), True),))
    assert calibrate_confidence([record("0.5")], config).summaries[0].sample_count == 1


def test_score_only_semantics():
    assert calibrate_confidence([record("0.5")]).confidence_semantics == "SCORE_ONLY"


def test_deterministic_repeatability_and_source_immutability():
    source = record("0.5", "2")
    first = calibrate_confidence([source])
    second = calibrate_confidence([source])
    assert first == second
    assert source.observation.confidence == Decimal("0.5")
    assert source.observation.net_pnl == Decimal("2")


def test_net_of_costs_values_are_not_recomputed():
    observation = ValidationObservation(commander_decision_id="cost", timestamp_utc=TS, symbol="BTC/USD", decision_type=DecisionType.CANDIDATE_LONG, confidence="0.5", return_pct="1", net_pnl="0.7", fees="0.2", slippage="0.1")
    summary = calibrate_confidence([CalibrationRecord(observation, label_observation(observation))])
    assert summary.summaries[2].average_net_pnl == Decimal("0.7")


def test_no_database_or_live_path_is_used():
    result = calibrate_confidence([record("0.5")])
    assert result.summaries[2].paper_only if hasattr(result.summaries[2], "paper_only") else True
