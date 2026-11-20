"""Deterministic, read-only strategy-level performance analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, Decimal("0")) / len(values) if values else None


def _profit_factor(values: list[Decimal]) -> Decimal | None:
    wins = sum((value for value in values if value > 0), Decimal("0"))
    losses = sum((value for value in values if value < 0), Decimal("0"))
    if not losses:
        return Decimal("Infinity") if wins else None
    return wins / abs(losses)


def _drawdown(values: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    if not values:
        return None, None
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd, None


@dataclass(frozen=True)
class StrategyPerformanceSummary:
    strategy_id: str
    strategy_name: str | None
    validation_horizon: str
    direction_segment: str
    confidence_band: str | None
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
    performance_status: str


@dataclass(frozen=True)
class StrategyPerformanceReport:
    summaries: tuple[StrategyPerformanceSummary, ...]
    ranking: tuple[tuple[str, str], ...]
    missing_strategy_count: int


def _status(net: Decimal | None, expectancy: Decimal | None, sample_count: int, values: list[Decimal]) -> str:
    if sample_count < 1 or net is None or expectancy is None:
        return "INSUFFICIENT_DATA"
    if any(value > 0 for value in values) and any(value < 0 for value in values):
        return "MIXED"
    if net > 0 and expectancy > 0:
        return "POSITIVE"
    if net < 0 and expectancy < 0:
        return "NEGATIVE"
    return "MIXED"


def _summary(records: list[CalibrationRecord], strategy_id: str, strategy_name: str | None, horizon: str, direction: str, confidence_band: str | None) -> StrategyPerformanceSummary:
    candidates = [item for item in records if item.observation.decision_type in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}]
    economic = [item for item in candidates if item.observation.net_pnl is not None]
    net_values = [item.observation.net_pnl for item in economic]
    returns = [item.observation.return_pct for item in economic if item.observation.return_pct is not None]
    correct = sum(item.label.direction_label is DirectionLabel.CORRECT_DIRECTION for item in candidates)
    wrong = sum(item.label.direction_label is DirectionLabel.WRONG_DIRECTION for item in candidates)
    neutral = sum(item.label.direction_label is DirectionLabel.NEUTRAL for item in candidates)
    unavailable = sum(item.label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for item in candidates)
    denominator = correct + wrong
    fees = [item.observation.fees for item in economic if item.observation.fees is not None]
    slippage = [item.observation.slippage for item in economic if item.observation.slippage is not None]
    gross_values = [item.observation.gross_pnl for item in economic if getattr(item.observation, "gross_pnl", None) is not None]
    mfe = [item.observation.mfe for item in economic if item.observation.mfe is not None]
    mae = [item.observation.mae for item in economic if item.observation.mae is not None]
    gross_exposure = [item.observation.gross_exposure for item in economic if getattr(item.observation, "gross_exposure", None) is not None]
    net_exposure = [item.observation.net_exposure for item in economic if getattr(item.observation, "net_exposure", None) is not None]
    turnover = [item.observation.turnover for item in economic if getattr(item.observation, "turnover", None) is not None]
    dd, dd_pct = _drawdown(net_values)
    wins = sum(value > 0 for value in net_values)
    losses = sum(value < 0 for value in net_values)
    breakeven = sum(value == 0 for value in net_values)
    sample = len(candidates)
    return StrategyPerformanceSummary(
        strategy_id=strategy_id, strategy_name=strategy_name, validation_horizon=horizon,
        direction_segment=direction, confidence_band=confidence_band, sample_count=sample,
        sample_size_status=classify_sample_size(sample), candidate_count=sample,
        executed_count=sum(item.label.execution_label is ExecutionLabel.EXECUTED for item in candidates),
        not_executed_count=sum(item.label.execution_label is ExecutionLabel.NOT_EXECUTED for item in candidates),
        correct_direction_count=correct, wrong_direction_count=wrong, neutral_count=neutral,
        data_not_available_count=unavailable, directional_accuracy=Decimal(correct) / denominator if denominator else None,
        winning_trade_count=wins, losing_trade_count=losses, breakeven_trade_count=breakeven,
        win_rate=Decimal(wins) / len(net_values) if net_values else None,
        loss_rate=Decimal(losses) / len(net_values) if net_values else None,
        breakeven_rate=Decimal(breakeven) / len(net_values) if net_values else None,
        gross_pnl=sum(gross_values, Decimal("0")) if gross_values else None,
        fees_paid=sum(fees, Decimal("0")) if fees else None,
        slippage_cost=sum(slippage, Decimal("0")) if slippage else None,
        net_pnl=sum(net_values, Decimal("0")) if net_values else None,
        expectancy_net_pnl=_mean(net_values), average_return_pct=_mean(returns),
        median_return_pct=Decimal(str(median(returns))) if returns else None,
        profit_factor=_profit_factor(net_values), max_drawdown=dd, max_drawdown_pct=dd_pct,
        average_mfe=_mean(mfe), average_mae=_mean(mae), gross_exposure=_mean(gross_exposure),
        net_exposure=_mean(net_exposure), turnover=sum(turnover, Decimal("0")) if turnover else None,
        performance_status=_status(sum(net_values, Decimal("0")) if net_values else None, _mean(net_values), sample, net_values),
    )


def analyze_strategy_performance(records: Iterable[CalibrationRecord], *, direction_segment: str = "ALL", confidence_band: str | None = None) -> StrategyPerformanceReport:
    """Aggregate candidates by canonical strategy identity and horizon."""
    values = tuple(records)
    if direction_segment not in {"ALL", "LONG", "SHORT"}:
        raise ValueError("direction_segment must be ALL, LONG, or SHORT")
    selected = []
    for item in values:
        kind = item.observation.decision_type
        if kind not in {DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}:
            continue
        if direction_segment == "LONG" and kind is not DecisionType.CANDIDATE_LONG:
            continue
        if direction_segment == "SHORT" and kind is not DecisionType.CANDIDATE_SHORT:
            continue
        if confidence_band is not None and item.observation.confidence_band != confidence_band:
            continue
        selected.append(item)
    groups: dict[tuple[str, str, str | None], list[CalibrationRecord]] = {}
    names: dict[str, str | None] = {}
    for item in selected:
        strategy_id = item.observation.strategy_id or DATA_NOT_AVAILABLE
        key = (strategy_id, item.observation.validation_horizon, item.observation.strategy_name)
        groups.setdefault(key, []).append(item)
        names[strategy_id] = item.observation.strategy_name
    summaries = tuple(_summary(group, strategy_id, name, horizon, direction_segment, confidence_band) for (strategy_id, horizon, name), group in sorted(groups.items()))
    def rank_key(summary: StrategyPerformanceSummary):
        status_order = {"STRONGER_SAMPLE": 3, "MODERATE_SAMPLE": 2, "LIMITED_SAMPLE": 1, "INSUFFICIENT_SAMPLE": 0}
        return (-status_order[summary.sample_size_status.value], -(summary.expectancy_net_pnl or Decimal("-Infinity")), -(summary.net_pnl or Decimal("-Infinity")), -(summary.max_drawdown or Decimal("-Infinity")), -(summary.profit_factor or Decimal("-Infinity")), -(summary.directional_accuracy or Decimal("-Infinity")), summary.strategy_id, summary.validation_horizon)
    ranking = tuple((item.strategy_id, item.validation_horizon) for item in sorted(summaries, key=rank_key))
    return StrategyPerformanceReport(summaries, ranking, sum(item.observation.strategy_id is None for item in selected))


__all__ = ["StrategyPerformanceReport", "StrategyPerformanceSummary", "analyze_strategy_performance"]
