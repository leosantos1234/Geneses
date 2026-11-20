import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.contracts import DATA_NOT_AVAILABLE, DecisionType, ValidationObservation
from genesis_validation.labeling import DirectionLabel, ExecutionLabel, label_dataset, label_observation, summarize_labels


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def obs(kind=DecisionType.CANDIDATE_LONG, ret=None, **kwargs):
    values = dict(commander_decision_id="d", timestamp_utc=TS, symbol="BTC/USD", decision_type=kind, return_pct=ret)
    values.update(kwargs)
    return ValidationObservation(**values)


def test_long_positive_correct():
    assert label_observation(obs(ret="1")).direction_label is DirectionLabel.CORRECT_DIRECTION


def test_long_negative_wrong():
    assert label_observation(obs(ret="-1")).direction_label is DirectionLabel.WRONG_DIRECTION


def test_long_zero_neutral():
    assert label_observation(obs(ret="0")).direction_label is DirectionLabel.NEUTRAL


def test_short_negative_correct():
    assert label_observation(obs(DecisionType.CANDIDATE_SHORT, "-1")).direction_label is DirectionLabel.CORRECT_DIRECTION


def test_short_positive_wrong():
    assert label_observation(obs(DecisionType.CANDIDATE_SHORT, "1")).direction_label is DirectionLabel.WRONG_DIRECTION


def test_short_zero_neutral():
    assert label_observation(obs(DecisionType.CANDIDATE_SHORT, "0")).direction_label is DirectionLabel.NEUTRAL


def test_non_candidates_not_applicable():
    assert label_observation(obs(DecisionType.NO_ACTION, "10")).direction_label is DirectionLabel.NOT_APPLICABLE
    assert label_observation(obs(DecisionType.WATCH, "-10")).direction_label is DirectionLabel.NOT_APPLICABLE


def test_insufficient_data_is_not_available():
    result = label_observation(obs(DecisionType.INSUFFICIENT_DATA))
    assert result.direction_label is DirectionLabel.DATA_NOT_AVAILABLE
    assert result.execution_label is ExecutionLabel.NOT_APPLICABLE


def test_missing_candidate_outcome_is_not_available():
    result = label_observation(obs())
    assert result.direction_label is DirectionLabel.DATA_NOT_AVAILABLE
    assert result.execution_label is ExecutionLabel.DATA_NOT_AVAILABLE


def test_risk_rejected_with_direction_outcome_keeps_both_axes():
    result = label_observation(obs(ret="2", risk_outcome="REJECT", paper_execution_outcome="REJECTED", fill_outcome="REJECTED"))
    assert result.direction_label is DirectionLabel.CORRECT_DIRECTION
    assert result.execution_label is ExecutionLabel.NOT_EXECUTED


def test_risk_rejected_without_outcome():
    result = label_observation(obs(risk_outcome="REJECT", paper_execution_outcome="REJECTED", fill_outcome="REJECTED"))
    assert result.direction_label is DirectionLabel.DATA_NOT_AVAILABLE
    assert result.execution_label is ExecutionLabel.NOT_EXECUTED


@pytest.mark.parametrize("status", ["PENDING", "EXPIRED", "CANCELLED"])
def test_pending_expired_cancelled_are_not_executed(status):
    result = label_observation(obs(ret="1", paper_execution_outcome=status, fill_outcome=status))
    assert result.direction_label is DirectionLabel.CORRECT_DIRECTION
    assert result.execution_label is ExecutionLabel.NOT_EXECUTED


def test_filled_is_executed():
    result = label_observation(obs(ret="1", paper_execution_outcome="FILLED", fill_outcome="FILLED"))
    assert result.execution_label is ExecutionLabel.EXECUTED


def test_horizons_are_independent():
    rows = [obs(ret="1", validation_horizon="H1", commander_decision_id="d"), obs(ret="0", validation_horizon="H2", commander_decision_id="d"), obs(ret="-1", validation_horizon="H3", commander_decision_id="d")]
    results = label_dataset(rows)
    assert [item.direction_label for item in results] == [DirectionLabel.CORRECT_DIRECTION, DirectionLabel.NEUTRAL, DirectionLabel.WRONG_DIRECTION]


def test_missing_data_never_becomes_directional_label():
    result = label_observation(obs(ret=None))
    assert result.direction_label not in {DirectionLabel.CORRECT_DIRECTION, DirectionLabel.WRONG_DIRECTION, DirectionLabel.NEUTRAL}


def test_exact_zero_has_no_implicit_epsilon():
    result = label_observation(obs(ret=Decimal("0")))
    assert result.direction_label is DirectionLabel.NEUTRAL


def test_confidence_and_strategy_are_unchanged():
    source = obs(ret="1", confidence="0.8", strategy_id="s1", strategy_name="alpha")
    label_observation(source)
    assert source.confidence == Decimal("0.8")
    assert source.strategy_id == "s1"


def test_input_observation_is_immutable():
    source = obs(ret="1")
    with pytest.raises(AttributeError):
        source.return_pct = Decimal("99")


def test_deterministic_repeatability():
    source = obs(ret="1", paper_execution_outcome="FILLED", fill_outcome="FILLED")
    assert label_observation(source).to_dict() == label_observation(source).to_dict()


def test_batch_is_stable_and_complete():
    rows = [obs(ret="1", commander_decision_id="b"), obs(ret="-1", commander_decision_id="a")]
    results = label_dataset(rows)
    assert [item.commander_decision_id for item in results] == ["b", "a"]
    assert len(results) == len(rows)


def test_batch_duplicate_outputs_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        label_dataset([obs(ret="1"), obs(ret="-1")])


def test_paper_only_invariant_and_summary():
    results = label_dataset([obs(ret="1"), obs(DecisionType.NO_ACTION, "1", commander_decision_id="n")])
    assert all(item.paper_only for item in results)
    summary = summarize_labels(results)
    assert summary["correct_direction_count"] == 1
    assert summary["not_applicable_count"] == 1


def test_no_live_execution_path_fields():
    result = label_observation(obs(ret="1"))
    assert "live" not in str(result.to_dict()).lower()
