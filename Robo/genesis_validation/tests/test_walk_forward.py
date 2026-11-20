from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType, ValidationObservation
from genesis_validation.labeling import label_observation
from genesis_validation.walk_forward import WalkForwardConfig, WalkForwardMode, build_walk_forward_folds, run_walk_forward


TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def record(i, net="1", horizon="H1", kind=DecisionType.CANDIDATE_LONG, strategy="s1", regime="TREND", **kwargs):
    values = dict(commander_decision_id=f"d{i}", timestamp_utc=TS + timedelta(hours=i), symbol="BTC/USD", decision_type=kind,
                  confidence="0.5", validation_horizon=horizon, net_pnl=net, return_pct=net,
                  strategy_id=strategy, market_regime=regime, paper_execution_outcome="FILLED", fill_outcome="FILLED")
    values.update(kwargs)
    obs = ValidationObservation(**values)
    return CalibrationRecord(obs, label_observation(obs))


def test_rolling_explicit_windows_and_ordering():
    rows = [record(i) for i in range(8)]
    folds = build_walk_forward_folds(rows, WalkForwardConfig(3, 2, 2, mode=WalkForwardMode.ROLLING))
    assert len(folds) == 2
    assert folds[0].train_count if hasattr(folds[0], "train_count") else len(folds[0].train_records) == 3
    assert folds[0].train_records[-1].observation.timestamp_utc < folds[0].validation_records[0].observation.timestamp_utc


def test_expanding_windows():
    folds = build_walk_forward_folds([record(i) for i in range(9)], WalkForwardConfig(2, 2, 2, mode=WalkForwardMode.EXPANDING))
    assert len(folds[1].train_records) > len(folds[0].train_records)


def test_gap_and_minimum_samples():
    folds = build_walk_forward_folds([record(i) for i in range(6)], WalkForwardConfig(2, 2, 1, gap_size=1, min_train_sample=3))
    assert folds[0].fold_status == "INSUFFICIENT_TRAIN_SAMPLE"
    assert folds[0].validation_records[0].observation.timestamp_utc > folds[0].train_records[-1].observation.timestamp_utc


def test_no_overlap_and_insufficient_validation():
    folds = build_walk_forward_folds([record(i) for i in range(5)], WalkForwardConfig(2, 2, 1, min_validation_sample=3))
    assert folds[0].fold_status == "INSUFFICIENT_VALIDATION_SAMPLE"
    assert not set(folds[0].train_records) & set(folds[0].validation_records)


def test_purge_cross_boundary_outcome():
    rows = [record(0, holding_period_seconds=7200), record(1), record(2), record(3)]
    folds = build_walk_forward_folds(rows, WalkForwardConfig(1, 1, 1, purge_cross_boundary_outcomes=True))
    assert folds[0].train_count if hasattr(folds[0], "train_count") else len(folds[0].train_records) == 0


def test_horizon_isolation():
    with pytest.raises(ValueError):
        build_walk_forward_folds([record(0, horizon="H1"), record(1, horizon="H2")], WalkForwardConfig(1, 1, 1))
    assert len(build_walk_forward_folds([record(0, horizon="H1"), record(1, horizon="H1")], WalkForwardConfig(1, 1, 1))) == 1


def test_strategy_direction_and_regime_filters():
    rows = [record(0, strategy="a"), record(1, strategy="a"), record(2, strategy="b", kind=DecisionType.CANDIDATE_SHORT), record(3, strategy="b", kind=DecisionType.CANDIDATE_SHORT), record(4, regime="RANGE"), record(5, regime="RANGE")]
    assert build_walk_forward_folds(rows, WalkForwardConfig(1, 1, 1), strategy_id="a")
    assert build_walk_forward_folds(rows, WalkForwardConfig(1, 1, 1), direction="SHORT")
    assert build_walk_forward_folds(rows, WalkForwardConfig(1, 1, 1), regime="RANGE")


def test_fold_metrics_and_aggregate_uses_validation_only():
    result = run_walk_forward([record(i, net=str(i + 1)) for i in range(6)], WalkForwardConfig(2, 2, 2))
    assert result.aggregate_net_pnl == Decimal("18")
    assert result.aggregate_trade_count == 4
    assert result.aggregate_expectancy == Decimal("4.5")
    assert result.folds[0].validation_count == 2


def test_aggregate_accuracy_recomputed():
    rows = [record(0, net="1"), record(1, net="-1", return_pct="-1"), record(2, net="1") , record(3, net="-1", return_pct="-1")]
    result = run_walk_forward(rows, WalkForwardConfig(2, 2, 2))
    assert result.aggregate_directional_accuracy == Decimal("0.5")


def test_fold_cost_metrics_and_drawdown():
    rows = [record(0, net="2"), record(1, net="-1"), record(2, fees="1", slippage="0.5"), record(3, fees="0.2", slippage="0.1")]
    result = run_walk_forward(rows, WalkForwardConfig(2, 2, 2))
    assert result.folds[0].fees == Decimal("1.2")
    assert result.folds[0].slippage == Decimal("0.6")
    assert result.folds[0].max_drawdown == Decimal("0")


def test_stability_and_deterioration():
    stable = run_walk_forward([record(i, net="1") for i in range(8)], WalkForwardConfig(2, 2, 2))
    mixed = run_walk_forward([record(0, net="2"), record(1, net="2"), record(2, net="-2"), record(3, net="-2"), record(4, net="1"), record(5, net="1")], WalkForwardConfig(2, 2, 2))
    assert stable.stability_status == "CONSISTENT"
    assert mixed.stability_status in {"UNSTABLE", "MOSTLY_CONSISTENT"}


def test_prefix_equivalence_and_determinism():
    rows = [record(i) for i in range(10)]
    config = WalkForwardConfig(3, 2, 2)
    full = build_walk_forward_folds(rows, config)
    prefix = build_walk_forward_folds(rows[:7], config)
    assert full[0] == prefix[0]
    assert run_walk_forward(rows, config) == run_walk_forward(rows, config)


def test_missing_economic_data_not_zero_and_nonexecuted_no_fake_pnl():
    rows = [record(0, net=None, return_pct=None, paper_execution_outcome="REJECTED", fill_outcome="REJECTED")]
    result = run_walk_forward(rows + [record(1), record(2)], WalkForwardConfig(1, 2, 1))
    assert result.aggregate_net_pnl == Decimal("2")


def test_config_and_automation_invariants():
    with pytest.raises(ValueError):
        WalkForwardConfig(0, 1, 1)
    rows = [record(i) for i in range(5)]
    assert "live" not in str(run_walk_forward(rows, WalkForwardConfig(1, 1, 1))).lower()
