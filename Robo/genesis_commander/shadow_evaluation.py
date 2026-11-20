"""Pure, historical-only evaluation of Commander shadow decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

from .contract import ShadowEvaluation

DATA_NOT_AVAILABLE = "DATA_NOT_AVAILABLE"
_HORIZON_RE = re.compile(r"^(?P<value>[1-9][0-9]*)\s*(?P<unit>m|min|h|d)$", re.IGNORECASE)
_DIRECTIONAL_DECISIONS = {"CANDIDATE_LONG", "CANDIDATE_SHORT"}


@dataclass(frozen=True)
class PricePoint:
    """A timestamped historical price observation."""

    timestamp: datetime
    price: float

    def __post_init__(self) -> None:
        _require_utc(self.timestamp, "price timestamp")
        if not isinstance(self.price, (int, float)) or not isfinite(float(self.price)) or float(self.price) <= 0:
            raise ValueError("price must be a finite positive number")


def _require_utc(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{name} must be timezone-aware UTC")


def _parse_horizon(value: Union[str, int, float, timedelta]) -> Tuple[str, timedelta]:
    if isinstance(value, timedelta):
        duration = value
        label = f"{int(duration.total_seconds())}s"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        duration = timedelta(seconds=float(value))
        label = f"{int(duration.total_seconds())}s"
    elif isinstance(value, str):
        match = _HORIZON_RE.fullmatch(value.strip())
        if not match:
            raise ValueError("evaluation_horizon must be a positive duration such as 1h or 1d")
        amount = int(match.group("value"))
        unit = match.group("unit").lower()
        seconds = amount * {"m": 60, "min": 60, "h": 3600, "d": 86400}[unit]
        duration = timedelta(seconds=seconds)
        label = f"{amount}{unit}"
    else:
        raise ValueError("evaluation_horizon must be a positive duration")
    if duration <= timedelta(0):
        raise ValueError("evaluation_horizon must be positive")
    return label, duration


def _coerce_point(value: Union[PricePoint, Mapping[str, Any]]) -> PricePoint:
    if isinstance(value, PricePoint):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("price window items must be PricePoint or mapping")
    timestamp = value.get("timestamp")
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return PricePoint(timestamp=timestamp, price=float(value["price"]))


def _decision_fields(decision: Any) -> Tuple[str, datetime, str, Optional[str]]:
    if isinstance(decision, Mapping):
        decision_id = str(decision.get("commander_decision_id") or decision.get("decision_id") or "")
        timestamp = decision.get("timestamp_utc") or decision.get("timestamp") or decision.get("decision_timestamp")
        decision_type = decision.get("decision")
        symbol = decision.get("symbol")
    else:
        decision_id = str(getattr(decision, "commander_decision_id", ""))
        timestamp = getattr(decision, "timestamp_utc", None) or getattr(decision, "decision_timestamp", None)
        decision_type = getattr(decision, "decision", "")
        symbol = getattr(decision, "symbol", None)
    if isinstance(timestamp, str):
        timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    _require_utc(timestamp, "decision timestamp")
    if hasattr(decision_type, "value"):
        decision_type = decision_type.value
    decision_type = str(decision_type)
    if not decision_id:
        raise ValueError("decision_id required")
    return decision_id, timestamp, decision_type, symbol or None


def _not_available(decision_id: str, timestamp: datetime, decision: str, symbol: Optional[str], label: str, notes: str) -> ShadowEvaluation:
    return ShadowEvaluation(
        decision_id=decision_id,
        decision_timestamp=timestamp,
        decision=decision,
        symbol=symbol,
        evaluation_horizon=label,
        notes=notes,
    )


def evaluate_shadow_decision(
    decision: Any,
    price_window: Sequence[Union[PricePoint, Mapping[str, Any]]],
    evaluation_horizon: Union[str, int, float, timedelta],
) -> ShadowEvaluation:
    """Evaluate one persisted decision using only its permitted future window."""
    decision_id, decision_ts, decision_type, symbol = _decision_fields(decision)
    label, duration = _parse_horizon(evaluation_horizon)
    points = sorted((_coerce_point(item) for item in price_window), key=lambda point: point.timestamp)
    end_ts = decision_ts + duration
    allowed = [point for point in points if decision_ts <= point.timestamp <= end_ts]

    if decision_type not in _DIRECTIONAL_DECISIONS:
        return _not_available(decision_id, decision_ts, decision_type, symbol, label, "Direction is not applicable for this decision")
    if not allowed:
        return _not_available(decision_id, decision_ts, decision_type, symbol, label, "No price observations in the permitted evaluation window")

    reference = allowed[0]
    outcome = allowed[-1]
    note = "Evaluation window incomplete; latest permitted observation used" if outcome.timestamp < end_ts else None
    reference_price = float(reference.price)
    outcome_price = float(outcome.price)
    returns = (outcome_price - reference_price) / reference_price * 100.0
    changes = [(point.price - reference_price) / reference_price * 100.0 for point in allowed]
    if decision_type == "CANDIDATE_SHORT":
        favorable = [-value for value in changes]
        adverse = [-value for value in changes]
        direction_correct = returns < 0
    else:
        favorable = changes
        adverse = changes
        direction_correct = returns > 0

    return ShadowEvaluation(
        decision_id=decision_id,
        decision_timestamp=decision_ts,
        decision=decision_type,
        symbol=symbol,
        reference_price=reference_price,
        evaluation_horizon=label,
        outcome_timestamp=outcome.timestamp,
        outcome_price=outcome_price,
        return_pct=returns,
        direction_correct=direction_correct,
        max_favorable_excursion=max(favorable),
        max_adverse_excursion=min(adverse),
        notes=note,
    )

