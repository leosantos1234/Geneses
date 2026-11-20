from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.calibration import CalibrationRecord
from genesis_validation.contracts import DecisionType
from genesis_validation.labeling import label_observation
from genesis_validation.oos import OOSConfig, assess_generalization, split_oos, summarize_oos
from genesis_validation.tests.test_walk_forward import record


def test_valid_terminal_fraction_split():
    rows = [record(i) for i in range(10)]
    split = split_oos(rows, OOSConfig("FRACTION", terminal_fraction=Decimal("0.2"), validation_count=2))
    assert len(split.oos_records) == 2
    assert split.train_records[-1].observation.timestamp_utc < split.validation_records[0].observation.timestamp_utc
    assert split.validation_records[-1].observation.timestamp_utc < split.oos_records[0].observation.timestamp_utc


def test_explicit_count_and_range_split():
    rows = [record(i) for i in range(10)]
    count_split = split_oos(rows, OOSConfig("COUNT", terminal_count=3, validation_count=2))
    range_split = split_oos(rows, OOSConfig("RANGE", oos_start=rows[7].observation.timestamp_utc, oos_end=rows[9].observation.timestamp_utc, validation_count=2))
    assert len(count_split.oos_records) == 3
    assert len(range_split.oos_records) == 3


def test_terminal_and_no_overlap():
    rows = [record(i) for i in range(8)]
    split = split_oos(rows, OOSConfig("COUNT", terminal_count=2, validation_count=2))
    assert not set(split.train_records) & set(split.oos_records)
    assert not set(split.validation_records) & set(split.oos_records)
    assert split.oos_records[-1].observation.timestamp_utc == rows[-1].observation.timestamp_utc


def test_no_implicit_split_and_immutable_membership():
    with pytest.raises(ValueError):
        OOSConfig("COUNT", terminal_count=None)
    rows = [record(i) for i in range(6)]
    first = split_oos(rows, OOSConfig("COUNT", terminal_count=2))
    second = split_oos(rows, OOSConfig("COUNT", terminal_count=2))
    assert first == second


def test_horizon_isolation():
    with pytest.raises(ValueError):
        split_oos([record(0, horizon="H1"), record(1, horizon="H2")], OOSConfig("COUNT", terminal_count=1))


def test_oos_metrics_and_costs():
    rows = [record(i, net=str(i + 1), fees="1", slippage="0.5") for i in range(5)]
    split = split_oos(rows, OOSConfig("COUNT", terminal_count=2))
    summary = summarize_oos(split.oos_records)
    assert summary.net_pnl == Decimal("9")
    assert summary.expectancy == Decimal("4.5")
    assert summary.fees_paid == Decimal("2")
    assert summary.slippage_cost == Decimal("1.0")
    assert summary.profit_factor == Decimal("Infinity")


def test_oos_direction_and_trade_metrics():
    rows = [record(0, net="1"), record(1, net="-1", return_pct="-1"), record(2, net="0", return_pct="0")]
    summary = summarize_oos(rows)
    assert summary.directional_accuracy == Decimal("0.5")
    assert summary.winning_trade_count == 1
    assert summary.losing_trade_count == 1
    assert summary.breakeven_trade_count == 1


def test_oos_missing_and_nonexecuted_not_zero():
    rows = [record(0, net=None, return_pct=None, paper_execution_outcome="REJECTED", fill_outcome="REJECTED"), record(1, net="2")]
    summary = summarize_oos(rows)
    assert summary.net_pnl == Decimal("2")
    assert summary.sample_count == 2


def test_generalizes_statuses():
    positive = summarize_oos([record(0, net="2")])
    negative = summarize_oos([record(0, net="-2", return_pct="-2")])
    assert assess_generalization(positive, reference_net_pnl=Decimal("1"), reference_expectancy=Decimal("1"), reference_directional_accuracy=Decimal("1")).generalization_status == "GENERALIZES"
    assert assess_generalization(negative, reference_net_pnl=Decimal("1"), reference_expectancy=Decimal("1"), reference_directional_accuracy=Decimal("1")).generalization_status == "FAILS_TO_GENERALIZE"


def test_partial_and_degraded_statuses():
    oos = summarize_oos([record(0, net="1")])
    result = assess_generalization(oos, reference_net_pnl=Decimal("3"), reference_expectancy=Decimal("3"), reference_directional_accuracy=Decimal("1"))
    assert result.generalization_status == "PARTIALLY_GENERALIZES"
    assert result.degradation_status == "DEGRADED"


def test_insufficient_data_status():
    summary = summarize_oos([])
    result = assess_generalization(summary, reference_net_pnl=Decimal("1"), reference_expectancy=Decimal("1"), reference_directional_accuracy=Decimal("1"))
    assert result.generalization_status == "INSUFFICIENT_DATA"


def test_oos_determinism_and_source_immutability():
    rows = [record(i) for i in range(6)]
    config = OOSConfig("COUNT", terminal_count=2)
    a = summarize_oos(split_oos(rows, config).oos_records)
    b = summarize_oos(split_oos(rows, config).oos_records)
    assert a == b
    assert rows[0].observation.confidence == Decimal("0.5")


def test_no_automatic_action_or_live_path():
    summary = summarize_oos([record(0)])
    assert "live" not in str(summary).lower()
