"""Prospective outcomes from the canonical closed 1m market memory."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Sequence

from genesis_runtime import connect_operational_database

HORIZONS = (5, 10, 15, 30, 60)


def _direction(value: Any) -> str | None:
    value = str(value or "").upper()
    return value if value in {"BUY", "SELL", "LONG", "SHORT"} else None


def _metrics(entry: float | None, side: str | None, candles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in HORIZONS:
        subset = [row for row in candles if row.get("minutes_from_event", 0) <= horizon]
        if entry is None or side is None or not subset or subset[-1].get("minutes_from_event") < horizon:
            result[f"forward_return_{horizon}m"] = None
            result[f"mfe_{horizon}m"] = None
            result[f"mae_{horizon}m"] = None
            continue
        closes = [float(row["close"]) for row in subset]
        highs = [float(row["high"]) for row in subset]
        lows = [float(row["low"]) for row in subset]
        if side in {"BUY", "LONG"}:
            result[f"forward_return_{horizon}m"] = (closes[-1] - entry) / entry * 100
            result[f"mfe_{horizon}m"] = (max(highs) - entry) / entry * 100
            result[f"mae_{horizon}m"] = (min(lows) - entry) / entry * 100
        else:
            result[f"forward_return_{horizon}m"] = (entry - closes[-1]) / entry * 100
            result[f"mfe_{horizon}m"] = (entry - min(lows)) / entry * 100
            result[f"mae_{horizon}m"] = (entry - max(highs)) / entry * 100
    return result


def update_natural_market_outcomes(*, symbols: Sequence[str] | None = None, max_events: int = 50) -> int:
    """Fill only horizons whose closed 1m candles already exist."""
    written = 0
    with connect_operational_database() as conn:
        with conn.cursor() as cur:
            params: list[Any] = []
            where = ["b.record_provenance='PRODUCTION_RUNTIME'"]
            if symbols:
                where.append("upper(b.symbol)=ANY(%s)")
                params.append([str(s).upper() for s in symbols])
            cur.execute(f"""SELECT b.breakout_evaluation_id,b.symbol,b.breakout_candle_open_time,
                                  b.breakout_candle_close_time,b.direction_candidate,b.primary_blocker,
                                  b.feature_vector
                           FROM breakout_evaluations b
                           WHERE {' AND '.join(where)}
                           ORDER BY b.breakout_candle_open_time DESC LIMIT %s""", tuple(params + [max_events]))
            events = cur.fetchall()
            for event_id, symbol, opened, closed, direction, blocker, _ in events:
                if opened is None:
                    continue
                cur.execute("""SELECT candle_open_time,candle_close_time,open,high,low,close,
                                      EXTRACT(EPOCH FROM (candle_open_time-%s))/60 AS minutes_from_event
                               FROM canonical_market_candles_1m
                               WHERE upper(symbol)=upper(%s) AND candle_open_time>%s AND closed=TRUE
                               ORDER BY candle_open_time""", (opened, symbol, opened))
                rows = [dict(zip([d.name for d in cur.description], row)) for row in cur.fetchall()]
                entry = None
                cur.execute("SELECT close FROM canonical_market_candles_1m WHERE upper(symbol)=upper(%s) AND candle_open_time=%s", (symbol, opened))
                entry_row = cur.fetchone()
                if entry_row and entry_row[0] is not None:
                    entry = float(entry_row[0])
                metrics = _metrics(entry, _direction(direction), rows)
                values = {
                    "event_id": str(event_id), "symbol": str(symbol).upper(),
                    "breakout_candle_open_time": opened, "breakout_candle_close_time": closed,
                    "policy": "CURRENT", "direction": _direction(direction), "first_blocker": blocker,
                }
                for horizon in HORIZONS:
                    values[f"forward_return_{horizon}m"] = metrics.get(f"forward_return_{horizon}m")
                    values[f"mfe_{horizon}m"] = metrics.get(f"mfe_{horizon}m")
                    values[f"mae_{horizon}m"] = metrics.get(f"mae_{horizon}m")
                available = [r["candle_close_time"] for r in rows if r.get("minutes_from_event", 0) >= 60]
                values["available_at"] = available[0] if available else None
                cur.execute("""INSERT INTO breakout_market_outcomes_1m
                    (event_id,symbol,breakout_candle_open_time,breakout_candle_close_time,policy,direction,first_blocker,
                     forward_return_5m,forward_return_10m,forward_return_15m,forward_return_30m,forward_return_60m,
                     mfe_5m,mae_5m,mfe_10m,mae_10m,mfe_15m,mae_15m,mfe_30m,mae_30m,mfe_60m,mae_60m,available_at)
                    VALUES (%(event_id)s,%(symbol)s,%(breakout_candle_open_time)s,%(breakout_candle_close_time)s,%(policy)s,%(direction)s,%(first_blocker)s,
                     %(forward_return_5m)s,%(forward_return_10m)s,%(forward_return_15m)s,%(forward_return_30m)s,%(forward_return_60m)s,
                     %(mfe_5m)s,%(mae_5m)s,%(mfe_10m)s,%(mae_10m)s,%(mfe_15m)s,%(mae_15m)s,%(mfe_30m)s,%(mae_30m)s,%(mfe_60m)s,%(mae_60m)s,%(available_at)s)
                    ON CONFLICT (event_id) DO UPDATE SET
                     forward_return_5m=EXCLUDED.forward_return_5m,forward_return_10m=EXCLUDED.forward_return_10m,
                     forward_return_15m=EXCLUDED.forward_return_15m,forward_return_30m=EXCLUDED.forward_return_30m,
                     forward_return_60m=EXCLUDED.forward_return_60m,mfe_5m=EXCLUDED.mfe_5m,mae_5m=EXCLUDED.mae_5m,
                     mfe_10m=EXCLUDED.mfe_10m,mae_10m=EXCLUDED.mae_10m,mfe_15m=EXCLUDED.mfe_15m,mae_15m=EXCLUDED.mae_15m,
                     mfe_30m=EXCLUDED.mfe_30m,mae_30m=EXCLUDED.mae_30m,mfe_60m=EXCLUDED.mfe_60m,mae_60m=EXCLUDED.mae_60m,
                     available_at=EXCLUDED.available_at""", values)
                written += 1
        conn.commit()
    return written


__all__ = ["HORIZONS", "update_natural_market_outcomes"]
