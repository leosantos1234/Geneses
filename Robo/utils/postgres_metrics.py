"""Telemetria read-only de conexões e locks PostgreSQL."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROBO_ROOT = Path(__file__).resolve().parents[1]
POSTGRES_METRICS_DIR = Path(
    os.getenv("GENESIS_POSTGRES_METRICS_DIR", str(ROBO_ROOT / "runtime" / "metrics"))
).expanduser()
if not POSTGRES_METRICS_DIR.is_absolute():
    POSTGRES_METRICS_DIR = (ROBO_ROOT / POSTGRES_METRICS_DIR).resolve()

_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False


def inspect_postgres() -> dict[str, Any]:
    """Consulta pg_stat_activity/pg_locks sem alterar estado do banco."""
    try:
        from genesis_runtime import connect_operational_database
    except ImportError:
        from Robo.genesis_runtime import connect_operational_database

    query_connections = """
        SELECT
            COUNT(*) AS open_connections,
            COUNT(*) FILTER (WHERE state = 'active') AS active_connections,
            COUNT(*) FILTER (WHERE state = 'idle') AS idle_connections,
            COUNT(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction,
            COUNT(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting_connections,
            COALESCE(MAX(EXTRACT(EPOCH FROM (NOW() - query_start))), 0) AS longest_query_seconds
        FROM pg_stat_activity
        WHERE datname = current_database()
    """
    query_limits = """
        SELECT setting::bigint
        FROM pg_settings
        WHERE name = 'max_connections'
    """
    query_locks = """
        SELECT
            COUNT(*) FILTER (WHERE granted) AS granted_locks,
            COUNT(*) FILTER (WHERE NOT granted) AS waiting_locks
        FROM pg_locks
        WHERE database = (SELECT oid FROM pg_database WHERE datname = current_database())
    """
    query_blocked = """
        SELECT
            COUNT(*) FILTER (WHERE cardinality(pg_blocking_pids(pid)) > 0) AS blocked_sessions,
            COALESCE(MAX(cardinality(pg_blocking_pids(pid))), 0) AS max_blockers_per_session
        FROM pg_stat_activity
        WHERE datname = current_database()
    """

    with connect_operational_database(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(query_connections)
            connections = cur.fetchone()
            cur.execute(query_limits)
            max_connections_row = cur.fetchone()
            cur.execute(query_locks)
            locks = cur.fetchone()
            cur.execute(query_blocked)
            blocked = cur.fetchone()

    open_connections = int(connections[0] or 0)
    max_connections = int(max_connections_row[0] or 0) if max_connections_row else 0
    utilization = (open_connections / max_connections * 100.0) if max_connections else None
    waiting_locks = int(locks[1] or 0)
    blocked_sessions = int(blocked[0] or 0)
    status = "healthy" if waiting_locks == 0 and blocked_sessions == 0 else "degraded"
    return {
        "status": status,
        "open_connections": open_connections,
        "max_connections": max_connections,
        "connection_utilization_pct": round(utilization, 2) if utilization is not None else None,
        "active_connections": int(connections[1] or 0),
        "idle_connections": int(connections[2] or 0),
        "idle_in_transaction": int(connections[3] or 0),
        "waiting_connections": int(connections[4] or 0),
        "longest_query_seconds": round(float(connections[5] or 0), 3),
        "granted_locks": int(locks[0] or 0),
        "waiting_locks": waiting_locks,
        "blocked_sessions": blocked_sessions,
        "max_blockers_per_session": int(blocked[1] or 0),
    }


def record_postgres_metrics(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    try:
        metrics = inspect_postgres()
        error_type = None
    except Exception as exc:
        metrics = {"status": "unavailable"}
        error_type = type(exc).__name__
        logger.warning("[GENESIS_POSTGRES] inspection_failed=%s", error_type)

    payload = {
        "event": "GENESIS_POSTGRES_LOCKS_CONNECTIONS",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "pid": snapshot.get("pid", os.getpid()),
        "role": role,
        "active_symbol": symbol,
        "environment": snapshot.get("environment"),
        "version": snapshot.get("version"),
        **metrics,
    }
    if error_type:
        payload["error_type"] = error_type
    try:
        POSTGRES_METRICS_DIR.mkdir(parents=True, exist_ok=True)
        target = POSTGRES_METRICS_DIR / f"postgres_{payload['pid']}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("[GENESIS_POSTGRES] metrics_write_failed=%s", type(exc).__name__)


def start_postgres_metrics_monitor(
    snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger
) -> None:
    global _MONITOR_STARTED
    if str(os.getenv("GENESIS_POSTGRES_METRICS_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on", "sim"}:
        return
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True

    interval = max(5.0, float(os.getenv("GENESIS_POSTGRES_METRICS_INTERVAL_SECONDS", "300")))

    def monitor() -> None:
        while True:
            record_postgres_metrics(snapshot, role=role, symbol=symbol, logger=logger)
            time.sleep(interval)

    thread = threading.Thread(target=monitor, name="genesis-postgres-metrics", daemon=True)
    thread.start()
    logger.info("[GENESIS_POSTGRES] enabled interval_seconds=%s output=%s", interval, POSTGRES_METRICS_DIR)
