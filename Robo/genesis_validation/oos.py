"""Untouched terminal out-of-sample validation for Sprint 9 Phase 11."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DecisionType, SampleSizeStatus, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


@dataclass(frozen=True)
class OOSConfig:
    policy: str
    terminal_fraction: Decimal | None = None
    terminal_count: int | None = None
    oos_start: datetime | None = None
    oos_end: datetime | None = None
    validation_count: int = 0

    def __post_init__(self) -> None:
        if self.policy not in {"FRACTION", "COUNT", "RANGE"}:
            raise ValueError("policy must be FRACTION, COUNT, or RANGE")
        if self.validation_count < 0:
            raise ValueError("validation_count must be non-negative")
        if self.policy == "FRACTION" and (self.terminal_fraction is None or not Decimal("0") < self.terminal_fraction < Decimal("1")):
            raise ValueError("terminal_fraction must be between 0 and 1")
        if self.policy == "COUNT" and (self.terminal_count is None or self.terminal_count <= 0):
            raise ValueError("terminal_count must be positive")
        if self.policy == "RANGE" and (self.oos_start is None or self.oos_end is None or self.oos_start >= self.oos_end):
            raise ValueError("explicit OOS range required")


@dataclass(frozen=True)
class OOSSplit:
    train_records: tuple[CalibrationRecord, ...]
    validation_records: tuple[CalibrationRecord, ...]
    oos_records: tuple[CalibrationRecord, ...]
    oos_start: datetime | None
    oos_end: datetime | None


@dataclass(frozen=True)
class OOSPerformanceSummary:
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
    expectancy: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    average_mfe: Decimal | None
    average_mae: Decimal | None


@dataclass(frozen=True)
class OOSGeneralizationSummary:
    generalization_status: str
    degradation_status: str
    oos: OOSPerformanceSummary
    reference_net_pnl: Decimal | None
    reference_expectancy: Decimal | None
    reference_directional_accuracy: Decimal | None


def split_oos(records: Iterable[CalibrationRecord], config: OOSConfig, *, validation_horizon: str | None = None) -> OOSSplit:
    values = tuple(sorted(records, key=lambda row: (row.observation.timestamp_utc, row.observation.commander_decision_id)))
    horizons = {row.observation.validation_horizon for row in values}
    if validation_horizon is None:
        if len(horizons) > 1:
            raise ValueError("validation_horizon is required for multiple horizons")
        validation_horizon = next(iter(horizons), None)
    values = tuple(row for row in values if row.observation.validation_horizon == validation_horizon)
    if config.policy == "FRACTION":
        count = max(1, int(len(values) * config.terminal_fraction))
        start_index = len(values) - count
    elif config.policy == "COUNT":
        count = config.terminal_count
        if count > len(values):
            raise ValueError("terminal_count exceeds dataset")
        start_index = len(values) - count
    else:
        selected = tuple(row for row in values if config.oos_start <= row.observation.timestamp_utc <= config.oos_end)
        if not selected:
            raise ValueError("explicit OOS range contains no records")
        start_index = values.index(selected[0])
        count = len(selected)
    oos = values[start_index:start_index + count]
    pre = values[:start_index]
    validation = pre[-config.validation_count:] if config.validation_count else tuple()
    train = pre[:-config.validation_count] if config.validation_count else pre
    if oos and pre and pre[-1].observation.timestamp_utc >= oos[0].observation.timestamp_utc:
        raise ValueError("OOS is not strictly terminal")
    return OOSSplit(train, validation, oos, oos[0].observation.timestamp_utc if oos else None, oos[-1].observation.timestamp_utc if oos else None)


def summarize_oos(records: Iterable[CalibrationRecord]) -> OOSPerformanceSummary:
    rows = tuple(records)
    economics = [row.observation.net_pnl for row in rows if row.observation.net_pnl is not None]
    returns = [row.observation.return_pct for row in rows if row.observation.return_pct is not None]
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in rows)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in rows)
    neutral = sum(row.label.direction_label is DirectionLabel.NEUTRAL for row in rows)
    unavailable = sum(row.label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE for row in rows)
    wins = sum(v > 0 for v in economics)
    losses = sum(v < 0 for v in economics)
    breakeven = sum(v == 0 for v in economics)
    fees = [row.observation.fees for row in rows if row.observation.fees is not None]
    slippage = [row.observation.slippage for row in rows if row.observation.slippage is not None]
    mfe = [row.observation.mfe for row in rows if row.observation.mfe is not None]
    mae = [row.observation.mae for row in rows if row.observation.mae is not None]
    gross = [row.observation.gross_pnl for row in rows if row.observation.gross_pnl is not None]
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for value in economics:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return OOSPerformanceSummary(
        len(rows), classify_sample_size(len(rows)), len(rows), sum(row.label.execution_label is ExecutionLabel.EXECUTED for row in rows),
        sum(row.label.execution_label is ExecutionLabel.NOT_EXECUTED for row in rows), correct, wrong, neutral, unavailable,
        Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None, wins, losses, breakeven,
        Decimal(wins) / Decimal(len(economics)) if economics else None, Decimal(losses) / Decimal(len(economics)) if economics else None,
        Decimal(breakeven) / Decimal(len(economics)) if economics else None, sum(gross, Decimal("0")) if gross else None,
        sum(fees, Decimal("0")) if fees else None, sum(slippage, Decimal("0")) if slippage else None,
        sum(economics, Decimal("0")) if economics else None, sum(economics, Decimal("0")) / Decimal(len(economics)) if economics else None,
        sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None, Decimal(str(median(returns))) if returns else None,
        (sum((v for v in economics if v > 0), Decimal("0")) / abs(sum((v for v in economics if v < 0), Decimal("0"))) if losses else Decimal("Infinity") if wins else None),
        max_dd if economics else None, sum(mfe, Decimal("0")) / Decimal(len(mfe)) if mfe else None, sum(mae, Decimal("0")) / Decimal(len(mae)) if mae else None,
    )


def assess_generalization(oos: OOSPerformanceSummary, *, reference_net_pnl: Decimal | None, reference_expectancy: Decimal | None, reference_directional_accuracy: Decimal | None) -> OOSGeneralizationSummary:
    if oos.sample_count < 1 or oos.expectancy is None or reference_expectancy is None:
        return OOSGeneralizationSummary("INSUFFICIENT_DATA", "INSUFFICIENT_DATA", oos, reference_net_pnl, reference_expectancy, reference_directional_accuracy)
    same_sign = (oos.expectancy >= 0) == (reference_expectancy >= 0)
    accuracy_ok = reference_directional_accuracy is None or oos.directional_accuracy is None or oos.directional_accuracy >= reference_directional_accuracy - Decimal("0.05")
    material_expectancy_degradation = same_sign and reference_expectancy != 0 and abs(oos.expectancy) < abs(reference_expectancy) / Decimal("2")
    if same_sign and accuracy_ok and not material_expectancy_degradation:
        general = "GENERALIZES"
    elif same_sign:
        general = "PARTIALLY_GENERALIZES"
    else:
        general = "FAILS_TO_GENERALIZE"
    if oos.expectancy > reference_expectancy and (reference_net_pnl is None or oos.net_pnl is None or oos.net_pnl >= reference_net_pnl):
        degradation = "IMPROVED"
    elif oos.expectancy == reference_expectancy:
        degradation = "STABLE"
    elif same_sign:
        degradation = "DEGRADED"
    else:
        degradation = "MIXED"
    return OOSGeneralizationSummary(general, degradation, oos, reference_net_pnl, reference_expectancy, reference_directional_accuracy)


__all__ = ["OOSConfig", "OOSGeneralizationSummary", "OOSPerformanceSummary", "OOSSplit", "assess_generalization", "split_oos", "summarize_oos"]
