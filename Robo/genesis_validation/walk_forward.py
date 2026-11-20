"""Deterministic chronological walk-forward validation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Iterable

from .calibration import CalibrationRecord
from .contracts import DATA_NOT_AVAILABLE, DecisionType, SampleSizeStatus, classify_sample_size
from .labeling import DirectionLabel, ExecutionLabel


class WalkForwardMode(Enum):
    ROLLING = "ROLLING"
    EXPANDING = "EXPANDING"


@dataclass(frozen=True)
class WalkForwardConfig:
    train_window_size: int
    validation_window_size: int
    step_size: int
    mode: WalkForwardMode = WalkForwardMode.ROLLING
    gap_size: int = 0
    min_train_sample: int = 1
    min_validation_sample: int = 1
    purge_cross_boundary_outcomes: bool = True

    def __post_init__(self) -> None:
        for name in ("train_window_size", "validation_window_size", "step_size", "min_train_sample", "min_validation_sample"):
            if not isinstance(getattr(self, name), int) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if not isinstance(self.gap_size, int) or self.gap_size < 0:
            raise ValueError("gap_size must be non-negative")
        if not isinstance(self.mode, WalkForwardMode):
            raise ValueError("mode must be explicit WalkForwardMode")


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: int
    train_records: tuple[CalibrationRecord, ...]
    validation_records: tuple[CalibrationRecord, ...]
    train_start: object
    train_end: object
    validation_start: object
    validation_end: object
    fold_status: str


@dataclass(frozen=True)
class WalkForwardFoldResult:
    fold_id: int
    fold_status: str
    train_count: int
    validation_count: int
    sample_size_status: SampleSizeStatus
    directional_accuracy: Decimal | None
    win_rate: Decimal | None
    net_pnl: Decimal | None
    expectancy: Decimal | None
    average_return_pct: Decimal | None
    median_return_pct: Decimal | None
    profit_factor: Decimal | None
    max_drawdown: Decimal | None
    fees: Decimal | None
    slippage: Decimal | None


@dataclass(frozen=True)
class WalkForwardSummary:
    folds: tuple[WalkForwardFoldResult, ...]
    aggregate_net_pnl: Decimal | None
    aggregate_expectancy: Decimal | None
    aggregate_directional_accuracy: Decimal | None
    aggregate_trade_count: int
    positive_fold_count: int
    negative_fold_count: int
    stability_status: str
    deterioration_status: str


def _economic(rows: tuple[CalibrationRecord, ...]) -> list[Decimal]:
    return [row.observation.net_pnl for row in rows if row.observation.net_pnl is not None]


def _drawdown(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    equity = peak = max_dd = Decimal("0")
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return max_dd


def _fold_result(fold: WalkForwardFold) -> WalkForwardFoldResult:
    rows = fold.validation_records
    economics = _economic(rows)
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for row in rows)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for row in rows)
    returns = [row.observation.return_pct for row in rows if row.observation.return_pct is not None]
    fees = [row.observation.fees for row in rows if row.observation.fees is not None]
    slippage = [row.observation.slippage for row in rows if row.observation.slippage is not None]
    wins = sum(value > 0 for value in economics)
    losses = sum(value < 0 for value in economics)
    profit_factor = None if not economics else Decimal("Infinity") if losses == 0 and wins else None if losses == 0 else sum((value for value in economics if value > 0), Decimal("0")) / abs(sum((value for value in economics if value < 0), Decimal("0")))
    return WalkForwardFoldResult(
        fold.fold_id, fold.fold_status, len(fold.train_records), len(rows), classify_sample_size(len(rows)),
        Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None,
        Decimal(wins) / Decimal(len(economics)) if economics else None,
        sum(economics, Decimal("0")) if economics else None,
        sum(economics, Decimal("0")) / Decimal(len(economics)) if economics else None,
        sum(returns, Decimal("0")) / Decimal(len(returns)) if returns else None,
        Decimal(str(median(returns))) if returns else None, profit_factor, _drawdown(economics),
        sum(fees, Decimal("0")) if fees else None, sum(slippage, Decimal("0")) if slippage else None,
    )


def _outcome_end(row: CalibrationRecord):
    seconds = row.observation.holding_period_seconds
    return row.observation.timestamp_utc if seconds is None else row.observation.timestamp_utc + __import__("datetime").timedelta(seconds=float(seconds))


def build_walk_forward_folds(records: Iterable[CalibrationRecord], config: WalkForwardConfig, *, validation_horizon: str | None = None, strategy_id: str | None = None, direction: str | None = None, regime: str | None = None) -> tuple[WalkForwardFold, ...]:
    values = tuple(sorted(records, key=lambda row: (row.observation.timestamp_utc, row.observation.commander_decision_id)))
    horizons = {row.observation.validation_horizon for row in values}
    if validation_horizon is None:
        if len(horizons) > 1:
            raise ValueError("validation_horizon is required when multiple horizons are present")
        validation_horizon = next(iter(horizons), None)
    selected = [row for row in values if row.observation.validation_horizon == validation_horizon]
    if strategy_id is not None:
        selected = [row for row in selected if row.observation.strategy_id == strategy_id]
    if direction == "LONG":
        selected = [row for row in selected if row.observation.decision_type is DecisionType.CANDIDATE_LONG]
    elif direction == "SHORT":
        selected = [row for row in selected if row.observation.decision_type is DecisionType.CANDIDATE_SHORT]
    if regime is not None:
        selected = [row for row in selected if row.observation.market_regime == regime]
    rows = tuple(selected)
    folds: list[WalkForwardFold] = []
    anchor = 0
    fold_id = 0
    while True:
        train_start_index = 0 if config.mode is WalkForwardMode.EXPANDING else anchor
        train_end_index = anchor + config.train_window_size
        validation_start_index = train_end_index + config.gap_size
        validation_end_index = validation_start_index + config.validation_window_size
        if validation_end_index > len(rows):
            break
        train = list(rows[train_start_index:train_end_index])
        validation = list(rows[validation_start_index:validation_end_index])
        if config.purge_cross_boundary_outcomes and validation:
            validation_start = validation[0].observation.timestamp_utc
            train = [row for row in train if _outcome_end(row) <= validation_start]
        status = "PASS"
        if len(train) < config.min_train_sample:
            status = "INSUFFICIENT_TRAIN_SAMPLE"
        elif len(validation) < config.min_validation_sample:
            status = "INSUFFICIENT_VALIDATION_SAMPLE"
        folds.append(WalkForwardFold(fold_id, tuple(train), tuple(validation), train[0].observation.timestamp_utc if train else None, train[-1].observation.timestamp_utc if train else None, validation[0].observation.timestamp_utc if validation else None, validation[-1].observation.timestamp_utc if validation else None, status))
        fold_id += 1
        anchor += config.step_size
    return tuple(folds)


def run_walk_forward(records: Iterable[CalibrationRecord], config: WalkForwardConfig, **filters) -> WalkForwardSummary:
    folds = build_walk_forward_folds(records, config, **filters)
    results = tuple(_fold_result(fold) for fold in folds)
    valid = [result for result in results if result.fold_status == "PASS"]
    economics = [row.observation.net_pnl for fold in folds if fold.fold_status == "PASS" for row in fold.validation_records if row.observation.net_pnl is not None]
    correct = sum(row.label.direction_label is DirectionLabel.CORRECT_DIRECTION for fold in folds if fold.fold_status == "PASS" for row in fold.validation_records)
    wrong = sum(row.label.direction_label is DirectionLabel.WRONG_DIRECTION for fold in folds if fold.fold_status == "PASS" for row in fold.validation_records)
    signs = [item.net_pnl for item in valid if item.net_pnl is not None]
    if len(signs) < 2:
        stability = "INSUFFICIENT_DATA"
    elif all(value > 0 for value in signs) or all(value < 0 for value in signs):
        stability = "CONSISTENT"
    elif sum(value > 0 for value in signs) >= len(signs) / 2:
        stability = "MOSTLY_CONSISTENT"
    else:
        stability = "UNSTABLE"
    if len(valid) < 2:
        deterioration = "INSUFFICIENT_DATA"
    else:
        half = len(valid) // 2
        early = sum((item.expectancy for item in valid[:half] if item.expectancy is not None), Decimal("0")) / max(1, sum(item.expectancy is not None for item in valid[:half]))
        late = sum((item.expectancy for item in valid[half:] if item.expectancy is not None), Decimal("0")) / max(1, sum(item.expectancy is not None for item in valid[half:]))
        deterioration = "IMPROVING" if late > early else "DETERIORATING" if late < early else "STABLE"
    return WalkForwardSummary(results, sum(economics, Decimal("0")) if economics else None, sum(economics, Decimal("0")) / Decimal(len(economics)) if economics else None, Decimal(correct) / Decimal(correct + wrong) if correct + wrong else None, len(economics), sum(value > 0 for value in signs), sum(value < 0 for value in signs), stability, deterioration)


__all__ = ["WalkForwardConfig", "WalkForwardFold", "WalkForwardFoldResult", "WalkForwardMode", "WalkForwardSummary", "build_walk_forward_folds", "run_walk_forward"]
