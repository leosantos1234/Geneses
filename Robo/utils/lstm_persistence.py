"""Deterministic mapping of runtime LSTM output to DB fields."""

from __future__ import annotations

from typing import Any
import math


def map_prediction(runtime_prediction: Any, action: Any, real_price: Any) -> dict[str, Any]:
    """Map a prediction without turning missing data into a valid-looking zero."""
    candidate = runtime_prediction
    if candidate is None and isinstance(action, dict):
        candidate = action.get("predicted_price")
    try:
        predicted_price = float(candidate)
    except (TypeError, ValueError) as exc:
        raise ValueError("LSTM prediction missing or non-numeric") from exc
    if not math.isfinite(predicted_price):
        raise ValueError("LSTM prediction is not finite")

    explicit_direction = action.get("direction") if isinstance(action, dict) else None
    confidence_raw = action.get("confidence") if isinstance(action, dict) else None
    try:
        confidence_score = float(confidence_raw) if confidence_raw is not None else None
    except (TypeError, ValueError):
        confidence_score = None

    current_price = float(real_price)
    direction = explicit_direction or (
        "buy" if predicted_price > current_price
        else "sell" if predicted_price < current_price
        else "hold"
    )
    error_percent = abs((predicted_price - current_price) / current_price) * 100 if current_price else None
    return {
        "predicted_price": predicted_price,
        "direction_predicted": direction,
        "confidence_score": confidence_score,
        "error_percent": error_percent,
    }
