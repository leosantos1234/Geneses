"""Deterministic, read-only evaluation dataset construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .contracts import (
    DATA_NOT_AVAILABLE,
    DecisionType,
    ValidationObservation,
    _decimal,
    _utc,
)

NOT_APPLICABLE = "NOT_APPLICABLE"


def _get(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} required")
    return value.strip()


def _decision_type(value: Any) -> DecisionType:
    raw = getattr(value, "value", value)
    try:
        return DecisionType(raw)
    except ValueError:
        raise ValueError(f"unsupported decision type: {raw}") from None


def _source_confidence(value: Any) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, None
    raw = getattr(value, "value", value)
    if isinstance(raw, (Decimal, int, float)) and not isinstance(raw, bool):
        parsed = _decimal(raw, "confidence")
        assert parsed is not None
        if not Decimal("0") <= parsed <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        return parsed, None
    if isinstance(raw, str):
        try:
            parsed = _decimal(raw, "confidence")
        except ValueError:
            return None, raw
        assert parsed is not None
        if not Decimal("0") <= parsed <= Decimal("1"):
            raise ValueError("confidence must be between 0 and 1")
        return parsed, None
    raise ValueError("confidence must be numeric or a source categorical value")


def _source_time(value: Any, name: str) -> datetime:
    return _utc(value, name)


@dataclass(frozen=True)
class DatasetConfig:
    """Explicit horizon configuration; durations are supplied by the caller."""

    horizons: Mapping[str, timedelta]

    def __post_init__(self) -> None:
        if not self.horizons:
            raise ValueError("at least one validation horizon is required")
        normalized: dict[str, timedelta] = {}
        for name, duration in self.horizons.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("horizon name required")
            if not isinstance(duration, timedelta) or duration <= timedelta(0):
                raise ValueError("horizon duration must be positive")
            normalized[name.strip()] = duration
        object.__setattr__(self, "horizons", MappingProxyType(dict(sorted(normalized.items()))))


@dataclass(frozen=True)
class PaperOutcome:
    """Paper result source; fields are outcomes, never decision-time context."""

    commander_decision_id: str
    paper_execution_outcome: str = DATA_NOT_AVAILABLE
    paper_risk_outcome: str = DATA_NOT_AVAILABLE
    paper_order_status: str = NOT_APPLICABLE
    paper_fill_status: str = NOT_APPLICABLE
    paper_order_id: str | None = None
    paper_fill_id: str | None = None
    paper_effect_id: str | None = None
    fill_price: Decimal | str | int | float | None = None
    fees_paid: Decimal | str | int | float | None = None
    slippage_cost: Decimal | str | int | float | None = None
    net_pnl: Decimal | str | int | float | None = None
    return_pct: Decimal | str | int | float | None = None
    mfe: Decimal | str | int | float | None = None
    mae: Decimal | str | int | float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    outcome_timestamp_utc: datetime | None = None
    record_provenance: str = "UNKNOWN"

    def __post_init__(self) -> None:
        object.__setattr__(self, "commander_decision_id", _id(self.commander_decision_id, "commander_decision_id"))
        for name in ("paper_execution_outcome", "paper_risk_outcome", "paper_order_status", "paper_fill_status"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        for name in ("fill_price", "fees_paid", "slippage_cost", "net_pnl", "return_pct", "mfe", "mae"):
            value = _decimal(getattr(self, name), name, non_negative=name in {"fill_price", "fees_paid", "slippage_cost"})
            object.__setattr__(self, name, value)
        for name in ("opened_at", "closed_at", "outcome_timestamp_utc"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _source_time(value, name))
        if self.opened_at is not None and self.closed_at is not None and self.closed_at < self.opened_at:
            raise ValueError("closed_at must not precede opened_at")


@dataclass(frozen=True)
class EvaluationDataset:
    observations: tuple[ValidationObservation, ...]
    config: DatasetConfig

    @property
    def quality(self) -> dict[str, int]:
        values = [item.decision_type for item in self.observations]
        complete = sum(item.paper_execution_outcome != DATA_NOT_AVAILABLE for item in self.observations)
        return {
            "total_decisions": len(values),
            "candidate_long_count": values.count(DecisionType.CANDIDATE_LONG),
            "candidate_short_count": values.count(DecisionType.CANDIDATE_SHORT),
            "watch_count": values.count(DecisionType.WATCH),
            "no_action_count": values.count(DecisionType.NO_ACTION),
            "insufficient_data_count": values.count(DecisionType.INSUFFICIENT_DATA),
            "observations_with_complete_paper_outcome": complete,
            "observations_with_missing_paper_outcome": len(values) - complete,
            "observations_with_market_regime": sum(item.market_regime not in (None, DATA_NOT_AVAILABLE) for item in self.observations),
            "observations_without_market_regime": sum(item.market_regime in (None, DATA_NOT_AVAILABLE) for item in self.observations),
            "confidence_available_count": sum(item.confidence is not None or item.confidence_raw is not None for item in self.observations),
            "confidence_missing_count": sum(item.confidence is None and item.confidence_raw is None for item in self.observations),
            "strategy_available_count": sum(item.strategy_id is not None or item.strategy_name is not None for item in self.observations),
            "strategy_missing_count": sum(item.strategy_id is None and item.strategy_name is None for item in self.observations),
        }

    def to_json(self) -> str:
        import json
        return json.dumps([item.to_dict() for item in self.observations], sort_keys=True, separators=(",", ":"))


def _outcome_for(outcomes: Mapping[str, Any], decision_id: str, horizon: str) -> PaperOutcome | None:
    raw = outcomes.get(decision_id)
    if raw is None:
        return None
    if isinstance(raw, Mapping) and horizon in raw:
        raw = raw[horizon]
    if raw is None:
        return None
    if isinstance(raw, PaperOutcome):
        return raw
    if isinstance(raw, Mapping):
        return PaperOutcome(commander_decision_id=decision_id, **dict(raw))
    raise ValueError("paper_outcomes values must be PaperOutcome or mappings")


def build_evaluation_dataset(
    decisions: Iterable[Any],
    paper_outcomes: Mapping[str, Any],
    config: DatasetConfig,
    historical_data: Any = None,
    regime_source: Any = None,
) -> EvaluationDataset:
    """Build sorted observations without writes, labeling, or source mutation."""
    if historical_data is not None and not isinstance(historical_data, Mapping):
        raise ValueError("historical_data must be an explicit read-only mapping when supplied")
    rows: list[ValidationObservation] = []
    seen: set[tuple[str, str]] = set()
    for source in decisions:
        decision_id = _id(_get(source, "commander_decision_id"), "commander_decision_id")
        timestamp = _source_time(_get(source, "timestamp_utc"), "timestamp_utc")
        symbol = _get(source, "symbol") or _get(source, "subject")
        if not symbol:
            symbol = DATA_NOT_AVAILABLE
        kind = _decision_type(_get(source, "decision", _get(source, "decision_type")))
        confidence, confidence_raw = _source_confidence(_get(source, "confidence"))
        for horizon, duration in config.horizons.items():
            identity = (decision_id, horizon)
            if identity in seen:
                raise ValueError("duplicate observation identity")
            seen.add(identity)
            outcome = _outcome_for(paper_outcomes, decision_id, horizon)
            if outcome is not None and outcome.commander_decision_id != decision_id:
                raise ValueError("dataset lineage crossover")
            if outcome is not None and outcome.outcome_timestamp_utc is not None:
                cutoff = timestamp + duration
                if outcome.outcome_timestamp_utc > cutoff:
                    raise ValueError("outcome exceeds configured horizon")
            fill_price = None if outcome is None else outcome.fill_price
            holding = None
            if outcome is not None and outcome.opened_at is not None and outcome.closed_at is not None:
                holding = Decimal(str((outcome.closed_at - outcome.opened_at).total_seconds()))
            order_status = NOT_APPLICABLE if outcome is None else outcome.paper_order_status
            fill_status = NOT_APPLICABLE if outcome is None else outcome.paper_fill_status
            rows.append(ValidationObservation(
                commander_decision_id=decision_id,
                timestamp_utc=timestamp,
                symbol=str(symbol),
                decision_type=kind,
                confidence=confidence,
                confidence_raw=confidence_raw,
                strategy_id=_get(source, "strategy_id"),
                strategy_name=_get(source, "strategy_name"),
                reference_price=_get(source, "reference_price"),
                paper_execution_outcome=DATA_NOT_AVAILABLE if outcome is None else outcome.paper_execution_outcome,
                risk_outcome=DATA_NOT_AVAILABLE if outcome is None else outcome.paper_risk_outcome,
                fill_outcome=fill_status,
                net_pnl=None if outcome is None else outcome.net_pnl,
                return_pct=None if outcome is None else outcome.return_pct,
                fees=None if outcome is None else outcome.fees_paid,
                slippage=None if outcome is None else outcome.slippage_cost,
                mfe=None if outcome is None else outcome.mfe,
                mae=None if outcome is None else outcome.mae,
                holding_period_seconds=holding,
                market_regime=_get(source, "market_regime"),
                validation_horizon=horizon,
                source_data_status="SOURCE_DATA_AVAILABLE" if outcome is not None else DATA_NOT_AVAILABLE,
                paper_order_id=None if outcome is None else outcome.paper_order_id,
                paper_fill_id=None if outcome is None else outcome.paper_fill_id,
                paper_effect_id=None if outcome is None else outcome.paper_effect_id,
                fill_price=fill_price,
            ))
    rows.sort(key=lambda item: (item.timestamp_utc, item.commander_decision_id, item.validation_horizon))
    return EvaluationDataset(tuple(rows), config)


__all__ = ["DatasetConfig", "EvaluationDataset", "NOT_APPLICABLE", "PaperOutcome", "build_evaluation_dataset"]
