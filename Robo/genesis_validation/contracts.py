"""Canonical, deterministic, read-only validation contracts for Sprint 9.

This module describes observations and dataset policy.  It does not run the
Commander, change strategy configuration, train models, or write execution
state.  Missing optional source data is represented explicitly rather than
being fabricated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from typing import Any

from genesis_paper.metrics import MetricValue


DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
AUTO_PROMOTION = False
SUPPORTED_HORIZONS = ("H1", "H2", "H3")
PRIMARY_ECONOMIC_METRICS = (
    "closed_trade_count", "net_pnl", "total_return_pct", "win_rate", "loss_rate",
    "breakeven_rate", "profit_factor", "expectancy", "average_trade_return",
    "median_trade_return", "max_drawdown", "max_drawdown_pct", "fees_paid",
    "slippage_cost", "gross_exposure", "net_exposure",
)
SIGNAL_QUALITY_METRICS = ("directional_accuracy", "net_expectancy", "win_rate")
BASELINE_CONTRACT = ("NO_ACTION_CASH", "SIMPLE_DIRECTIONAL", "BUY_AND_HOLD", "SEEDED_RANDOM")


class _ValueEnum(Enum):
    def __str__(self) -> str:
        return self.value


class DecisionType(_ValueEnum):
    NO_ACTION = "NO_ACTION"
    WATCH = "WATCH"
    CANDIDATE_LONG = "CANDIDATE_LONG"
    CANDIDATE_SHORT = "CANDIDATE_SHORT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ValidationLabel(_ValueEnum):
    CORRECT = "CORRECT"
    INCORRECT = "INCORRECT"
    BREAKEVEN = "BREAKEVEN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DATA_NOT_AVAILABLE = DATA_NOT_AVAILABLE


class PromotionState(_ValueEnum):
    REJECT = "REJECT"
    KEEP_SHADOW = "KEEP_SHADOW"
    NEEDS_IMPROVEMENT = "NEEDS_IMPROVEMENT"
    CANDIDATE_FOR_NEXT_STAGE = "CANDIDATE_FOR_NEXT_STAGE"


class SampleSizeStatus(_ValueEnum):
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    LIMITED_SAMPLE = "LIMITED_SAMPLE"
    MODERATE_SAMPLE = "MODERATE_SAMPLE"
    STRONGER_SAMPLE = "STRONGER_SAMPLE"


def _decimal(value: Any, name: str, *, non_negative: bool = False) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be Decimal-compatible")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name} must be Decimal-compatible") from None
    if not parsed.is_finite():
        raise ValueError(f"{name} must be finite")
    if non_negative and parsed < 0:
        raise ValueError(f"{name} must not be negative")
    return parsed


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be UTC")
    return value.astimezone(timezone.utc)


def _required(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} required")
    return value.strip()


def classify_sample_size(decision_count: int) -> SampleSizeStatus:
    if not isinstance(decision_count, int) or isinstance(decision_count, bool) or decision_count < 0:
        raise ValueError("decision_count must be a non-negative integer")
    if decision_count < 30:
        return SampleSizeStatus.INSUFFICIENT_SAMPLE
    if decision_count < 100:
        return SampleSizeStatus.LIMITED_SAMPLE
    if decision_count < 300:
        return SampleSizeStatus.MODERATE_SAMPLE
    return SampleSizeStatus.STRONGER_SAMPLE


@dataclass(frozen=True)
class HorizonSpec:
    """A deterministic evaluation horizon; no implicit project timeframe."""

    name: str
    duration: timedelta

    def __post_init__(self) -> None:
        _required(self.name, "name")
        if not isinstance(self.duration, timedelta) or self.duration <= timedelta(0):
            raise ValueError("duration must be positive")


@dataclass(frozen=True)
class TemporalSplit:
    """Strict chronological TRAIN -> VALIDATION -> OUT_OF_SAMPLE_TEST split."""

    train_end_utc: datetime
    validation_end_utc: datetime
    test_end_utc: datetime

    def __post_init__(self) -> None:
        train_end = _utc(self.train_end_utc, "train_end_utc")
        validation_end = _utc(self.validation_end_utc, "validation_end_utc")
        test_end = _utc(self.test_end_utc, "test_end_utc")
        if not train_end < validation_end < test_end:
            raise ValueError("temporal split must satisfy train < validation < test")
        object.__setattr__(self, "train_end_utc", train_end)
        object.__setattr__(self, "validation_end_utc", validation_end)
        object.__setattr__(self, "test_end_utc", test_end)

    def partition(self, timestamp_utc: datetime) -> str:
        timestamp = _utc(timestamp_utc, "timestamp_utc")
        if timestamp <= self.train_end_utc:
            return "TRAIN"
        if timestamp <= self.validation_end_utc:
            return "VALIDATION"
        if timestamp <= self.test_end_utc:
            return "OUT_OF_SAMPLE_TEST"
        raise ValueError("timestamp is outside split")


@dataclass(frozen=True)
class ValidationObservation:
    """One Commander decision mapped to one paper outcome observation."""

    commander_decision_id: str
    timestamp_utc: datetime
    symbol: str
    decision_type: DecisionType
    confidence: Decimal | str | int | float | None = None
    confidence_raw: str | None = None
    strategy_id: str | None = None
    strategy_name: str | None = None
    reference_price: Decimal | str | int | float | None = None
    paper_execution_outcome: str = DATA_NOT_AVAILABLE
    risk_outcome: str = DATA_NOT_AVAILABLE
    fill_outcome: str = DATA_NOT_AVAILABLE
    net_pnl: Decimal | str | int | float | None = None
    return_pct: Decimal | str | int | float | None = None
    fees: Decimal | str | int | float | None = None
    slippage: Decimal | str | int | float | None = None
    mfe: Decimal | str | int | float | None = None
    mae: Decimal | str | int | float | None = None
    holding_period_seconds: Decimal | str | int | float | None = None
    market_regime: str | None = None
    volatility_regime: str | None = None
    session: str | None = None
    trend_state: str | None = None
    validation_label: ValidationLabel | None = None
    validation_horizon: str = "H1"
    source_data_status: str = "SOURCE_DATA_AVAILABLE"
    paper_order_id: str | None = None
    paper_fill_id: str | None = None
    paper_effect_id: str | None = None
    fill_price: Decimal | str | int | float | None = None
    paper_only: bool = True
    gross_pnl: Decimal | str | int | float | None = None
    gross_exposure: Decimal | str | int | float | None = None
    net_exposure: Decimal | str | int | float | None = None
    turnover: Decimal | str | int | float | None = None
    confidence_band: str | None = None
    volatility: Decimal | str | int | float | None = None
    volatility_band: str | None = None
    time_bucket: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "commander_decision_id", _required(self.commander_decision_id, "commander_decision_id"))
        object.__setattr__(self, "timestamp_utc", _utc(self.timestamp_utc, "timestamp_utc"))
        object.__setattr__(self, "symbol", _required(self.symbol, "symbol"))
        if not isinstance(self.decision_type, DecisionType):
            raise ValueError("decision_type invalid")
        if self.confidence is not None:
            confidence = _decimal(self.confidence, "confidence")
            assert confidence is not None
            if not Decimal("0") <= confidence <= Decimal("1"):
                raise ValueError("confidence must be between 0 and 1")
            object.__setattr__(self, "confidence", confidence)
        if self.confidence_raw is not None and not isinstance(self.confidence_raw, str):
            raise ValueError("confidence_raw must be a string")
        if not isinstance(self.validation_horizon, str) or not self.validation_horizon.strip():
            raise ValueError("validation_horizon required")
        object.__setattr__(self, "validation_horizon", self.validation_horizon.strip())
        if not isinstance(self.source_data_status, str) or not self.source_data_status.strip():
            raise ValueError("source_data_status required")
        if self.paper_only is not True:
            raise ValueError("paper_only must be True")
        for name in ("reference_price", "net_pnl", "return_pct", "fees", "slippage", "mfe", "mae", "holding_period_seconds", "gross_pnl", "gross_exposure", "net_exposure", "turnover", "volatility"):
            parsed = _decimal(getattr(self, name), name, non_negative=name in {"reference_price", "fees", "slippage", "holding_period_seconds"})
            object.__setattr__(self, name, parsed)
        parsed_fill_price = _decimal(self.fill_price, "fill_price", non_negative=True)
        object.__setattr__(self, "fill_price", parsed_fill_price)
        for name in ("paper_execution_outcome", "risk_outcome", "fill_outcome"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if self.validation_label is not None and not isinstance(self.validation_label, ValidationLabel):
            raise ValueError("validation_label invalid")

    @property
    def derived_label(self) -> ValidationLabel:
        if self.decision_type in {DecisionType.NO_ACTION, DecisionType.WATCH, DecisionType.INSUFFICIENT_DATA}:
            return ValidationLabel.NOT_APPLICABLE
        if self.net_pnl is None:
            return ValidationLabel.DATA_NOT_AVAILABLE
        if self.net_pnl == 0:
            return ValidationLabel.BREAKEVEN
        if self.decision_type is DecisionType.CANDIDATE_LONG:
            return ValidationLabel.CORRECT if self.net_pnl > 0 else ValidationLabel.INCORRECT
        return ValidationLabel.CORRECT if self.net_pnl < 0 else ValidationLabel.INCORRECT

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, value in asdict(self).items():
            if isinstance(value, datetime):
                result[name] = value.isoformat()
            elif isinstance(value, Decimal):
                result[name] = format(value, "f")
            elif isinstance(value, Enum):
                result[name] = value.value
            elif value is None:
                result[name] = DATA_NOT_AVAILABLE
            else:
                result[name] = value
        result["derived_label"] = self.derived_label.value
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ValidationRunSummary:
    observation_count: int
    sample_size_status: SampleSizeStatus
    promotion_state: PromotionState
    auto_promotion: bool = AUTO_PROMOTION
    validation_future_leakage_allowed: bool = False
    validation_mutates_source_decisions: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.observation_count, int) or self.observation_count < 0:
            raise ValueError("observation_count must be non-negative integer")
        expected = classify_sample_size(self.observation_count)
        if self.sample_size_status is not expected:
            raise ValueError("sample_size_status does not match observation_count")
        if not isinstance(self.promotion_state, PromotionState):
            raise ValueError("promotion_state invalid")
        if self.auto_promotion is not False:
            raise ValueError("auto_promotion must be disabled")
        if self.validation_future_leakage_allowed is not False:
            raise ValueError("future leakage must be disabled")
        if self.validation_mutates_source_decisions is not False:
            raise ValueError("source decision mutation must be disabled")


__all__ = [
    "AUTO_PROMOTION", "BASELINE_CONTRACT", "DATA_NOT_AVAILABLE", "DecisionType", "HorizonSpec", "MetricValue",
    "PRIMARY_ECONOMIC_METRICS", "SIGNAL_QUALITY_METRICS", "SUPPORTED_HORIZONS",
    "PromotionState", "SampleSizeStatus", "TemporalSplit", "ValidationLabel",
    "ValidationObservation", "ValidationRunSummary", "classify_sample_size",
]
