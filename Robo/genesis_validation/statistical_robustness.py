"""Deterministic statistical robustness diagnostics for Sprint 9 Phase 9."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, classify_sample_size
from .labeling import DirectionLabel


Z_VALUES = {Decimal("0.90"): Decimal("1.64485362695147"), Decimal("0.95"): Decimal("1.95996398454005"), Decimal("0.99"): Decimal("2.5758293035489")}


@dataclass(frozen=True)
class StatisticalConfig:
    seed: int = 202609
    resample_count: int = 1000
    confidence_level: Decimal = Decimal("0.95")
    block_size: int = 1
    subperiod_count: int = 2
    top_n_outliers: int = 1

    def __post_init__(self) -> None:
        if self.resample_count <= 0 or self.block_size <= 0 or self.subperiod_count <= 0 or self.top_n_outliers <= 0:
            raise ValueError("statistical config counts must be positive")
        if self.confidence_level not in Z_VALUES:
            raise ValueError("confidence_level must be 0.90, 0.95, or 0.99")


@dataclass(frozen=True)
class StatisticalMetricInterval:
    metric_name: str
    point_estimate: Decimal | None
    lower_bound: Decimal | None
    upper_bound: Decimal | None
    confidence_level: Decimal
    interval_status: str
    bootstrap_method: str
    resample_count: int
    seed: int


@dataclass(frozen=True)
class SubperiodPerformanceSummary:
    index: int
    sample_count: int
    net_pnl: Decimal | None
    expectancy_net_pnl: Decimal | None
    directional_accuracy: Decimal | None
    win_rate: Decimal | None


@dataclass(frozen=True)
class RobustnessAnalysisSummary:
    segment_identity: str
    validation_horizon: str | None
    sample_count: int
    sample_size_status: SampleSizeStatus
    eligible_count: int
    excluded_missing_count: int
    intervals: tuple[StatisticalMetricInterval, ...]
    subperiods: tuple[SubperiodPerformanceSummary, ...]
    outlier_sensitivity_status: str
    subperiod_stability_status: str
    sign_consistency_status: str
    temporal_dependence_policy: str


def _quantile(values: list[Decimal], q: Decimal) -> Decimal:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - Decimal(low)
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def wilson_interval(successes: int, trials: int, confidence_level: Decimal = Decimal("0.95")) -> tuple[Decimal, Decimal] | None:
    if trials <= 0 or successes < 0 or successes > trials:
        return None
    z = Z_VALUES.get(confidence_level)
    if z is None:
        raise ValueError("unsupported confidence_level")
    n = Decimal(trials)
    p = Decimal(successes) / n
    z2 = z * z
    denominator = Decimal(1) + z2 / n
    centre = (p + z2 / (Decimal(2) * n)) / denominator
    margin = z / denominator * ((p * (Decimal(1) - p) / n + z2 / (Decimal(4) * n * n)).sqrt())
    return max(Decimal(0), centre - margin), min(Decimal(1), centre + margin)


def _bootstrap(values: list[Decimal], config: StatisticalConfig) -> list[Decimal]:
    if not values:
        return []
    rng = random.Random(config.seed)
    size = len(values)
    block = min(config.block_size, size)
    samples: list[Decimal] = []
    for _ in range(config.resample_count):
        selected: list[Decimal] = []
        while len(selected) < size:
            start = rng.randrange(size)
            selected.extend(values[start:start + block])
        selected = selected[:size]
        samples.append(sum(selected, Decimal("0")) / Decimal(size))
    return samples


def _economic_interval(name: str, values: list[Decimal], config: StatisticalConfig) -> StatisticalMetricInterval:
    if not values:
        return StatisticalMetricInterval(name, None, None, None, config.confidence_level, "NOT_APPLICABLE", "BLOCK_BOOTSTRAP", 0, config.seed)
    point = sum(values, Decimal("0")) / Decimal(len(values))
    bootstrap = _bootstrap(values, config)
    alpha = (Decimal(1) - config.confidence_level) / Decimal(2)
    return StatisticalMetricInterval(name, point, _quantile(bootstrap, alpha), _quantile(bootstrap, Decimal(1) - alpha), config.confidence_level, "VALUE", "BLOCK_BOOTSTRAP", config.resample_count, config.seed)


def _interval_status(interval: StatisticalMetricInterval) -> str:
    if interval.lower_bound is None:
        return "NOT_APPLICABLE"
    if interval.lower_bound > 0:
        return "POSITIVE_INTERVAL"
    if interval.upper_bound < 0:
        return "NEGATIVE_INTERVAL"
    return "ZERO_CROSSING"


def _subperiod(rows: list[CalibrationRecord], index: int) -> SubperiodPerformanceSummary:
    economics = [row.observation.net_pnl for row in rows if row.observation.net_pnl is not None]
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in rows)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in rows)
    win_count = sum(value > 0 for value in economics)
    return SubperiodPerformanceSummary(index, len(rows), sum(economics, Decimal("0")) if economics else None, sum(economics, Decimal("0")) / Decimal(len(economics)) if economics else None, Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None, Decimal(win_count) / Decimal(len(economics)) if economics else None)


def analyze_robustness(records: Iterable[CalibrationRecord], *, segment_identity: str = "overall", config: StatisticalConfig = StatisticalConfig()) -> RobustnessAnalysisSummary:
    values = tuple(records)
    horizons = {row.observation.validation_horizon for row in values}
    if len(horizons) > 1:
        raise ValueError("statistical analysis requires one validation horizon per run")
    ordered = tuple(sorted(values, key=lambda row: (row.observation.timestamp_utc, row.observation.commander_decision_id)))
    eligible = [row for row in ordered if row.observation.decision_type in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}]
    economics = [row.observation.net_pnl for row in eligible if row.observation.net_pnl is not None]
    excluded = len(eligible) - len(economics)
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in eligible)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in eligible)
    wins = sum(value > 0 for value in economics)
    accuracy_interval = wilson_interval(correct, correct + wrong, config.confidence_level)
    win_interval = wilson_interval(wins, len(economics), config.confidence_level)
    intervals = [
        StatisticalMetricInterval("directional_accuracy", Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None, *(accuracy_interval or (None, None)), config.confidence_level, "VALUE" if accuracy_interval else "NOT_APPLICABLE", "WILSON", 0, config.seed),
        StatisticalMetricInterval("win_rate", Decimal(wins) / Decimal(len(economics)) if economics else None, *(win_interval or (None, None)), config.confidence_level, "VALUE" if win_interval else "NOT_APPLICABLE", "WILSON", 0, config.seed),
        _economic_interval("expectancy_net_pnl", economics, config),
        _economic_interval("average_return_pct", [row.observation.return_pct for row in eligible if row.observation.return_pct is not None], config),
        _economic_interval("net_pnl", economics, config),
    ]
    intervals = [StatisticalMetricInterval(item.metric_name, item.point_estimate, item.lower_bound, item.upper_bound, item.confidence_level, _interval_status(item), item.bootstrap_method, item.resample_count, item.seed) for item in intervals]
    n = len(eligible)
    period_count = min(config.subperiod_count, n) if n else 0
    subperiods: list[SubperiodPerformanceSummary] = []
    if period_count:
        for index in range(period_count):
            start = index * n // period_count
            end = (index + 1) * n // period_count
            subperiods.append(_subperiod(eligible[start:end], index))
    signs = [item.expectancy_net_pnl for item in subperiods if item.expectancy_net_pnl is not None]
    if len(signs) < 2:
        sign_status = "INSUFFICIENT_DATA"
        stability = "INSUFFICIENT_DATA"
    elif all(value > 0 for value in signs):
        sign_status = "CONSISTENT_POSITIVE"
        stability = "STABLE"
    elif all(value < 0 for value in signs):
        sign_status = "CONSISTENT_NEGATIVE"
        stability = "STABLE"
    else:
        sign_status = "MIXED"
        stability = "UNSTABLE"
    positive_count = sum(value > 0 for value in economics)
    negative_count = sum(value < 0 for value in economics)
    if len(economics) < 2:
        outlier = "INSUFFICIENT_DATA"
    else:
        top_winner = max((value for value in economics if value > 0), default=Decimal(0))
        top_loser = abs(min((value for value in economics if value < 0), default=Decimal(0)))
        positive_total = sum((value for value in economics if value > 0), Decimal(0))
        negative_total = abs(sum((value for value in economics if value < 0), Decimal(0)))
        outlier = "OUTLIER_SENSITIVE" if (positive_total and top_winner / positive_total > Decimal("0.50")) or (negative_total and top_loser / negative_total > Decimal("0.50")) else "ROBUST_TO_OUTLIERS"
    return RobustnessAnalysisSummary(segment_identity, next(iter(horizons), None), len(eligible), classify_sample_size(len(eligible)), len(economics), excluded, tuple(intervals), tuple(subperiods), outlier, stability, sign_status, "BLOCK_BOOTSTRAP" if config.block_size > 1 else "IID_BASELINE_DISCLOSED")


__all__ = ["RobustnessAnalysisSummary", "StatisticalConfig", "StatisticalMetricInterval", "SubperiodPerformanceSummary", "analyze_robustness", "wilson_interval"]
