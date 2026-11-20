"""Read-only canonical market-regime segmentation for Sprint 9 Phase 6."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Iterable, Mapping

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, ValidationObservation, _utc, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


@dataclass(frozen=True)
class CanonicalRegimePoint:
    symbol: str
    timestamp_utc: datetime
    market_regime: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc, "timestamp_utc"))
        if not self.symbol or not self.market_regime:
            raise ValueError("canonical regime point requires symbol and market_regime")


def resolve_canonical_regime(observation: ValidationObservation, points: Iterable[CanonicalRegimePoint]) -> str:
    """Resolve only a canonical state known at/before the decision timestamp."""
    eligible = [point for point in points if point.symbol == observation.symbol and point.timestamp_utc <= observation.timestamp_utc]
    if not eligible:
        return DATA_NOT_AVAILABLE
    return max(eligible, key=lambda point: point.timestamp_utc).market_regime


@dataclass(frozen=True)
class RegimeAnalysisConfig:
    dependency_expectancy_dispersion: Decimal = Decimal("0")
    concentration_ratio: Decimal = Decimal("0.70")

    def __post_init__(self) -> None:
        if self.dependency_expectancy_dispersion < 0 or not Decimal("0") <= self.concentration_ratio <= Decimal("1"):
            raise ValueError("invalid regime analysis thresholds")


@dataclass(frozen=True)
class RegimePerformanceSummary:
    market_regime: str
    validation_horizon: str
    strategy_id: str | None
    direction_segment: str
    confidence_band: str | None
    sample_count: int
    sample_size_status: SampleSizeStatus
    candidate_count: int
    executed_count: int
    correct_direction_count: int
    wrong_direction_count: int
    neutral_count: int
    data_not_available_count: int
    directional_accuracy: Decimal | None
    winning_trade_count: int
    losing_trade_count: int
    breakeven_trade_count: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    breakeven_rate: Decimal | None
    gross_pnl: Decimal | None
    fees_paid: Decimal | None
    slippage_cost: Decimal | None
    net_pnl: Decimal | None
    expectancy: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    max_drawdown_pct: Decimal | None
    average_mfe: Decimal | None
    average_mae: Decimal | None
    performance_status: str
    regime_dependency_status: str | None = None


@dataclass(frozen=True)
class RegimeAnalysisSummary:
    summaries: tuple[RegimePerformanceSummary, ...]
    missing_regime_count: int
    total_observations: int
    observations_with_regime: int
    observations_without_regime: int
    ranking: tuple[tuple[str, str, str | None], ...]


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


def _pf(values: list[Decimal]) -> Decimal | None:
    wins = sum((value for value in values if value > 0), Decimal("0"))
    losses = sum((value for value in values if value < 0), Decimal("0"))
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


def _status(values: list[Decimal], sample: int) -> str:
    if not values or sample < 1:
        return "INSUFFICIENT_DATA"
    if any(value > 0 for value in values) and any(value < 0 for value in values):
        return "MIXED"
    return "POSITIVE" if sum(values) > 0 else "NEGATIVE" if sum(values) < 0 else "MIXED"


def _make_summary(records: list[CalibrationRecord], regime: str, horizon: str, strategy_id: str | None, direction: str, confidence_band: str | None) -> RegimePerformanceSummary:
    candidates = [item for item in records if item.observation.decision_type in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}]
    economics = [item.observation.net_pnl for item in candidates if item.observation.net_pnl is not None]
    returns = [item.observation.return_pct for item in candidates if item.observation.return_pct is not None]
    correct = sum(item.label.direction_label is DirectionLabel.CORRECT_DIRECTION for item in candidates)
    wrong = sum(item.label.direction_label is DirectionLabel.WRONG_DIRECTION for item in candidates)
    neutral = sum(item.label.direction_label is DirectionLabel.NEUTRAL for item in candidates)
    unavailable = sum(item.label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for item in candidates)
    denom = correct + wrong
    fees = [item.observation.fees for item in candidates if item.observation.fees is not None]
    slippage = [item.observation.slippage for item in candidates if item.observation.slippage is not None]
    mfe = [item.observation.mfe for item in candidates if item.observation.mfe is not None]
    mae = [item.observation.mae for item in candidates if item.observation.mae is not None]
    gross = [item.observation.gross_pnl for item in candidates if item.observation.gross_pnl is not None]
    wins = sum(value > 0 for value in economics)
    losses = sum(value < 0 for value in economics)
    breakeven = sum(value == 0 for value in economics)
    return RegimePerformanceSummary(
        market_regime=regime, validation_horizon=horizon, strategy_id=strategy_id, direction_segment=direction,
        confidence_band=confidence_band, sample_count=len(candidates), sample_size_status=classify_sample_size(len(candidates)),
        candidate_count=len(candidates), executed_count=sum(item.label.execution_label is ExecutionLabel.EXECUTED for item in candidates),
        correct_direction_count=correct, wrong_direction_count=wrong, neutral_count=neutral, data_not_available_count=unavailable,
        directional_accuracy=Decimal(correct) / denom if denom else None,
        winning_trade_count=wins, losing_trade_count=losses, breakeven_trade_count=breakeven,
        win_rate=Decimal(wins) / len(economics) if economics else None, loss_rate=Decimal(losses) / len(economics) if economics else None,
        breakeven_rate=Decimal(breakeven) / len(economics) if economics else None,
        gross_pnl=sum(gross, Decimal("0")) if gross else None, fees_paid=sum(fees, Decimal("0")) if fees else None,
        slippage_cost=sum(slippage, Decimal("0")) if slippage else None, net_pnl=sum(economics, Decimal("0")) if economics else None,
        expectancy=_mean(economics), average_return_pct=_mean(returns), median_return_pct=Decimal(str(median(returns))) if returns else None,
        profit_factor=_pf(economics), max_drawdown=_dd(economics), max_drawdown_pct=None,
        average_mfe=_mean(mfe), average_mae=_mean(mae), performance_status=_status(economics, len(candidates)),
    )


def analyze_regimes(records: Iterable[CalibrationRecord], *, direction_segment: str = "ALL", strategy_id: str | None = None, confidence_band: str | None = None, config: RegimeAnalysisConfig = RegimeAnalysisConfig()) -> RegimeAnalysisSummary:
    values = tuple(records)
    if direction_segment not in {"ALL", "LONG", "SHORT"}:
        raise ValueError("direction_segment must be ALL, LONG, or SHORT")
    candidates = []
    for item in values:
        kind = item.observation.decision_type
        if kind not in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}:
            continue
        if direction_segment == "LONG" and kind is not DecisionType.CANDIDATE_LONG:
            continue
        if direction_segment == "SHORT" and kind is not DecisionType.CANDIDATE_SHORT:
            continue
        if strategy_id is not None and item.observation.strategy_id != strategy_id:
            continue
        if confidence_band is not None and item.observation.confidence_band != confidence_band:
            continue
        candidates.append(item)
    missing = sum(item.observation.market_regime in (None, DATA_NOT_AVAILABLE) for item in candidates)
    groups: dict[tuple[str, str, str | None], list[CalibrationRecord]] = {}
    for item in candidates:
        regime = item.observation.market_regime
        if regime in (None, DATA_NOT_AVAILABLE):
            continue
        key = (regime, item.observation.validation_horizon, item.observation.strategy_id)
        groups.setdefault(key, []).append(item)
    summaries = tuple(_make_summary(group, regime, horizon, strategy, direction_segment, confidence_band) for (regime, horizon, strategy), group in sorted(groups.items()))
    def rank_key(item: RegimePerformanceSummary):
        order = {"STRONGER_SAMPLE": 3, "MODERATE_SAMPLE": 2, "LIMITED_SAMPLE": 1, "INSUFFICIENT_SAMPLE": 0}
        return (-order[item.sample_size_status.value], -(item.expectancy or Decimal("-Infinity")), -(item.net_pnl or Decimal("-Infinity")), -(item.max_drawdown or Decimal("-Infinity")), -(item.profit_factor or Decimal("-Infinity")), -(item.directional_accuracy or Decimal("-Infinity")), item.market_regime, item.validation_horizon)
    ranking = tuple((item.market_regime, item.validation_horizon, item.strategy_id) for item in sorted(summaries, key=rank_key))
    return RegimeAnalysisSummary(summaries, missing, len(values), len(candidates) - missing, missing, ranking)


__all__ = ["CanonicalRegimePoint", "RegimeAnalysisConfig", "RegimeAnalysisSummary", "RegimePerformanceSummary", "analyze_regimes", "resolve_canonical_regime"]
