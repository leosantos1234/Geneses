"""Deterministic descriptive Commander error analysis."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from decimal import Decimal
from typing import Iterable, Mapping

from .calibration import CalibrationRecord
from .labeling import DirectionLabel, ExecutionLabel


class ErrorClass(Enum):
    WRONG_DIRECTION = "WRONG_DIRECTION"
    LOW_CONFIDENCE_ERROR = "LOW_CONFIDENCE_ERROR"
    HIGH_CONFIDENCE_ERROR = "HIGH_CONFIDENCE_ERROR"
    RISK_REJECTED_CORRECT_DIRECTION = "RISK_REJECTED_CORRECT_DIRECTION"
    RISK_REJECTED_WRONG_DIRECTION = "RISK_REJECTED_WRONG_DIRECTION"
    EXECUTED_LOSS = "EXECUTED_LOSS"
    MISSED_OPPORTUNITY = "MISSED_OPPORTUNITY"
    COST_ERODED_EDGE = "COST_ERODED_EDGE"
    REGIME_MISMATCH = "REGIME_MISMATCH"
    TIME_SEGMENT_WEAKNESS = "TIME_SEGMENT_WEAKNESS"
    VOLATILITY_SEGMENT_WEAKNESS = "VOLATILITY_SEGMENT_WEAKNESS"
    STRATEGY_SPECIFIC_WEAKNESS = "STRATEGY_SPECIFIC_WEAKNESS"
    LONG_SPECIFIC_WEAKNESS = "LONG_SPECIFIC_WEAKNESS"
    SHORT_SPECIFIC_WEAKNESS = "SHORT_SPECIFIC_WEAKNESS"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    BENCHMARK_UNDERPERFORMANCE = "BENCHMARK_UNDERPERFORMANCE"


@dataclass(frozen=True)
class CommanderErrorRecord:
    commander_decision_id: str
    validation_horizon: str
    strategy_id: str | None
    direction: str
    confidence_band: str | None
    market_regime: str | None
    time_bucket: str | None
    volatility_segment: str | None
    error_classes: tuple[ErrorClass, ...]
    severity: str
    direction_label: str
    execution_label: str
    net_pnl: Decimal | None
    return_pct: Decimal | None
    fees: Decimal | None
    slippage: Decimal | None
    walk_forward_persistence: str
    oos_persistence: str
    source_data_status: str


@dataclass(frozen=True)
class CommanderErrorSummary:
    records: tuple[CommanderErrorRecord, ...]
    class_counts: Mapping[str, int]
    class_denominators: Mapping[str, int]
    class_rates: Mapping[str, Decimal | None]
    concentration: Mapping[str, tuple[tuple[str, int], ...]]


def _direction(record: CalibrationRecord) -> str:
    return "LONG" if record.observation.decision_type.value == "CANDIDATE_LONG" else "SHORT"


def _classify(item: CalibrationRecord, *, benchmark_underperformance: bool = False) -> tuple[ErrorClass, ...]:
    obs = item.observation
    label = item.label
    classes: list[ErrorClass] = []
    if label.direction_label is DirectionLabel.WRONG_DIRECTION:
        classes.append(ErrorClass.WRONG_DIRECTION)
        if obs.confidence_band == "0.80-1.00":
            classes.append(ErrorClass.HIGH_CONFIDENCE_ERROR)
        elif obs.confidence_band in {"0.00-0.20", "0.20-0.40"}:
            classes.append(ErrorClass.LOW_CONFIDENCE_ERROR)
    if label.execution_label is ExecutionLabel.NOT_EXECUTED and label.direction_label is DirectionLabel.CORRECT_DIRECTION and obs.risk_outcome.upper() in {"REJECT", "REJECTED"}:
        classes.append(ErrorClass.RISK_REJECTED_CORRECT_DIRECTION)
    if label.execution_label is ExecutionLabel.NOT_EXECUTED and label.direction_label is DirectionLabel.WRONG_DIRECTION and obs.risk_outcome.upper() in {"REJECT", "REJECTED"}:
        classes.append(ErrorClass.RISK_REJECTED_WRONG_DIRECTION)
    if label.execution_label is ExecutionLabel.EXECUTED and obs.net_pnl is not None and obs.net_pnl < 0:
        classes.append(ErrorClass.EXECUTED_LOSS)
    if obs.gross_pnl is not None and obs.net_pnl is not None and obs.gross_pnl > 0 and obs.net_pnl <= 0:
        classes.append(ErrorClass.COST_ERODED_EDGE)
    if label.execution_label is ExecutionLabel.NOT_EXECUTED and label.direction_label is DirectionLabel.CORRECT_DIRECTION and obs.source_data_status == "MISSED_OPPORTUNITY_ELIGIBLE":
        classes.append(ErrorClass.MISSED_OPPORTUNITY)
    if benchmark_underperformance:
        classes.append(ErrorClass.BENCHMARK_UNDERPERFORMANCE)
    if label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE:
        classes.append(ErrorClass.DATA_QUALITY_FAILURE)
    if not classes:
        classes.append(ErrorClass.INSUFFICIENT_DATA if label.direction_label is DirectionLabel.DATA_NOT_AVAILABLE else ErrorClass.INSUFFICIENT_DATA)
    return tuple(dict.fromkeys(classes))


def _severity(item: CalibrationRecord, classes: tuple[ErrorClass, ...]) -> str:
    if ErrorClass.HIGH_CONFIDENCE_ERROR in classes or (item.observation.net_pnl is not None and item.observation.net_pnl <= Decimal("-10")):
        return "HIGH"
    if ErrorClass.EXECUTED_LOSS in classes or ErrorClass.COST_ERODED_EDGE in classes:
        return "MEDIUM"
    return "LOW"


def analyze_errors(records: Iterable[CalibrationRecord], *, benchmark_underperformance_ids: set[str] | None = None, walk_forward_persistence: Mapping[str, str] | None = None, oos_persistence: Mapping[str, str] | None = None) -> CommanderErrorSummary:
    benchmark_underperformance_ids = benchmark_underperformance_ids or set()
    walk_forward_persistence = walk_forward_persistence or {}
    oos_persistence = oos_persistence or {}
    result: list[CommanderErrorRecord] = []
    for item in records:
        if item.observation.decision_type.value not in {"CANDIDATE_LONG", "CANDIDATE_SHORT"}:
            continue
        classes = _classify(item, benchmark_underperformance=item.observation.commander_decision_id in benchmark_underperformance_ids)
        obs = item.observation
        result.append(CommanderErrorRecord(obs.commander_decision_id, obs.validation_horizon, obs.strategy_id, _direction(item), obs.confidence_band, obs.market_regime, obs.time_bucket, obs.volatility_band, classes, _severity(item, classes), item.label.direction_label.value, item.label.execution_label.value, obs.net_pnl, obs.return_pct, obs.fees, obs.slippage, walk_forward_persistence.get(obs.commander_decision_id, "INSUFFICIENT_DATA"), oos_persistence.get(obs.commander_decision_id, "INSUFFICIENT_DATA"), obs.source_data_status))
    result.sort(key=lambda item: (item.validation_horizon, item.commander_decision_id))
    counts = {error.value: sum(error in record.error_classes for record in result) for error in ErrorClass}
    denominators = {error.value: len(result) for error in ErrorClass}
    rates = {name: (Decimal(count) / Decimal(denominators[name]) if denominators[name] else None) for name, count in counts.items()}
    concentration = {
        "strategy": tuple(sorted(((key or "DATA_NOT_AVAILABLE", sum(key == record.strategy_id for record in result)) for key in {record.strategy_id for record in result}), key=lambda item: (-item[1], item[0]))),
        "direction": tuple(sorted(((key, sum(key == record.direction for record in result)) for key in {record.direction for record in result}), key=lambda item: (-item[1], item[0]))),
        "regime": tuple(sorted(((key or "DATA_NOT_AVAILABLE", sum(key == record.market_regime for record in result)) for key in {record.market_regime for record in result}), key=lambda item: (-item[1], item[0]))),
    }
    return CommanderErrorSummary(tuple(result), counts, denominators, rates, concentration)


__all__ = ["CommanderErrorRecord", "CommanderErrorSummary", "ErrorClass", "analyze_errors"]
