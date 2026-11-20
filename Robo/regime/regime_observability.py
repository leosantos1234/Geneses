"""Append-only, best-effort regime telemetry for production runtime."""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from genesis_runtime import connect_operational_database

_jobs: queue.Queue[dict[str, Any]] = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _write(data: dict[str, Any]) -> None:
    try:
        with connect_operational_database() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO regime_evaluations
                    (regime_evaluation_id,timestamp_utc,symbol,timeframe,candle_open_time,
                     candle_close_time,candle_closed,close,atr,atr_percent,ema12,ema26,
                     ema50,ema200,ema12_ema26_distance,ema50_ema200_distance,ema12_slope,
                     ema50_slope,ema200_slope,ema200_distance_percent,volume,volume_average,
                     volume_ratio,body_fraction,tendencia_forte,tendencia_moderada,regime4_rule,
                     regime1_rule,regime3_rule_path_1,regime3_rule_path_2,regime2_explicit_rule,
                     fallback_to_regime2,winning_rule,regime,record_provenance,created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    tuple(data[key] for key in (
                        "regime_evaluation_id", "timestamp_utc", "symbol", "timeframe",
                        "candle_open_time", "candle_close_time", "candle_closed", "close",
                        "atr", "atr_percent", "ema12", "ema26", "ema50", "ema200",
                        "ema12_ema26_distance", "ema50_ema200_distance", "ema12_slope",
                        "ema50_slope", "ema200_slope", "ema200_distance_percent", "volume",
                        "volume_average", "volume_ratio", "body_fraction", "tendencia_forte",
                        "tendencia_moderada", "regime4_rule", "regime1_rule",
                        "regime3_rule_path_1", "regime3_rule_path_2", "regime2_explicit_rule",
                        "fallback_to_regime2", "winning_rule", "regime", "record_provenance",
                        "created_at",
                    )),
                )
            conn.commit()
    except Exception:
        # Telemetry must not change the trading decision or execution path.
        return


def _worker_main() -> None:
    while True:
        job = _jobs.get()
        try:
            _write(job)
        finally:
            _jobs.task_done()


def record_regime_evaluation(*, symbol: str, timeframe: str, candle: dict[str, Any], diagnostics: dict[str, Any], regime: int) -> str:
    """Queue one production regime evaluation without exposing secrets."""
    global _worker
    now = datetime.now(timezone.utc)
    data = {
        "regime_evaluation_id": str(uuid4()),
        "timestamp_utc": now,
        "symbol": str(symbol).upper(),
        "timeframe": str(timeframe),
        "candle_open_time": candle["candle_open_time"],
        "candle_close_time": candle["candle_close_time"],
        "candle_closed": bool(candle["candle_closed"]),
        "record_provenance": "PRODUCTION_RUNTIME",
        "created_at": now,
        "regime": int(regime),
    }
    data.update(diagnostics)
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="genesis-regime-observability", daemon=True)
            _worker.start()
    _jobs.put(data)
    return data["regime_evaluation_id"]


def flush_regime_observability(timeout: float = 5.0) -> bool:
    import time
    deadline = time.monotonic() + max(float(timeout), 0.0)
    while _jobs.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return _jobs.unfinished_tasks == 0

