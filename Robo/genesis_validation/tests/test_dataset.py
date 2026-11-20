import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.contracts import DATA_NOT_AVAILABLE, DecisionType
from genesis_validation.dataset import DatasetConfig, NOT_APPLICABLE, PaperOutcome, build_evaluation_dataset


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
CONFIG = DatasetConfig({"H1": timedelta(minutes=5), "H2": timedelta(minutes=30), "H3": timedelta(hours=2)})


def decision(decision_type=DecisionType.CANDIDATE_LONG, **kwargs):
    values = {
        "commander_decision_id": "d-1", "timestamp_utc": TS, "symbol": "BTC/USD",
        "decision": decision_type, "confidence": "0.8", "reference_price": "100",
    }
    values.update(kwargs)
    return values


def outcome(decision_id="d-1", **kwargs):
    values = {"commander_decision_id": decision_id, "paper_execution_outcome": "FILLED",
              "paper_risk_outcome": "ALLOW", "paper_order_status": "FILLED",
              "paper_fill_status": "FILLED", "paper_order_id": "o-1", "paper_fill_id": "f-1",
              "paper_effect_id": "e-1", "fill_price": "101", "fees_paid": "1",
              "slippage_cost": "0.5", "net_pnl": "8.5", "return_pct": "8.5",
              "opened_at": TS, "closed_at": TS + timedelta(minutes=4),
              "outcome_timestamp_utc": TS + timedelta(minutes=4)}
    values.update(kwargs)
    return PaperOutcome(**values)


def test_complete_candidate_long_row():
    dataset = build_evaluation_dataset([decision(strategy_id="s1", strategy_name="alpha")], {"d-1": outcome()}, CONFIG)
    row = dataset.observations[0]
    assert len(dataset.observations) == 3
    assert row.strategy_id == "s1"
    assert row.paper_fill_id == "f-1"
    assert row.net_pnl == Decimal("8.5")


def test_candidate_short_and_non_candidates_are_retained():
    decisions = [
        decision("CANDIDATE_SHORT", commander_decision_id="d-short"),
        decision(DecisionType.NO_ACTION, commander_decision_id="d-none", confidence=None),
        decision(DecisionType.WATCH, commander_decision_id="d-watch"),
        decision(DecisionType.INSUFFICIENT_DATA, commander_decision_id="d-insufficient", confidence=None),
    ]
    dataset = build_evaluation_dataset(decisions, {"d-short": outcome("d-short")}, DatasetConfig({"H1": timedelta(minutes=5)}))
    assert dataset.quality["candidate_short_count"] == 1
    assert dataset.quality["no_action_count"] == 1
    assert dataset.quality["watch_count"] == 1
    assert dataset.quality["insufficient_data_count"] == 1


def test_missing_paper_outcome_uses_missing_and_not_applicable_semantics():
    row = build_evaluation_dataset([decision(DecisionType.NO_ACTION, confidence=None)], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.paper_execution_outcome == DATA_NOT_AVAILABLE
    assert row.fill_price is None
    assert row.fill_outcome == NOT_APPLICABLE
    assert row.to_dict()["fill_price"] == DATA_NOT_AVAILABLE


def test_missing_reference_strategy_and_regime_are_not_fabricated():
    row = build_evaluation_dataset([decision(reference_price=None, strategy_id=None, strategy_name=None)], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    payload = row.to_dict()
    assert payload["reference_price"] == DATA_NOT_AVAILABLE
    assert payload["strategy_id"] == DATA_NOT_AVAILABLE
    assert payload["market_regime"] == DATA_NOT_AVAILABLE


def test_original_confidence_is_preserved_without_rescaling():
    row = build_evaluation_dataset([decision(confidence="0.37")], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.confidence == Decimal("0.37")


def test_categorical_confidence_is_preserved_raw():
    row = build_evaluation_dataset([decision(confidence="HIGH")], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.confidence is None
    assert row.confidence_raw == "HIGH"


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValueError, match="between 0 and 1"):
        build_evaluation_dataset([decision(confidence="1.1")], {}, DatasetConfig({"H1": timedelta(minutes=5)}))


def test_utc_and_naive_timestamp_policy():
    assert build_evaluation_dataset([decision()], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0].timestamp_utc.tzinfo is timezone.utc
    with pytest.raises(ValueError, match="timezone-aware"):
        build_evaluation_dataset([decision(timestamp_utc=datetime(2026, 1, 1))], {}, DatasetConfig({"H1": timedelta(minutes=5)}))


def test_stable_chronological_sort():
    later = decision(commander_decision_id="d-2", timestamp_utc=TS + timedelta(minutes=1))
    earlier = decision(commander_decision_id="d-1", timestamp_utc=TS)
    dataset = build_evaluation_dataset([later, earlier], {}, DatasetConfig({"H1": timedelta(minutes=5)}))
    assert [item.commander_decision_id for item in dataset.observations] == ["d-1", "d-2"]


def test_duplicate_identity_is_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        build_evaluation_dataset([decision(), decision()], {}, DatasetConfig({"H1": timedelta(minutes=5)}))


def test_conflicting_lineage_is_rejected():
    with pytest.raises(ValueError, match="lineage"):
        build_evaluation_dataset([decision()], {"d-1": outcome("other")}, DatasetConfig({"H1": timedelta(minutes=5)}))


def test_horizon_cutoff_is_enforced():
    with pytest.raises(ValueError, match="exceeds configured horizon"):
        build_evaluation_dataset([decision()], {"d-1": outcome(outcome_timestamp_utc=TS + timedelta(minutes=6))}, DatasetConfig({"H1": timedelta(minutes=5)}))


def test_mfe_mae_and_holding_period_are_source_outcomes():
    row = build_evaluation_dataset([decision()], {"d-1": outcome(mfe="9", mae="-2")}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.mfe == Decimal("9")
    assert row.mae == Decimal("-2")
    assert row.holding_period_seconds == Decimal("240.0")


def test_open_trade_holding_period_is_not_fabricated():
    row = build_evaluation_dataset([decision()], {"d-1": outcome(closed_at=None, outcome_timestamp_utc=None)}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.holding_period_seconds is None


def test_source_inputs_are_not_mutated_and_serialization_repeats():
    source = decision()
    dataset = build_evaluation_dataset([source], {"d-1": outcome()}, DatasetConfig({"H1": timedelta(minutes=5)}))
    assert source["confidence"] == "0.8"
    assert dataset.to_json() == dataset.to_json()


def test_paper_only_invariant_and_no_live_path():
    row = build_evaluation_dataset([decision()], {}, DatasetConfig({"H1": timedelta(minutes=5)})).observations[0]
    assert row.paper_only is True
    assert "live" not in row.to_json().lower()


def test_quality_counts_missing_and_complete_outcomes():
    dataset = build_evaluation_dataset([decision(), decision(commander_decision_id="d-2")], {"d-1": outcome()}, DatasetConfig({"H1": timedelta(minutes=5)}))
    assert dataset.quality["total_decisions"] == 2
    assert dataset.quality["observations_with_complete_paper_outcome"] == 1
    assert dataset.quality["observations_with_missing_paper_outcome"] == 1
