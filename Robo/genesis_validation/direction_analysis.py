"""Deterministic analytical LONG vs SHORT comparison."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


@dataclass(frozen=True)
class DirectionPerformanceSummary:
    validation_horizon: str
    direction: str
    sample_count: int
    sample_size_status: SampleSizeStatus
    candidate_count: int
    executed_count: int
    not_executed_count: int
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
    expectancy_net_pnl: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    max_drawdown_pct: Decimal | None
    average_mfe: Decimal | None
    average_mae: Decimal | None
    gross_exposure: Decimal | None
    net_exposure: Decimal | None
    turnover: Decimal | None


@dataclass(frozen=True)
class LongShortComparisonSummary:
    summaries: tuple[DirectionPerformanceSummary, ...]
    long_candidate_count: int
    short_candidate_count: int
    executed_long_count: int
    executed_short_count: int
    balance_status: str
    performance_asymmetry_status: str
    strategy_direction_matrix: tuple[tuple[str, str, str], ...]
    regime_direction_matrix: tuple[tuple[str, str, str], ...]
    confidence_direction_matrix: tuple[tuple[str, str, str], ...]


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


def _make_summary(rows: list[CalibrationRecord], horizon: str, direction: str) -> DirectionPerformanceSummary:
    economics = [row.observation.net_pnl for row in rows if row.observation.net_pnl is not None]
    returns = [row.observation.return_pct for row in rows if row.observation.return_pct is not None]
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in rows)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in rows)
    neutral = sum(row.label.direction_label is DirectionLabel.NEUTRAL for row in rows)
    unavailable = sum(row.label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for row in rows)
    denominator = correct + wrong
    fees = [row.observation.fees for row in rows if row.observation.fees is not None]
    slippage = [row.observation.slippage for row in rows if row.observation.slippage is not None]
    gross = [row.observation.gross_pnl for row in rows if row.observation.gross_pnl is not None]
    mfe = [row.observation.mfe for row in rows if row.observation.mfe is not None]
    mae = [row.observation.mae for row in rows if row.observation.mae is not None]
    wins = sum(v > 0 for v in economics)
    losses = sum(v < 0 for v in economics)
    breakeven = sum(v == 0 for v in economics)
    return DirectionPerformanceSummary(
        validation_horizon=horizon, direction=direction, sample_count=len(rows), sample_size_status=classify_sample_size(len(rows)),
        candidate_count=len(rows), executed_count=sum(row.label.execution_label is ExecutionLabel.EXECUTED for row in rows),
        not_executed_count=sum(row.label.execution_label is ExecutionLabel.NOT_EXECUTED for row in rows),
        correct_direction_count=correct, wrong_direction_count=wrong, neutral_count=neutral, data_not_available_count=unavailable,
        directional_accuracy=Decimal(correct) / denominator if denominator else None,
        winning_trade_count=wins, losing_trade_count=losses, breakeven_trade_count=breakeven,
        win_rate=Decimal(wins) / len(economics) if economics else None, loss_rate=Decimal(losses) / len(economics) if economics else None,
        breakeven_rate=Decimal(breakeven) / len(economics) if economics else None,
        gross_pnl=sum(gross, Decimal("0")) if gross else None, fees_paid=sum(fees, Decimal("0")) if fees else None,
        slippage_cost=sum(slippage, Decimal("0")) if slippage else None, net_pnl=sum(economics, Decimal("0")) if economics else None,
        expectancy_net_pnl=_mean(economics), average_return_pct=_mean(returns),
        median_return_pct=Decimal(str(median(returns))) if returns else None,
        profit_factor=_pf(economics), max_drawdown=_dd(economics), max_drawdown_pct=None,
        average_mfe=_mean(mfe), average_mae=_mean(mae),
        gross_exposure=_mean([row.observation.gross_exposure for row in rows if row.observation.gross_exposure is not None]),
        net_exposure=_mean([row.observation.net_exposure for row in rows if row.observation.net_exposure is not None]),
        turnover=sum((row.observation.turnover for row in rows if row.observation.turnover is not None), Decimal("0")) or None,
    )


def _balance(long_count: int, short_count: int, threshold: Decimal) -> str:
    if long_count + short_count == 0:
        return "INSUFFICIENT_DATA"
    ratio = Decimal(max(long_count, short_count)) / Decimal(long_count + short_count)
    if ratio <= threshold:
        return "BALANCED"
    return "LONG_DOMINANT" if long_count > short_count else "SHORT_DOMINANT"


def _asymmetry(summaries: list[DirectionPerformanceSummary]) -> str:
    by_direction = {item.direction: item for item in summaries}
    long = by_direction.get("LONG")
    short = by_direction.get("SHORT")
    if long is None or short is None or long.expectancy_net_pnl is None or short.expectancy_net_pnl is None:
        return "INSUFFICIENT_DATA"
    if long.expectancy_net_pnl == short.expectancy_net_pnl and long.directional_accuracy == short.directional_accuracy:
        return "SIMILAR"
    if long.expectancy_net_pnl > short.expectancy_net_pnl and (long.directional_accuracy or 0) >= (short.directional_accuracy or 0):
        return "LONG_STRONGER"
    if short.expectancy_net_pnl > long.expectancy_net_pnl and (short.directional_accuracy or 0) >= (long.directional_accuracy or 0):
        return "SHORT_STRONGER"
    return "MIXED"


def compare_long_short(records: Iterable[CalibrationRecord], *, balance_threshold: Decimal = Decimal("0.60"), confidence_band: str | None = None) -> LongShortComparisonSummary:
    if not Decimal("0") <= balance_threshold <= Decimal("1"):
        raise ValueError("balance_threshold must be between 0 and 1")
    values = tuple(records)
    candidates = [row for row in values if row.observation.decision_type in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}]
    if confidence_band is not None:
        candidates = [row for row in candidates if row.observation.confidence_band == confidence_band]
    groups: dict[tuple[str, str], list[CalibrationRecord]] = {}
    for row in candidates:
        direction = "LONG" if row.observation.decision_type is DecisionType.CANDIDATE_LONG else "SHORT"
        groups.setdefault((row.observation.validation_horizon, direction), []).append(row)
    summaries = tuple(_make_summary(rows, horizon, direction) for (horizon, direction), rows in sorted(groups.items()))
    all_h1 = [item for item in summaries if item.validation_horizon == (summaries[0].validation_horizon if summaries else "")]
    long_count = sum(row.observation.decision_type is DecisionType.CANDIDATE_LONG for row in candidates)
    short_count = len(candidates) - long_count
    matrix_strategy = tuple(sorted({(row.observation.strategy_id or DATA_NOT_AVAILABLE, row.observation.validation_horizon, "LONG" if row.observation.decision_type is DecisionType.CANDIDATE_LONG else "SHORT") for row in candidates}))
    matrix_regime = tuple(sorted({(row.observation.market_regime or DATA_NOT_AVAILABLE, row.observation.validation_horizon, "LONG" if row.observation.decision_type is DecisionType.CANDIDATE_LONG else "SHORT") for row in candidates}))
    matrix_confidence = tuple(sorted({(row.observation.confidence_band or DATA_NOT_AVAILABLE, row.observation.validation_horizon, "LONG" if row.observation.decision_type is DecisionType.CANDIDATE_LONG else "SHORT") for row in candidates}))
    return LongShortComparisonSummary(
        summaries=summaries, long_candidate_count=long_count, short_candidate_count=short_count,
        executed_long_count=sum(row.label.execution_label is ExecutionLabel.EXECUTED and row.observation.decision_type is DecisionType.CANDIDATE_LONG for row in candidates),
        executed_short_count=sum(row.label.execution_label is ExecutionLabel.EXECUTED and row.observation.decision_type is DecisionType.CANDIDATE_SHORT for row in candidates),
        balance_status=_balance(long_count, short_count, balance_threshold), performance_asymmetry_status=_asymmetry(all_h1),
        strategy_direction_matrix=matrix_strategy, regime_direction_matrix=matrix_regime, confidence_direction_matrix=matrix_confidence,
    )


__all__ = ["DirectionPerformanceSummary", "LongShortComparisonSummary", "compare_long_short"]
