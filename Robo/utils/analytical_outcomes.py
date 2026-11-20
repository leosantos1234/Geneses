"""Prospective outcome analytics for Analytical Telemetry V2.

This module is deliberately detached from trading execution.  It only reads
closed canonical candles and observed Breakout events, then monotonically
fills outcomes once their future horizons are knowable.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from genesis_runtime import connect_operational_database

HORIZONS = (1, 3, 6, 12)
OUTCOME_CLASSES = ("PROTECTED_CAPITAL", "MISSED_OPPORTUNITY", "NEUTRAL", "INSUFFICIENT_DATA")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _direction(direction: Any) -> str | None:
    value = str(direction or "").upper()
    return value if value in {"BUY", "SELL", "LONG", "SHORT", "UNKNOWN"} else None


def _is_buy(direction: str | None) -> bool:
    return direction in {"BUY", "LONG"}


def _is_sell(direction: str | None) -> bool:
    return direction in {"SELL", "SHORT"}


def _close_time(candle: dict[str, Any]) -> datetime | None:
    value = candle.get("candle_close_time")
    return value if isinstance(value, datetime) else None


def calculate_horizon_metrics(event_close: Any, direction: Any,
                              future_candles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Calculate only horizons with N closed future candles available.

    ``future_candles`` must contain candles strictly after the event and must
    already be limited to closed candles by the caller.
    """
    entry = _number(event_close)
    side = _direction(direction)
    result: dict[str, Any] = {}
    for horizon in HORIZONS:
        prefix = f"{horizon}"
        fields = (f"forward_return_{horizon}", f"mfe_pct_{horizon}",
                  f"mae_pct_{horizon}", f"forward_return_{horizon}_available_at")
        if entry is None or not (_is_buy(side) or _is_sell(side)) or len(future_candles) < horizon:
            result.update({field: None for field in fields})
            continue
        window = future_candles[:horizon]
        closes = [_number(row.get("close")) for row in window]
        highs = [_number(row.get("high")) for row in window]
        lows = [_number(row.get("low")) for row in window]
        if any(value is None for value in (*closes, *highs, *lows)):
            result.update({field: None for field in fields})
            continue
        close = closes[-1]
        if _is_buy(side):
            forward = (close - entry) / entry * 100
            mfe = (max(highs) - entry) / entry * 100
            mae = (min(lows) - entry) / entry * 100
        else:
            forward = (entry - close) / entry * 100
            mfe = (entry - min(lows)) / entry * 100
            mae = (entry - max(highs)) / entry * 100
        result[f"forward_return_{horizon}"] = forward
        result[f"mfe_pct_{horizon}"] = mfe
        result[f"mae_pct_{horizon}"] = mae
        result[f"forward_return_{horizon}_available_at"] = _close_time(window[-1])
    return result


def classify_outcome(metrics: dict[str, Any], direction: Any,
                     threshold_pct: float | None = None) -> str:
    """Classify a blocked event for analytics, never for execution.

    The default threshold is zero: the first mature directional return above
    zero is a descriptive MISSED_OPPORTUNITY, below zero is PROTECTED_CAPITAL,
    and exactly zero is NEUTRAL.  It is configurable only through the
    analytics function/environment and is never read by strategy code.
    """
    side = _direction(direction)
    if not (_is_buy(side) or _is_sell(side)):
        return "INSUFFICIENT_DATA"
    if threshold_pct is None:
        threshold_pct = _number(os.getenv("GENESIS_ANALYTICAL_OUTCOME_THRESHOLD_PCT", "0")) or 0.0
    values = [metrics.get(f"forward_return_{horizon}") for horizon in HORIZONS]
    values = [value for value in values if _number(value) is not None]
    if not values:
        return "INSUFFICIENT_DATA"
    value = values[-1]
    if value > threshold_pct:
        return "MISSED_OPPORTUNITY"
    if value < -abs(threshold_pct):
        return "PROTECTED_CAPITAL"
    return "NEUTRAL"


def build_outcome(event: dict[str, Any], future_candles: Sequence[dict[str, Any]],
                  *, now: datetime | None = None, threshold_pct: float | None = None) -> dict[str, Any]:
    """Build an idempotent outcome payload without writing anywhere."""
    metrics = calculate_horizon_metrics(event.get("event_close"), event.get("direction"), future_candles)
    available = [metrics.get(f"forward_return_{horizon}_available_at") for horizon in HORIZONS]
    available = [value for value in available if value is not None]
    payload = {
        "id": f"{event['event_type']}:{event['event_id']}",
        "event_type": event["event_type"], "event_id": str(event["event_id"]),
        "symbol": str(event["symbol"]).upper(), "timeframe": str(event["timeframe"]),
        "event_candle_open_time": event["event_candle_open_time"],
        "event_candle_close_time": event.get("event_candle_close_time"),
        "direction": _direction(event.get("direction")), "regime": event.get("regime"),
        "blocker": event.get("blocker"), "feature_vector": event.get("feature_vector") or {},
        **metrics, "outcome_class": classify_outcome(metrics, event.get("direction"), threshold_pct),
        "available_at": max(available) if available else None,
        "created_at": now or datetime.now(timezone.utc),
    }
    return payload


def _event_rows(cur: Any, symbols: Sequence[str] | None = None,
                prospective_after: datetime | None = None,
                max_events: int = 50) -> list[dict[str, Any]]:
    clauses = ["b.record_provenance = 'PRODUCTION_RUNTIME'", "b.candle_open_time IS NOT NULL",
               "b.candle_open_time >= %s",
               "(o.id IS NULL OR (upper(coalesce(b.direction_candidate, 'UNKNOWN')) <> 'UNKNOWN' "
               "AND o.forward_return_12_available_at IS NULL))"]
    params: list[Any] = [prospective_after]
    if symbols:
        clauses.append("upper(b.symbol) = ANY(%s)")
        params.append([str(symbol).upper() for symbol in symbols])
    cur.execute(f"""SELECT b.breakout_evaluation_id AS event_id, 'BREAKOUT' AS event_type,
                           b.symbol, b.timeframe, b.candle_open_time AS event_candle_open_time,
                           b.candle_close_time AS event_candle_close_time,
                           b.direction_candidate AS direction, NULLIF(b.regime, '')::integer AS regime,
                           b.primary_blocker AS blocker, b.feature_vector,
                           c.close AS event_close
                    FROM breakout_evaluations b
                    LEFT JOIN analytical_event_outcomes o
                      ON o.event_type='BREAKOUT' AND o.event_id=b.breakout_evaluation_id
                    LEFT JOIN analytical_candle_telemetry c
                      ON upper(c.symbol)=upper(b.symbol) AND c.timeframe=b.timeframe
                     AND c.candle_open_time=b.candle_open_time
                    WHERE {' AND '.join(clauses)}
                    ORDER BY b.candle_open_time, b.breakout_evaluation_id
                    LIMIT %s""", tuple(params + [max(1, min(int(max_events), 200))]))
    columns = [description.name for description in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _future_candles(cur: Any, event: dict[str, Any]) -> list[dict[str, Any]]:
    cur.execute("""SELECT candle_open_time,candle_close_time,close,high,low
                   FROM analytical_candle_telemetry
                   WHERE upper(symbol)=upper(%s) AND timeframe=%s
                     AND candle_open_time > %s AND candle_closed=TRUE
                     AND record_provenance='PRODUCTION_RUNTIME'
                   ORDER BY candle_open_time""",
                (event["symbol"], event["timeframe"], event["event_candle_open_time"]))
    columns = [description.name for description in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def _write_outcome(cur: Any, payload: dict[str, Any]) -> None:
    columns = ("id", "event_type", "event_id", "symbol", "timeframe", "event_candle_open_time",
               "event_candle_close_time", "direction", "regime", "blocker", "feature_vector",
               "forward_return_1", "forward_return_3", "forward_return_6", "forward_return_12",
               "mfe_pct_1", "mae_pct_1", "mfe_pct_3", "mae_pct_3", "mfe_pct_6", "mae_pct_6",
               "mfe_pct_12", "mae_pct_12", "forward_return_1_available_at",
               "forward_return_3_available_at", "forward_return_6_available_at",
               "forward_return_12_available_at", "outcome_class", "available_at", "created_at")
    values = tuple(json.dumps(payload[key], separators=(",", ":")) if key == "feature_vector" else payload.get(key) for key in columns)
    placeholder_values = ["%s"] * len(columns)
    placeholder_values[10] = "%s::jsonb"
    placeholders = ",".join(placeholder_values)
    updates = ",".join(f"{key}=COALESCE(EXCLUDED.{key}, analytical_event_outcomes.{key})"
                       for key in columns[10:-1]
                       if key not in {"id", "event_type", "event_id", "created_at", "outcome_class", "available_at"})
    updates += ",outcome_class=CASE WHEN EXCLUDED.outcome_class <> 'INSUFFICIENT_DATA' THEN EXCLUDED.outcome_class ELSE analytical_event_outcomes.outcome_class END"
    updates += ",available_at=CASE WHEN EXCLUDED.available_at IS NULL THEN analytical_event_outcomes.available_at WHEN analytical_event_outcomes.available_at IS NULL THEN EXCLUDED.available_at ELSE GREATEST(EXCLUDED.available_at, analytical_event_outcomes.available_at) END"
    cur.execute(f"""INSERT INTO analytical_event_outcomes ({','.join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT (event_type,event_id) DO UPDATE SET {updates}""", values)


def update_natural_outcomes(*, prospective_after: datetime, connection: Any = None,
                            symbols: Sequence[str] | None = None, max_events: int = 50) -> int:
    """Persist only outcomes made knowable after an explicit activation point.

    Requiring ``prospective_after`` makes historical backfill impossible by
    omission.  A scheduler should persist its activation timestamp and pass
    it on every run.
    """
    if prospective_after is None:
        raise ValueError("prospective_after is required; historical backfill is not implicit")
    owned = connection is None
    connection = connection or connect_operational_database()
    written = 0
    try:
        with connection.cursor() as cur:
            for event in _event_rows(cur, symbols, prospective_after, max_events):
                payload = build_outcome(event, _future_candles(cur, event))
                _write_outcome(cur, payload)
                written += 1
        connection.commit()
        return written
    finally:
        if owned:
            connection.close()


def _activation_time(connection: Any) -> datetime:
    with connection.cursor() as cur:
        cur.execute("""SELECT completed_at FROM genesis_migration_runs
                       WHERE migration_key='analytical-outcomes-v3' AND status='completed'
                       ORDER BY completed_at DESC LIMIT 1""")
        row = cur.fetchone()
    if not row or row[0] is None:
        raise RuntimeError("analytical outcomes activation watermark unavailable")
    return row[0]


def update_natural_outcomes_from_activation(*, symbols: Sequence[str] | None = None,
                                             max_events: int = 50) -> int:
    """Runtime-safe entry point using the migration completion watermark."""
    with connect_operational_database() as connection:
        activation = _activation_time(connection)
    return update_natural_outcomes(prospective_after=activation, symbols=symbols,
                                   max_events=max_events)


def segment_outcomes(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Small, stable analytical segments; never used as a trading gate."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = "|".join(str(row.get(field) or "UNKNOWN") for field in ("symbol", "regime", "direction", "blocker"))
        groups.setdefault(key, []).append(row)
    result = {}
    for key, group in groups.items():
        sample_size = len(group)
        returns = [_number(row.get("forward_return_3")) for row in group]
        returns = [value for value in returns if value is not None]
        result[key] = {"sample_size": sample_size, "coverage_pct": len(returns) / sample_size * 100 if sample_size else 0.0,
                       "data_quality": "INSUFFICIENT" if sample_size < 20 else "LOW" if sample_size < 50 else "MEDIUM" if sample_size < 100 else "HIGH",
                       "avg_forward_return_3": sum(returns) / len(returns) if returns else None}
    return result


def rag_outcome_context(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build descriptive RAG context; caller decides whether/how to expose it."""
    contexts = []
    for key, metrics in segment_outcomes(rows).items():
        symbol, regime, direction, blocker = key.split("|", 3)
        contexts.append({"context_type": "blocker_performance", "symbol": symbol, "regime": regime,
                         "direction": direction, "blocker": blocker, **metrics,
                         "descriptive_only": metrics["data_quality"] == "INSUFFICIENT"})
    return contexts


__all__ = ["HORIZONS", "build_outcome", "calculate_horizon_metrics", "classify_outcome",
           "rag_outcome_context", "segment_outcomes", "update_natural_outcomes",
           "update_natural_outcomes_from_activation"]
