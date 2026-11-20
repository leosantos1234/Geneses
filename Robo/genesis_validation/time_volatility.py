"""Read-only time and canonical-volatility segmentation for Phase 8."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, ValidationObservation, _utc, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


@dataclass(frozen=True)
class VolatilityBand:
    name: str
    lower_bound: Decimal
    upper_bound: Decimal
    upper_inclusive: bool = False

    def contains(self, value: Decimal) -> bool:
        return self.lower_bound <= value <= self.upper_bound if self.upper_inclusive else self.lower_bound <= value < self.upper_bound


@dataclass(frozen=True)
class TimeVolatilityConfig:
    hour_bucket_size: int = 1
    volatility_bands: tuple[VolatilityBand, ...] = ()
    dependency_dispersion: Decimal = Decimal("0")
    concentration_ratio: Decimal = Decimal("0.70")

    def __post_init__(self) -> None:
        if self.hour_bucket_size not in {1, 2, 3, 4, 6, 8, 12, 24}:
            raise ValueError("hour_bucket_size must divide 24")
        if self.dependency_dispersion < 0 or not Decimal("0") <= self.concentration_ratio <= Decimal("1"):
            raise ValueError("invalid dependency thresholds")
        for left, right in zip(self.volatility_bands, self.volatility_bands[1:]):
            if left.upper_bound != right.lower_bound:
                raise ValueError("volatility bands must be contiguous")


@dataclass(frozen=True)
class CanonicalVolatilityPoint:
    symbol: str
    timestamp_utc: datetime
    volatility: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc, "timestamp_utc"))
        if not self.symbol or self.volatility < 0:
            raise ValueError("invalid canonical volatility point")


def resolve_canonical_volatility(observation: ValidationObservation, points: Iterable[CanonicalVolatilityPoint]) -> Decimal | None:
    eligible = [point for point in points if point.symbol == observation.symbol and point.timestamp_utc <= observation.timestamp_utc]
    return max(eligible, key=lambda point: point.timestamp_utc).volatility if eligible else None


@dataclass(frozen=True)
class SegmentPerformanceSummary:
    segment: str
    validation_horizon: str
    sample_count: int
    sample_size_status: SampleSizeStatus
    directional_accuracy: Decimal | None
    win_rate: Decimal | None
    loss_rate: Decimal | None
    breakeven_rate: Decimal | None
    net_pnl: Decimal | None
    expectancy: Decimal | None
    gross_pnl: Decimal | None
    fees_paid: Decimal | None
    slippage_cost: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    max_drawdown_pct: Decimal | None
    average_mfe: Decimal | None
    average_mae: Decimal | None
    performance_status: str


@dataclass(frozen=True)
class TimeVolatilityAnalysisSummary:
    dimension: str
    summaries: tuple[SegmentPerformanceSummary, ...]
    total_observations: int
    observations_with_segment: int
    observations_without_segment: int
    dependency_status: str
    ranking: tuple[str, ...]


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


def _pf(values: list[Decimal]) -> Decimal | None:
    wins = sum((v for v in values if v > 0), Decimal("0"))
    losses = sum((v for v in values if v < 0), Decimal("0"))
    if not losses:
        return Decimal("Infinity") if wins else None
    return wins / abs(losses)


def _dd(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    equity = peak = max_dd = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd


def _summary(rows: list[CalibrationRecord], segment: str, horizon: str) -> SegmentPerformanceSummary:
    economics = [row.observation.net_pnl for row in rows if row.observation.net_pnl is not None]
    returns = [row.observation.return_pct for row in rows if row.observation.return_pct is not None]
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in rows)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in rows)
    denom = correct + wrong
    wins = sum(v > 0 for v in economics)
    losses = sum(v < 0 for v in economics)
    breakeven = sum(v == 0 for v in economics)
    fees = [row.observation.fees for row in rows if row.observation.fees is not None]
    slippage = [row.observation.slippage for row in rows if row.observation.slippage is not None]
    gross = [row.observation.gross_pnl for row in rows if row.observation.gross_pnl is not None]
    mfe = [row.observation.mfe for row in rows if row.observation.mfe is not None]
    mae = [row.observation.mae for row in rows if row.observation.mae is not None]
    status = "INSUFFICIENT_DATA" if not economics else "MIXED" if any(v > 0 for v in economics) and any(v < 0 for v in economics) else "POSITIVE" if sum(economics) > 0 else "NEGATIVE" if sum(economics) < 0 else "MIXED"
    return SegmentPerformanceSummary(
        segment=segment, validation_horizon=horizon, sample_count=len(rows), sample_size_status=classify_sample_size(len(rows)),
        directional_accuracy=Decimal(correct) / denom if denom else None,
        win_rate=Decimal(wins) / len(economics) if economics else None, loss_rate=Decimal(losses) / len(economics) if economics else None,
        breakeven_rate=Decimal(breakeven) / len(economics) if economics else None,
        net_pnl=sum(economics, Decimal("0")) if economics else None, expectancy=_mean(economics),
        gross_pnl=sum(gross, Decimal("0")) if gross else None, fees_paid=sum(fees, Decimal("0")) if fees else None,
        slippage_cost=sum(slippage, Decimal("0")) if slippage else None, average_return_pct=_mean(returns),
        median_return_pct=Decimal(str(median(returns))) if returns else None, profit_factor=_pf(economics),
        max_drawdown=_dd(economics), max_drawdown_pct=None, average_mfe=_mean(mfe), average_mae=_mean(mae), performance_status=status,
    )


def _time_segment(observation: ValidationObservation, config: TimeVolatilityConfig) -> str:
    hour = observation.timestamp_utc.hour
    start = (hour // config.hour_bucket_size) * config.hour_bucket_size
    return f"UTC_{start:02d}_{start + config.hour_bucket_size - 1:02d}"


def _volatility_segment(observation: ValidationObservation, config: TimeVolatilityConfig) -> str:
    if observation.volatility_band:
        return observation.volatility_band
    if observation.volatility is None or not config.volatility_bands:
        return DATA_NOT_AVAILABLE
    for band in config.volatility_bands:
        if band.contains(observation.volatility):
            return band.name
    return DATA_NOT_AVAILABLE


def analyze_time_volatility(records: Iterable[CalibrationRecord], *, dimension: str = "time", config: TimeVolatilityConfig = TimeVolatilityConfig(), strategy_id: str | None = None, direction: str | None = None, confidence_band: str | None = None) -> TimeVolatilityAnalysisSummary:
    if dimension not in {"time", "volatility"}:
        raise ValueError("dimension must be time or volatility")
    values = tuple(records)
    candidates = []
    for row in values:
        kind = row.observation.decision_type
        if kind not in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}:
            continue
        if strategy_id is not None and row.observation.strategy_id != strategy_id:
            continue
        if direction == "LONG" and kind is not DecisionType.CANDIDATE_LONG:
            continue
        if direction == "SHORT" and kind is not DecisionType.CANDIDATE_SHORT:
            continue
        if confidence_band is not None and row.observation.confidence_band != confidence_band:
            continue
        candidates.append(row)
    segments = {row: (_time_segment(row.observation, config) if dimension == "time" else _volatility_segment(row.observation, config)) for row in candidates}
    missing = sum(segment == DATA_NOT_AVAILABLE for segment in segments.values())
    groups: dict[tuple[str, str], list[CalibrationRecord]] = {}
    for row, segment in segments.items():
        if segment != DATA_NOT_AVAILABLE:
            groups.setdefault((segment, row.observation.validation_horizon), []).append(row)
    summaries = tuple(_summary(group, segment, horizon) for (segment, horizon), group in sorted(groups.items()))
    values_for_dependency = [item.expectancy for item in summaries if item.expectancy is not None]
    if len(values_for_dependency) < 2:
        dependency = "INSUFFICIENT_DATA"
    elif max(values_for_dependency) - min(values_for_dependency) > config.dependency_dispersion:
        dependency = "TIME_SENSITIVE" if dimension == "time" else "VOLATILITY_SENSITIVE"
    else:
        dependency = "TIME_INDEPENDENT" if dimension == "time" else "VOLATILITY_INDEPENDENT"
    ranking = tuple(item.segment for item in sorted(summaries, key=lambda item: (-(item.expectancy or Decimal("-Infinity")), -(item.net_pnl or Decimal("-Infinity")), item.segment, item.validation_horizon)))
    return TimeVolatilityAnalysisSummary(dimension, summaries, len(values), len(candidates) - missing, missing, dependency, ranking)


__all__ = ["CanonicalVolatilityPoint", "SegmentPerformanceSummary", "TimeVolatilityAnalysisSummary", "TimeVolatilityConfig", "VolatilityBand", "analyze_time_volatility", "resolve_canonical_volatility"]
