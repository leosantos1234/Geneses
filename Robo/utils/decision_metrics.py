"""Contagem periódica dos estados das decisões persistidas."""

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
DECISION_METRICS_DIR = Path(
    os.getenv("GENESIS_DECISION_METRICS_DIR", str(ROBO_ROOT / "runtime" / "metrics"))
).expanduser()
if not DECISION_METRICS_DIR.is_absolute():
    DECISION_METRICS_DIR = (ROBO_ROOT / DECISION_METRICS_DIR).resolve()

STATUSES = ("CREATED", "BLOCKED", "COMPLETED", "ERROR", "IN_PROGRESS")
_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False


def count_decisions(symbol: str | None = None) -> dict[str, int]:
    """Consulta contagens sem alterar o banco e sempre retorna todos os status."""
    try:
        from genesis_runtime import connect_operational_database
    except ImportError:
        from Robo.genesis_runtime import connect_operational_database

    normalized = str(symbol or "").strip().upper()
    symbol_filter = normalized if normalized and normalized != "UNKNOWN" else None
    query = """
        WITH scoped AS (
            SELECT decision_id, final_status
            FROM decision_trace
            -- O PostgreSQL nao consegue inferir o tipo de um parametro usado
            -- diretamente em IS NULL (erro: "could not determine type of
            -- parameter $1"). O cast explicito mantem a consulta segura e
            -- funciona tanto com filtro de ativo quanto no escopo global.
            WHERE (%s::text IS NULL OR UPPER(symbol) = %s::text)
        ), status_rows AS (
            SELECT 'CREATED' AS status
            FROM decision_events e
            JOIN scoped s ON s.decision_id = e.decision_id
            WHERE e.event_type = 'DECISION_CREATED'
            UNION ALL
            SELECT final_status AS status
            FROM scoped
            WHERE final_status IN ('BLOCKED', 'COMPLETED', 'ERROR', 'IN_PROGRESS')
        )
        SELECT status, COUNT(*)
        FROM status_rows
        GROUP BY status
    """
    counts = {status: 0 for status in STATUSES}
    with connect_operational_database(read_only=True) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (symbol_filter, symbol_filter))
            for status, total in cur.fetchall():
                if status in counts:
                    counts[status] = int(total)
    return counts


def record_decision_counts(snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger) -> None:
    try:
        counts = count_decisions(None)
        payload = {
            "event": "GENESIS_DECISION_STATUS_COUNTS",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "pid": snapshot.get("pid", os.getpid()),
            "role": role,
            "active_symbol": symbol,
            "scope": "all_decisions",
            "environment": snapshot.get("environment"),
            "version": snapshot.get("version"),
            "counts": counts,
        }
        DECISION_METRICS_DIR.mkdir(parents=True, exist_ok=True)
        target = DECISION_METRICS_DIR / f"decisions_{payload['pid']}.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.warning("[GENESIS_DECISIONS] count_failed=%s", exc)


def start_decision_metrics_monitor(
    snapshot: dict[str, Any], *, role: str, symbol: str, logger: logging.Logger
) -> None:
    global _MONITOR_STARTED
    if str(os.getenv("GENESIS_DECISION_METRICS_ENABLED", "true")).strip().lower() not in {"1", "true", "yes", "on", "sim"}:
        return
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True

    interval = max(5.0, float(os.getenv("GENESIS_DECISION_METRICS_INTERVAL_SECONDS", "300")))

    def monitor() -> None:
        while True:
            record_decision_counts(snapshot, role=role, symbol=symbol, logger=logger)
            time.sleep(interval)

    thread = threading.Thread(target=monitor, name="genesis-decision-metrics", daemon=True)
    thread.start()
    logger.info("[GENESIS_DECISIONS] enabled interval_seconds=%s output=%s", interval, DECISION_METRICS_DIR)
