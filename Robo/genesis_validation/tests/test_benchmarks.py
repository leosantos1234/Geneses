from decimal import Decimal
import sys

sys.path.insert(0, "Robo")

from genesis_validation.benchmarks import BenchmarkDefinition, compare_benchmarks
from genesis_validation.tests.test_walk_forward import record


def test_cash_baseline_and_commander_outperformance():
    result = compare_benchmarks([record(0, net="2"), record(1, net="1")], [BenchmarkDefinition("cash", "CASH")])[0]
    assert result.benchmark.net_pnl == Decimal("0")
    assert result.net_pnl_delta == Decimal("3")
    assert result.advantage_status == "COMMANDER_OUTPERFORMS"


def test_always_long_short_are_explicit():
    rows = [record(0, net="2", return_pct="1"), record(1, net="-1", return_pct="-1")]
    results = compare_benchmarks(rows, [BenchmarkDefinition("long", "ALWAYS_LONG"), BenchmarkDefinition("short", "ALWAYS_SHORT")])
    assert [item.benchmark.benchmark_id for item in results] == ["long", "short"]
    assert results[0].benchmark.net_pnl == Decimal("0")


def test_buy_hold_available_and_unavailable():
    rows = [record(0, net="1")]
    available = compare_benchmarks(rows, [BenchmarkDefinition("bh", "BUY_AND_HOLD")], supplied_values={"bh": [(Decimal("2"), Decimal("2"), Decimal("0"), Decimal("0"))]})[0]
    unavailable = compare_benchmarks(rows, [BenchmarkDefinition("bh", "BUY_AND_HOLD")])[0]
    assert available.benchmark.net_pnl == Decimal("2")
    assert unavailable.advantage_status == "INSUFFICIENT_DATA"


def test_seeded_random_is_deterministic():
    rows = [record(i, net="1", return_pct="1") for i in range(10)]
    definition = BenchmarkDefinition("random", "SEEDED_RANDOM", seed=7, direction_probability=Decimal("0.5"))
    first = compare_benchmarks(rows, [definition])
    second = compare_benchmarks(rows, [definition])
    assert first == second


def test_no_post_hoc_order_and_deltas():
    rows = [record(0, net="2", return_pct="2")]
    results = compare_benchmarks(rows, [BenchmarkDefinition("cash", "CASH"), BenchmarkDefinition("long", "ALWAYS_LONG")])
    assert [item.benchmark.benchmark_id for item in results] == ["cash", "long"]
    assert results[0].return_delta == Decimal("2")
    assert "best" not in str(results).lower()


def test_directional_accuracy_and_cost_fairness():
    rows = [record(0, net="1", return_pct="1", fees="0.1", slippage="0.1"), record(1, net="-1", return_pct="1", fees="0.1", slippage="0.1")]
    result = compare_benchmarks(rows, [BenchmarkDefinition("cash", "CASH")])[0]
    assert result.commander.directional_accuracy == Decimal("1")
    assert result.commander.fees == Decimal("0.2")
    assert result.commander.slippage == Decimal("0.2")


def test_same_window_and_horizon_inputs_are_preserved():
    rows = [record(0, horizon="H1"), record(1, horizon="H1")]
    result = compare_benchmarks(rows, [BenchmarkDefinition("cash", "CASH")])[0]
    assert result.commander.sample_count == 2


def test_missing_data_is_not_zero_for_non_cash():
    result = compare_benchmarks([record(0, net=None, return_pct=None)], [BenchmarkDefinition("long", "ALWAYS_LONG")])[0]
    assert result.benchmark.net_pnl is None


def test_source_immutability_and_no_live_path():
    row = record(0)
    compare_benchmarks([row], [BenchmarkDefinition("cash", "CASH")])
    assert row.observation.confidence == Decimal("0.5")
    assert "live" not in str(compare_benchmarks([row], [BenchmarkDefinition("cash", "CASH")])).lower()
