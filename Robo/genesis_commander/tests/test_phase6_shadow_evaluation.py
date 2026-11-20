"""Phase 6 pure shadow-evaluation tests."""

from datetime import datetime, timedelta, timezone

import pytest

from genesis_commander.contract import ShadowEvaluation
from genesis_commander.shadow_evaluation import DATA_NOT_AVAILABLE, PricePoint, evaluate_shadow_decision


UTC = timezone.utc
T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def decision(kind="CANDIDATE_LONG"):
    return {
        "decision_id": "decision-1",
        "timestamp": T0.isoformat(),
        "decision": kind,
        "symbol": "BTCUSDT",
    }


def points(*prices, start=T0, step=timedelta(minutes=15)):
    return [PricePoint(start + step * index, price) for index, price in enumerate(prices)]


def test_candidate_long_correct():
    result = evaluate_shadow_decision(decision(), points(100, 105), "1h")
    assert result.direction_correct is True
    assert result.return_pct == 5.0


def test_candidate_long_incorrect():
    result = evaluate_shadow_decision(decision(), points(100, 95), "1h")
    assert result.direction_correct is False


def test_candidate_short_correct():
    result = evaluate_shadow_decision(decision("CANDIDATE_SHORT"), points(100, 95), "1h")
    assert result.direction_correct is True
    assert result.return_pct == -5.0


def test_candidate_short_incorrect():
    result = evaluate_shadow_decision(decision("CANDIDATE_SHORT"), points(100, 105), "1h")
    assert result.direction_correct is False


@pytest.mark.parametrize("kind", ["NO_ACTION", "WATCH", "INSUFFICIENT_DATA"])
def test_non_directional_decisions_are_not_scored(kind):
    result = evaluate_shadow_decision(decision(kind), points(100, 105), "1h")
    assert result.direction_correct is None
    assert result.reference_price is None
    assert result.to_dict()["direction_correct"] == DATA_NOT_AVAILABLE


def test_missing_price_window_is_explicit():
    result = evaluate_shadow_decision(decision(), [], "1h")
    payload = result.to_dict()
    assert payload["reference_price"] == DATA_NOT_AVAILABLE
    assert payload["outcome_price"] == DATA_NOT_AVAILABLE


def test_incomplete_window_is_explicit():
    result = evaluate_shadow_decision(decision(), points(100, 105), "1h")
    assert result.notes == "Evaluation window incomplete; latest permitted observation used"
    assert result.outcome_timestamp == T0 + timedelta(minutes=15)


def test_cutoff_excludes_later_price():
    result = evaluate_shadow_decision(decision(), points(100, 105, 200, start=T0, step=timedelta(hours=1)), "1h")
    assert result.outcome_price == 105.0
    assert result.outcome_timestamp == T0 + timedelta(hours=1)


def test_future_leakage_zero_for_earlier_and_later_points():
    result = evaluate_shadow_decision(
        decision(),
        [PricePoint(T0 - timedelta(minutes=1), 1), PricePoint(T0, 100), PricePoint(T0 + timedelta(hours=1), 105), PricePoint(T0 + timedelta(hours=2), 999)],
        "1h",
    )
    assert result.outcome_price == 105.0
    assert result.max_favorable_excursion == 5.0


def test_mfe_and_mae_long():
    result = evaluate_shadow_decision(decision(), points(100, 110, 95, 105), "1h")
    assert result.max_favorable_excursion == 10.0
    assert result.max_adverse_excursion == -5.0


def test_mfe_and_mae_short():
    result = evaluate_shadow_decision(decision("CANDIDATE_SHORT"), points(100, 90, 105, 95), "1h")
    assert result.max_favorable_excursion == 10.0
    assert result.max_adverse_excursion == -5.0


def test_repeatability():
    window = points(100, 110, 95, 105)
    first = evaluate_shadow_decision(decision(), window, "1h").to_dict()
    second = evaluate_shadow_decision(decision(), window, "1h").to_dict()
    assert first == second


def test_utc_is_required_for_decision_and_prices():
    with pytest.raises(ValueError, match="UTC"):
        evaluate_shadow_decision({**decision(), "timestamp": T0.replace(tzinfo=None)}, points(100), "1h")
    with pytest.raises(ValueError, match="UTC"):
        evaluate_shadow_decision(decision(), [PricePoint(T0.replace(tzinfo=None), 100)], "1h")


def test_invalid_horizon_is_rejected():
    with pytest.raises(ValueError):
        evaluate_shadow_decision(decision(), points(100), "0h")


def test_mapping_and_dataclass_inputs_are_supported():
    dataclass_decision = type("Decision", (), {
        "commander_decision_id": "decision-1",
        "timestamp_utc": T0,
        "decision": type("Enum", (), {"value": "CANDIDATE_LONG"})(),
        "symbol": "BTCUSDT",
    })()
    result = evaluate_shadow_decision(dataclass_decision, [{"timestamp": T0.isoformat(), "price": 100}], "1h")
    assert isinstance(result, ShadowEvaluation)


def test_evaluator_does_not_mutate_price_input():
    window = [{"timestamp": T0.isoformat(), "price": 100}, {"timestamp": (T0 + timedelta(minutes=1)).isoformat(), "price": 101}]
    original = list(window)
    evaluate_shadow_decision(decision(), window, "1h")
    assert window == original


def test_serialization_uses_exact_missing_marker_and_mfe_aliases():
    payload = evaluate_shadow_decision(decision(), [], "1h").to_dict()
    assert payload["MFE"] == DATA_NOT_AVAILABLE
    assert payload["MAE"] == DATA_NOT_AVAILABLE

