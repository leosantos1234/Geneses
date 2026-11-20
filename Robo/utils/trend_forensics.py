"""Non-blocking forensic snapshot of the effective Breakout trend gate."""
from __future__ import annotations

import math
import os
from typing import Any


def _number(value: Any) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _last(df: Any, name: str, fallback: Any = None) -> float | None:
    try:
        if name in df.columns:
            value = df[name].iloc[-1]
            number = _number(value)
            if number is not None:
                return number
    except Exception:
        pass
    return _number(fallback)


def build_trend_forensics(df: Any, direction: str | None, *, state: str,
                          failure_reason: str | None = None) -> dict[str, Any]:
    """Mirror the production ``autorizar_score_breakout`` EMA inputs.

    Production authorization uses EMA50 and EMA200 only.  No slope or
    tolerance participates in that decision, so those fields remain NULL.
    """
    side = str(direction or "").upper()
    expected = "BUY" if side in {"BUY", "LONG"} else "SELL" if side in {"SELL", "SHORT"} else None
    close = _last(df, "close")
    short = _last(df, "EMA50", close)
    long = _last(df, "EMA200", close)
    short_period = int(os.getenv("EMA_50", "50"))
    long_period = int(os.getenv("EMA_TREND", os.getenv("EMA_LONGA", "200")))
    final = None
    if expected == "BUY" and short is not None and long is not None:
        final = short > long
    elif expected == "SELL" and short is not None and long is not None:
        final = short < long
    distance = None
    if short is not None and long not in (None, 0):
        distance = (short - long) / abs(long) * 100
    return {
        "trend_ema_short_period": short_period,
        "trend_ema_medium_period": None,
        "trend_ema_long_period": long_period,
        "trend_ema_short_value": short,
        "trend_ema_medium_value": None,
        "trend_ema_long_value": long,
        "trend_ema_short_slope": None,
        "trend_ema_medium_slope": None,
        "trend_ema_long_slope": None,
        "trend_direction_expected": expected,
        "trend_condition_short_medium": None,
        "trend_condition_medium_long": None,
        "trend_condition_final": final if state in {"PASS", "FAIL"} else None,
        "trend_distance_short_medium_pct": None,
        "trend_distance_medium_long_pct": distance,
        "trend_state": state,
        "trend_failure_reason": failure_reason,
    }


__all__ = ["build_trend_forensics"]
