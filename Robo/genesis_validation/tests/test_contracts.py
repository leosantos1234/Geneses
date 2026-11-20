import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.contracts import (
    DATA_NOT_AVAILABLE,
    PRIMARY_ECONOMIC_METRICS,
    DecisionType,
    HorizonSpec,
    PromotionState,
    SampleSizeStatus,
    TemporalSplit,
    ValidationLabel,
    ValidationObservation,
    ValidationRunSummary,
    SUPPORTED_HORIZONS,
    classify_sample_size,
)


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def observation(decision_type=DecisionType.CANDIDATE_LONG, **kwargs):
    values = {
        "commander_decision_id": "decision-1",
        "timestamp_utc": TS,
        "symbol": "BTC/USD",
        "decision_type": decision_type,
    }
    values.update(kwargs)
    return ValidationObservation(**values)


def test_valid_candidate_long_observation():
    item = observation(confidence="0.80", reference_price="100", net_pnl="5", fees="1", slippage="0.5")
    assert item.derived_label is ValidationLabel.CORRECT
    assert item.net_pnl == Decimal("5")


def test_valid_candidate_short_observation():
    item = observation(DecisionType.CANDIDATE_SHORT, confidence=0.8, net_pnl="-5")
    assert item.derived_label is ValidationLabel.CORRECT


def test_no_action_observation_is_not_a_prediction_error():
    assert observation(DecisionType.NO_ACTION).derived_label is ValidationLabel.NOT_APPLICABLE


def test_watch_observation_is_not_a_prediction_error():
    assert observation(DecisionType.WATCH).derived_label is ValidationLabel.NOT_APPLICABLE


def test_insufficient_data_is_not_a_prediction():
    assert observation(DecisionType.INSUFFICIENT_DATA).derived_label is ValidationLabel.NOT_APPLICABLE


def test_missing_optional_metric_is_explicit():
    payload = observation().to_dict()
    assert payload["mfe"] == DATA_NOT_AVAILABLE
    assert payload["market_regime"] == DATA_NOT_AVAILABLE


def test_utc_aware_timestamp_required():
    item = observation()
    assert item.timestamp_utc.tzinfo is timezone.utc


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        observation(timestamp_utc=datetime(2026, 1, 1))


def test_confidence_range_validation():
    with pytest.raises(ValueError, match="between 0 and 1"):
        observation(confidence="1.01")


def test_economic_fields_use_decimal():
    item = observation(net_pnl="1.10", return_pct=Decimal("2.2"))
    assert isinstance(item.net_pnl, Decimal)
    assert isinstance(item.return_pct, Decimal)


@pytest.mark.parametrize(("count", "expected"), [
    (0, SampleSizeStatus.INSUFFICIENT_SAMPLE),
    (29, SampleSizeStatus.INSUFFICIENT_SAMPLE),
    (30, SampleSizeStatus.LIMITED_SAMPLE),
    (99, SampleSizeStatus.LIMITED_SAMPLE),
    (100, SampleSizeStatus.MODERATE_SAMPLE),
    (299, SampleSizeStatus.MODERATE_SAMPLE),
    (300, SampleSizeStatus.STRONGER_SAMPLE),
])
def test_sample_size_classification(count, expected):
    assert classify_sample_size(count) is expected


def test_promotion_state_enum_and_invalid_value():
    assert PromotionState.KEEP_SHADOW.value == "KEEP_SHADOW"
    with pytest.raises(ValueError):
        ValidationRunSummary(0, SampleSizeStatus.INSUFFICIENT_SAMPLE, "KEEP_SHADOW")


def test_temporal_train_validation_test_split():
    split = TemporalSplit(TS, TS + timedelta(days=1), TS + timedelta(days=2))
    assert split.partition(TS) == "TRAIN"
    assert split.partition(TS + timedelta(hours=12)) == "VALIDATION"
    assert split.partition(TS + timedelta(days=1)) == "VALIDATION"
    assert split.partition(TS + timedelta(days=2)) == "OUT_OF_SAMPLE_TEST"


def test_temporal_ordering_invariant():
    with pytest.raises(ValueError, match="train < validation < test"):
        TemporalSplit(TS + timedelta(days=1), TS, TS + timedelta(days=2))


def test_cost_aware_outcome_fields_are_preserved():
    item = observation(net_pnl="8", fees="1.25", slippage="0.75")
    assert item.net_pnl == Decimal("8")
    assert item.fees + item.slippage == Decimal("2.00")


def test_deterministic_serialization():
    item = observation(net_pnl="1.00", confidence="0.5")
    assert item.to_json() == item.to_json()
    assert json.loads(item.to_json())["confidence"] == "0.5"


def test_source_decision_is_immutable():
    item = observation()
    with pytest.raises((AttributeError, TypeError)):
        item.net_pnl = Decimal("99")


def test_no_auto_promotion():
    summary = ValidationRunSummary(30, SampleSizeStatus.LIMITED_SAMPLE, PromotionState.CANDIDATE_FOR_NEXT_STAGE)
    assert summary.auto_promotion is False


def test_future_leakage_and_mutation_are_disabled():
    summary = ValidationRunSummary(0, SampleSizeStatus.INSUFFICIENT_SAMPLE, PromotionState.KEEP_SHADOW)
    assert summary.validation_future_leakage_allowed is False
    assert summary.validation_mutates_source_decisions is False


def test_horizon_is_explicit_and_positive():
    assert SUPPORTED_HORIZONS == ("H1", "H2", "H3")
    horizon = HorizonSpec("H1", timedelta(minutes=5))
    assert horizon.duration == timedelta(minutes=5)
    with pytest.raises(ValueError, match="positive"):
        HorizonSpec("H1", timedelta(0))
