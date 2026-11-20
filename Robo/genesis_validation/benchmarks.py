"""Deterministic Commander-versus-baseline analytical comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random
from statistics import median
from typing import Iterable, Mapping

from .calibration import CalibrationRecord
from .oos import OOSPerformanceSummary, summarize_oos


@dataclass(frozen=True)
class BenchmarkDefinition:
    benchmark_id: str
    benchmark_type: str
    seed: int | None = None
    direction_probability: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.benchmark_id or self.benchmark_type not in {"CASH", "ALWAYS_LONG", "ALWAYS_SHORT", "BUY_AND_HOLD", "SEEDED_RANDOM", "EXISTING_NON_COMMANDER"}:
            raise ValueError("invalid benchmark definition")
        if self.benchmark_type == "SEEDED_RANDOM" and self.seed is None:
            raise ValueError("seed required for random benchmark")
        if self.direction_probability is not None and not Decimal("0") <= self.direction_probability <= Decimal("1"):
            raise ValueError("direction_probability must be between 0 and 1")


@dataclass(frozen=True)
class BenchmarkPerformanceSummary:
    benchmark_id: str
    benchmark_type: str
    sample_count: int
    net_pnl: Decimal | None
    total_return_pct: Decimal | None
    expectancy: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    win_rate: Decimal | None
    directional_accuracy: Decimal | None
    max_drawdown: Decimal | None
    fees: Decimal | None
    slippage: Decimal | None


@dataclass(frozen=True)
class BenchmarkComparisonResult:
    benchmark: BenchmarkPerformanceSummary
    commander: BenchmarkPerformanceSummary
    net_pnl_delta: Decimal | None
    expectancy_delta: Decimal | None
    return_delta: Decimal | None
    drawdown_delta: Decimal | None
    directional_accuracy_delta: Decimal | None
    advantage_status: str


def _summary(definition: BenchmarkDefinition, values: list[tuple[Decimal, Decimal, Decimal, Decimal]]) -> BenchmarkPerformanceSummary:
    if not values:
        return BenchmarkPerformanceSummary(definition.benchmark_id, definition.benchmark_type, 0, None, None, None, None, None, None, None, None, None, None)
    pnl = [item[0] for item in values]
    returns = [item[1] for item in values]
    fees = sum((item[2] for item in values), Decimal("0"))
    slippage = sum((item[3] for item in values), Decimal("0"))
    wins = sum(value > 0 for value in pnl)
    equity = peak = max_dd = Decimal("0")
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return BenchmarkPerformanceSummary(definition.benchmark_id, definition.benchmark_type, len(values), sum(pnl, Decimal("0")), sum(returns, Decimal("0")), sum(pnl, Decimal("0")) / Decimal(len(pnl)), sum(returns, Decimal("0")) / Decimal(len(returns)), Decimal(str(median(returns))), Decimal(wins) / Decimal(len(pnl)), None, max_dd, fees, slippage)


def _baseline_values(definition: BenchmarkDefinition, records: tuple[CalibrationRecord, ...], supplied: Iterable[tuple[Decimal, Decimal, Decimal, Decimal]] | None) -> list[tuple[Decimal, Decimal, Decimal, Decimal]]:
    if definition.benchmark_type == "CASH":
        return [(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")) for _ in records]
    if definition.benchmark_type in {"BUY_AND_HOLD", "EXISTING_NON_COMMANDER"}:
        return list(supplied or ())
    if supplied is not None:
        return list(supplied)
    if definition.benchmark_type in {"ALWAYS_LONG", "ALWAYS_SHORT"}:
        sign = Decimal("1") if definition.benchmark_type == "ALWAYS_LONG" else Decimal("-1")
        return [(record.observation.return_pct * sign, record.observation.return_pct * sign, record.observation.fees or Decimal("0"), record.observation.slippage or Decimal("0")) for record in records if record.observation.return_pct is not None]
    if definition.benchmark_type == "SEEDED_RANDOM":
        rng = random.Random(definition.seed)
        probability = definition.direction_probability if definition.direction_probability is not None else Decimal("0.5")
        return [(record.observation.return_pct * (Decimal("1") if rng.random() < float(probability) else Decimal("-1")), record.observation.return_pct, record.observation.fees or Decimal("0"), record.observation.slippage or Decimal("0")) for record in records if record.observation.return_pct is not None]
    return []


def compare_benchmarks(records: Iterable[CalibrationRecord], definitions: Iterable[BenchmarkDefinition], *, supplied_values: Mapping[str, Iterable[tuple[Decimal, Decimal, Decimal, Decimal]]] | None = None) -> tuple[BenchmarkComparisonResult, ...]:
    rows = tuple(records)
    commander_values = [(row.observation.net_pnl, row.observation.return_pct, row.observation.fees or Decimal("0"), row.observation.slippage or Decimal("0")) for row in rows if row.observation.net_pnl is not None and row.observation.return_pct is not None]
    commander_definition = BenchmarkDefinition("COMMANDER", "EXISTING_NON_COMMANDER")
    commander = _summary(commander_definition, commander_values)
    correct = sum(row.label.direction_label.value == "CORRECT_DIRECTION" for row in rows)
    wrong = sum(row.label.direction_label.value == "WRONG_DIRECTION" for row in rows)
    commander = BenchmarkPerformanceSummary(commander.benchmark_id, commander.benchmark_type, commander.sample_count, commander.net_pnl, commander.total_return_pct, commander.expectancy, commander.average_return_pct, commander.median_return_pct, commander.win_rate, Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None, commander.max_drawdown, commander.fees, commander.slippage)
    results = []
    for definition in definitions:
        values = _baseline_values(definition, rows, None if supplied_values is None else supplied_values.get(definition.benchmark_id))
        benchmark = _summary(definition, values)
        if benchmark.net_pnl is None or commander.net_pnl is None:
            status = "INSUFFICIENT_DATA"
        elif commander.net_pnl > benchmark.net_pnl and (commander.expectancy or 0) > (benchmark.expectancy or 0):
            status = "COMMANDER_OUTPERFORMS"
        elif commander.net_pnl < benchmark.net_pnl and (commander.expectancy or 0) < (benchmark.expectancy or 0):
            status = "BASELINE_OUTPERFORMS"
        elif commander.net_pnl == benchmark.net_pnl and commander.expectancy == benchmark.expectancy:
            status = "ROUGHLY_EQUIVALENT"
        else:
            status = "MIXED"
        results.append(BenchmarkComparisonResult(benchmark, commander, commander.net_pnl - benchmark.net_pnl if benchmark.net_pnl is not None and commander.net_pnl is not None else None, commander.expectancy - benchmark.expectancy if benchmark.expectancy is not None and commander.expectancy is not None else None, commander.average_return_pct - benchmark.average_return_pct if benchmark.average_return_pct is not None and commander.average_return_pct is not None else None, commander.max_drawdown - benchmark.max_drawdown if benchmark.max_drawdown is not None and commander.max_drawdown is not None else None, None, status))
    return tuple(results)


__all__ = ["BenchmarkComparisonResult", "BenchmarkDefinition", "BenchmarkPerformanceSummary", "compare_benchmarks"]
