"""Prospective canonical candle telemetry; never participates in trading decisions."""
from __future__ import annotations

import json
import logging
import math
import queue
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from genesis_runtime import connect_operational_database

_jobs: queue.Queue[dict[str, Any]] = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def normalize_db_value(value: Any) -> Any:
    """Convert pandas/numpy scalars to psycopg-safe values without defaults."""
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        if isinstance(value, dict):
            return {str(k): normalize_db_value(v) for k, v in value.items()}
        return [normalize_db_value(v) for v in value]
    if value.__class__.__name__ == "NaTType":
        return None
    # pandas.Timestamp is a datetime subclass, so this must precede the
    # native datetime branch to guarantee a plain Python datetime for psycopg.
    if hasattr(value, "to_pydatetime"):
        try:
            return normalize_db_value(value.to_pydatetime())
        except (TypeError, ValueError):
            return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return value

    # numpy scalar types expose item(); native values do not.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return normalize_db_value(item())
        except (TypeError, ValueError):
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    return value


def normalize_json_value(value: Any) -> Any:
    """Return JSON-native values recursively for JSON/JSONB parameters."""
    if isinstance(value, dict):
        return {str(key): normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    value = normalize_db_value(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _json(value: Any) -> str:
    return json.dumps(normalize_json_value(value), ensure_ascii=False, separators=(",", ":"))


def _write(data: dict[str, Any]) -> None:
    try:
        if data.get("__kind") == "canonical_1m":
            with connect_operational_database() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO canonical_market_candles_1m
                        (symbol,exchange,timeframe,candle_open_time,candle_close_time,open,high,low,close,volume,turnover,closed,record_provenance)
                        VALUES (%s,%s,'1m',%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s)
                        ON CONFLICT (symbol,exchange,timeframe,candle_open_time) DO NOTHING""",
                        (data["symbol"], data["exchange"], data["candle_open_time"], data["candle_close_time"],
                         data.get("open"), data.get("high"), data.get("low"), data.get("close"),
                         data.get("volume"), data.get("turnover"), data["record_provenance"]),
                    )
                conn.commit()
            try:
                from utils.breakout_market_outcomes import update_natural_market_outcomes
                update_natural_market_outcomes(symbols=[data["symbol"]], max_events=50)
            except Exception as outcome_exc:
                _logger.error("[BREAKOUT_MARKET_OUTCOMES][ERROR] symbol=%s exception=%s message=%s",
                              data.get("symbol"), type(outcome_exc).__name__, str(outcome_exc).replace("\n", " ")[:300])
            return
        data = {key: normalize_db_value(value) for key, value in data.items()}
        with connect_operational_database() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO analytical_candle_telemetry
                    (symbol,timeframe,candle_open_time,candle_close_time,open,high,low,close,
                     volume,atr,atr_percent,volume_ratio,body_fraction,regime,winning_rule,
                     candle_closed,record_provenance,feature_vector,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                    ON CONFLICT (symbol,timeframe,candle_open_time) DO UPDATE SET
                      candle_close_time=EXCLUDED.candle_close_time,
                      open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                      close=EXCLUDED.close, volume=EXCLUDED.volume, atr=EXCLUDED.atr,
                      atr_percent=EXCLUDED.atr_percent, volume_ratio=EXCLUDED.volume_ratio,
                      body_fraction=EXCLUDED.body_fraction, regime=EXCLUDED.regime,
                      winning_rule=EXCLUDED.winning_rule, candle_closed=EXCLUDED.candle_closed,
                      feature_vector=EXCLUDED.feature_vector""",
                    tuple(
                        _json(data["feature_vector"]) if key == "feature_vector" else data[key]
                        for key in (
                        "symbol", "timeframe", "candle_open_time", "candle_close_time",
                        "open", "high", "low", "close", "volume", "atr", "atr_percent",
                        "volume_ratio", "body_fraction", "regime", "winning_rule",
                        "candle_closed", "record_provenance", "feature_vector", "created_at",
                        )
                    ),
                )
            conn.commit()
        # Best-effort, bounded analytical enrichment.  This runs on the
        # telemetry worker after the candle commit and is never on the order
        # or execution path.
        try:
            from utils.analytical_outcomes import update_natural_outcomes_from_activation
            update_natural_outcomes_from_activation(symbols=[str(data["symbol"])], max_events=50)
        except Exception as outcome_exc:
            _logger.error(
                "[ANALYTICAL_OUTCOMES][ERROR] symbol=%s exception=%s message=%s",
                data.get("symbol"), type(outcome_exc).__name__,
                str(outcome_exc).replace("\n", " ")[:300],
            )
    except Exception as exc:
        # Observability must never block or alter the decision path, but a
        # persistence failure must remain visible and sanitized.
        _logger.error(
            "[ANALYTICAL_TELEMETRY][ERROR] symbol=%s timeframe=%s candle_open_time=%s exception=%s message=%s",
            data.get("symbol"), data.get("timeframe"), data.get("candle_open_time"),
            type(exc).__name__, str(exc).replace("\n", " ")[:300],
        )
        return


def _worker_main() -> None:
    while True:
        job = _jobs.get()
        try:
            _write(job)
        finally:
            _jobs.task_done()


def record_canonical_candle(*, symbol: str, timeframe: str, candle: dict[str, Any],
                            diagnostics: dict[str, Any], regime: int) -> None:
    """Queue one closed natural candle, keyed by its canonical identity."""
    global _worker
    now = datetime.now(timezone.utc)
    feature_vector = {
        key: diagnostics.get(key) for key in (
            "close", "atr", "atr_percent", "volume_ratio", "body_fraction",
            "ema12", "ema26", "ema50", "ema200", "ema12_slope", "ema50_slope",
            "ema200_slope", "ema200_distance_percent",
        ) if diagnostics.get(key) is not None
    }
    data = {
        "symbol": str(symbol).upper(), "timeframe": str(timeframe),
        "candle_open_time": candle["candle_open_time"],
        "candle_close_time": candle["candle_close_time"],
        "open": candle.get("open"), "high": candle.get("high"),
        "low": candle.get("low"), "close": candle.get("close", diagnostics.get("close")),
        "volume": candle.get("volume", diagnostics.get("volume")),
        "atr": diagnostics.get("atr"), "atr_percent": diagnostics.get("atr_percent"),
        "volume_ratio": diagnostics.get("volume_ratio"),
        "body_fraction": diagnostics.get("body_fraction"), "regime": int(regime),
        "winning_rule": diagnostics.get("winning_rule"),
        "candle_closed": bool(candle.get("candle_closed")),
        "record_provenance": "PRODUCTION_RUNTIME", "feature_vector": feature_vector,
        "created_at": now,
    }
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="genesis-analytical-telemetry", daemon=True)
            _worker.start()
    _jobs.put({key: normalize_db_value(value) for key, value in data.items()})


def record_closed_1m_candle(*, symbol: str, candle: dict[str, Any],
                            exchange: str = "BYBIT", evaluation_time: datetime | None = None) -> bool:
    """Queue exactly one final 1m candle; forming candles are rejected."""
    opened = candle.get("candle_open_time")
    closed_at = candle.get("candle_close_time")
    now = evaluation_time or datetime.now(timezone.utc)
    if not candle.get("candle_closed") or opened is None or closed_at is None:
        return False
    opened = normalize_db_value(opened)
    closed_at = normalize_db_value(closed_at)
    now = normalize_db_value(now)
    if opened is None or closed_at is None or now is None or closed_at > now:
        return False
    data = {
        "__kind": "canonical_1m", "symbol": str(symbol).upper(), "exchange": str(exchange).upper(),
        "candle_open_time": opened, "candle_close_time": closed_at,
        "open": candle.get("open"), "high": candle.get("high"), "low": candle.get("low"),
        "close": candle.get("close"), "volume": candle.get("volume"), "turnover": candle.get("turnover"),
        "record_provenance": "PRODUCTION_RUNTIME",
    }
    global _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="genesis-analytical-telemetry", daemon=True)
            _worker.start()
    _jobs.put({key: normalize_db_value(value) for key, value in data.items()})
    return True


def flush_analytical_telemetry(timeout: float = 5.0) -> bool:
    import time
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _jobs.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return _jobs.unfinished_tasks == 0


__all__ = ["normalize_db_value", "normalize_json_value", "record_canonical_candle", "record_closed_1m_candle", "flush_analytical_telemetry"]
