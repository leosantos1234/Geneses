"""Append-only, best-effort decision lineage.

The writer is deliberately isolated from the trading decision.  A lineage
failure is observable as LINEAGE_RECORDING_FAILURE and never propagates into
the canonical decision path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from genesis_runtime import database_url

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

SCHEMA_VERSION = "1.0"
_decision_context: ContextVar[str | None] = ContextVar("genesis_decision_id", default=None)
_jobs: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()
_metrics = {"decisions_created": 0, "decisions_completed": 0, "lineage_write_failures": 0,
            "lineage_events": 0, "lineage_write_latency_ms": 0.0}
_metrics_lock = threading.Lock()
_REDACT_KEYS = ("api_key", "api_secret", "secret", "password", "token", "authorization", "dsn", "url")

EVENT_TYPES = {
    "DECISION_CREATED", "MARKET_CONTEXT_CAPTURED", "STRATEGY_EVALUATED", "STRATEGY_SELECTED",
    "COMMANDER_EVALUATED", "COMMANDER_ALLOWED", "COMMANDER_BLOCKED", "COMMANDER_DECISION_CREATED",
    "CANONICAL_COMMANDER_SHADOW",
    "RISK_EVALUATED", "RISK_ALLOWED", "RISK_BLOCKED", "EXECUTION_GATE_EVALUATED", "EXECUTION_BLOCKED",
    "ORDER_INTENT_CREATED", "ORDER_SUBMITTED", "ORDER_ACCEPTED", "ORDER_REJECTED",
    "FILL_RECORDED", "POSITION_OPENED", "DECISION_FINALIZED", "DECISION_ERROR",
}

def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key).lower()
            result[str(key)] = "<redacted>" if any(part in name for part in _REDACT_KEYS) else _safe(item, depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in list(value)[:100]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:500]

def _json(value: Any) -> str:
    return json.dumps(_safe(value or {}), ensure_ascii=False, default=str, separators=(",", ":"))

def _fingerprint(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    return hashlib.sha256(_json(context).encode("utf-8")).hexdigest()[:24]

def _conn():
    if psycopg is None or not database_url():
        raise RuntimeError("DATABASE_OPERATIONAL_FAILURE: PostgreSQL unavailable")
    return psycopg.connect(database_url(), connect_timeout=5)

def _write(kind: str, data: dict[str, Any]) -> None:
    started = time.perf_counter()
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                if kind == "create":
                    cur.execute("""INSERT INTO decision_trace
                        (decision_id, symbol, timeframe, strategy, environment, mode, schema_version,
                         decision_context, config_fingerprint, code_version, strategy_version, model_version,
                         legacy, lineage_quality)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                        (data["decision_id"], data.get("symbol"), data.get("timeframe"), data.get("strategy"),
                         data.get("environment"), data.get("mode"), SCHEMA_VERSION, _json(data.get("context")),
                         _fingerprint(data.get("context")), data.get("code_version"), data.get("strategy_version"),
                         data.get("model_version"), False, "complete"))
                elif kind == "event":
                    cur.execute("""INSERT INTO decision_events
                        (decision_id, stage, event_type, component, status, reason_code, duration_ms, payload)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                        (data["decision_id"], data["stage"], data["event_type"], data.get("component", "unknown"),
                         data.get("status", "INFO"), data.get("reason_code"), data.get("duration_ms"), _json(data.get("payload"))))
                elif kind == "link_trade":
                    cur.execute("""UPDATE decision_trace SET trade_id=COALESCE(trade_id,%s),
                        order_id=COALESCE(order_id,%s), updated_at=NOW() WHERE decision_id=%s""",
                        (data.get("trade_id"), data.get("order_id"), data["decision_id"]))
                    if data.get("trade_id"):
                        cur.execute("UPDATE trades SET decision_id=%s WHERE trade_id=%s AND decision_id IS NULL",
                                    (data["decision_id"], data["trade_id"]))
                elif kind == "finalize":
                    cur.execute("""UPDATE decision_trace SET final_status=%s, reason_code=COALESCE(%s,reason_code),
                        reason_message=COALESCE(%s,reason_message), updated_at=NOW() WHERE decision_id=%s""",
                        (data.get("status", "UNKNOWN"), data.get("reason_code"), data.get("message"), data["decision_id"]))
                elif kind == "finalize_if_open":
                    cur.execute("""UPDATE decision_trace SET final_status=%s, reason_code=COALESCE(%s,reason_code),
                        reason_message=COALESCE(%s,reason_message), updated_at=NOW()
                        WHERE decision_id=%s AND final_status='IN_PROGRESS'""",
                        (data.get("status", "COMPLETED"), data.get("reason_code"), data.get("message"), data["decision_id"]))
            conn.commit()
        with _metrics_lock:
            _metrics["lineage_events"] += int(kind in ("event", "create"))
            if kind == "finalize":
                _metrics["decisions_completed"] += 1
            _metrics["lineage_write_latency_ms"] += (time.perf_counter() - started) * 1000
    except Exception as exc:  # lineage must never change the decision
        with _metrics_lock:
            _metrics["lineage_write_failures"] += 1
        if "DATABASE_OPERATIONAL_FAILURE" in str(exc):
            logging.warning("DATABASE_OPERATIONAL_FAILURE lineage_backend=%s", type(exc).__name__)
        logging.warning("LINEAGE_RECORDING_FAILURE kind=%s error=%s", kind, type(exc).__name__)

def _worker_main():
    while True:
        kind, data = _jobs.get()
        try:
            _write(kind, data)
        finally:
            _jobs.task_done()

def _enqueue(kind: str, data: dict[str, Any]) -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_main, name="genesis-lineage", daemon=True)
            _worker.start()
    _jobs.put((kind, data))

def current_decision_id() -> str | None:
    return _decision_context.get()

def set_current_decision_id(decision_id: str | None) -> None:
    _decision_context.set(str(decision_id) if decision_id else None)

def clear_current_decision() -> None:
    _decision_context.set(None)

def create_decision(symbol=None, timeframe=None, strategy=None, mode=None, context=None, **metadata) -> str:
    decision_id = str(uuid.uuid4())
    set_current_decision_id(decision_id)
    _enqueue("create", {"decision_id": decision_id, "symbol": symbol, "timeframe": timeframe,
                         "strategy": strategy, "mode": mode or os.getenv("GENESIS_MODE", "live"),
                         "context": context, **metadata})
    _enqueue("event", {"decision_id": decision_id, "stage": "CANDIDATE", "event_type": "DECISION_CREATED",
                        "component": "decision_lineage", "status": "INFO"})
    with _metrics_lock:
        _metrics["decisions_created"] += 1
    return decision_id

def record_event(decision_id, event_type, stage, status="INFO", component="genesis", reason_code=None,
                 payload=None, duration_ms=None) -> None:
    if not decision_id or event_type not in EVENT_TYPES:
        return
    _enqueue("event", {"decision_id": str(decision_id), "event_type": event_type, "stage": stage,
                        "status": status, "component": component, "reason_code": reason_code,
                        "payload": payload, "duration_ms": duration_ms})

def link_trade(decision_id, trade_id, order_id=None, status=None) -> None:
    if decision_id:
        _enqueue("link_trade", {"decision_id": str(decision_id), "trade_id": trade_id, "order_id": order_id})
        if status:
            record_event(decision_id, "POSITION_OPENED", "POSITION", status="SUCCESS", payload={"status": status})

def finalize_decision(decision_id, status, reason_code=None, message=None) -> None:
    if decision_id:
        record_event(decision_id, "DECISION_FINALIZED", "OUTCOME", status=status,
                     reason_code=reason_code, payload={"message": message} if message else None)
        _enqueue("finalize", {"decision_id": str(decision_id), "status": status,
                               "reason_code": reason_code, "message": message})


def finalize_decision_if_open(decision_id, status="COMPLETED", reason_code="CYCLE_COMPLETE", message=None) -> None:
    """Close only an open future decision; never overwrite terminal history."""
    if decision_id:
        record_event(decision_id, "DECISION_FINALIZED", "OUTCOME", status=status,
                     reason_code=reason_code, payload={"message": message} if message else None)
        _enqueue("finalize_if_open", {"decision_id": str(decision_id), "status": status,
                                       "reason_code": reason_code, "message": message})


def reconcile_stale_decisions(max_age_minutes: float | None = None, limit: int = 500) -> int:
    """Finaliza decisões abertas que não recebem eventos há tempo excessivo.

    A reconciliação é idempotente, usa SKIP LOCKED para não disputar com outro
    worker e grava o erro antes do evento de finalização na mesma transação.
    """
    age_minutes = float(
        max_age_minutes
        if max_age_minutes is not None
        else os.getenv(
            "GENESIS_DECISION_EXPIRATION_MINUTES",
            os.getenv("GENESIS_DECISION_STALE_MINUTES", "15"),
        )
    )
    age_minutes = max(1.0, age_minutes)
    batch_limit = max(1, min(int(limit or 500), 5000))
    reason = "STALE_DECISION_TIMEOUT"
    message = f"Decisão IN_PROGRESS sem atualização por mais de {age_minutes:g} minutos"

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.decision_id
                    FROM decision_trace t
                    WHERE t.final_status = 'IN_PROGRESS'
                      AND GREATEST(
                          COALESCE(t.updated_at, t.created_at),
                          COALESCE(
                              (SELECT MAX(e.event_ts)
                               FROM decision_events e
                               WHERE e.decision_id = t.decision_id),
                              t.created_at
                          )
                      ) < NOW() - (%s * INTERVAL '1 minute')
                    ORDER BY GREATEST(
                        COALESCE(t.updated_at, t.created_at),
                        COALESCE(
                            (SELECT MAX(e.event_ts)
                             FROM decision_events e
                             WHERE e.decision_id = t.decision_id),
                            t.created_at
                        )
                    )
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (age_minutes, batch_limit),
                )
                decision_ids = [str(row[0]) for row in cur.fetchall()]
                for decision_id in decision_ids:
                    cur.execute(
                        """
                        INSERT INTO decision_events
                            (decision_id, stage, event_type, component, status, reason_code, payload)
                        VALUES (%s, 'OUTCOME', 'DECISION_ERROR', 'decision_watchdog',
                                'ERROR', %s, %s::jsonb)
                        """,
                        (decision_id, reason, _json({"message": message, "watchdog": True})),
                    )
                    cur.execute(
                        """
                        INSERT INTO decision_events
                            (decision_id, stage, event_type, component, status, reason_code, payload)
                        VALUES (%s, 'OUTCOME', 'DECISION_FINALIZED', 'decision_watchdog',
                                'ERROR', %s, %s::jsonb)
                        """,
                        (decision_id, reason, _json({"message": message, "watchdog": True})),
                    )
                    cur.execute(
                        """
                        UPDATE decision_trace
                        SET final_status = 'ERROR', reason_code = %s,
                            reason_message = %s, updated_at = NOW()
                        WHERE decision_id = %s AND final_status = 'IN_PROGRESS'
                        """,
                        (reason, message, decision_id),
                    )
            conn.commit()
        if decision_ids:
            logging.warning(
                "DECISION_WATCHDOG reconciled=%d max_age_minutes=%s",
                len(decision_ids), age_minutes,
            )
        return len(decision_ids)
    except Exception as exc:
        logging.warning("DECISION_WATCHDOG_FAILURE error=%s", type(exc).__name__)
        return 0

def flush(timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while _jobs.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    return _jobs.unfinished_tasks == 0

def _read_conn():
    conn = _conn()
    conn.execute("SET default_transaction_read_only = on")
    conn.execute("SET statement_timeout = '5s'")
    return conn

def get_decision_trace(decision_id):
    try:
        uuid.UUID(str(decision_id))
    except (ValueError, TypeError, AttributeError):
        return None
    flush()
    with _read_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT decision_id,created_at,updated_at,symbol,timeframe,strategy,environment,mode,final_status,trade_id,order_id,position_id,schema_version,decision_context,config_fingerprint,code_version,strategy_version,model_version,reason_code,reason_message,legacy,lineage_quality FROM decision_trace WHERE decision_id=%s", (str(decision_id),))
            row = cur.fetchone()
            if not row:
                return None
            keys = ["decision_id","created_at","updated_at","symbol","timeframe","strategy","environment","mode","final_status","trade_id","order_id","position_id","schema_version","decision_context","config_fingerprint","code_version","strategy_version","model_version","reason_code","reason_message","legacy","lineage_quality"]
            trace = dict(zip(keys, row))
            cur.execute("SELECT event_id,event_ts,stage,event_type,component,status,reason_code,duration_ms,payload,schema_version FROM decision_events WHERE decision_id=%s ORDER BY event_ts,event_id", (str(decision_id),))
            event_keys = ["event_id","event_ts","stage","event_type","component","status","reason_code","duration_ms","payload","schema_version"]
            trace["events"] = [dict(zip(event_keys, item)) for item in cur.fetchall()]
            return trace

def list_decisions(symbol=None, strategy=None, status=None, reason_code=None, start_time=None, end_time=None,
                   trade_id=None, limit=50, offset=0):
    limit = min(max(int(limit or 50), 1), 500)
    offset = max(int(offset or 0), 0)
    clauses, params = [], []
    for column, value in (("symbol", symbol), ("strategy", strategy), ("final_status", status), ("reason_code", reason_code), ("trade_id", trade_id)):
        if value:
            clauses.append(f"{column}=%s"); params.append(value)
    if start_time: clauses.append("created_at >= %s"); params.append(start_time)
    if end_time: clauses.append("created_at <= %s"); params.append(end_time)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _read_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT decision_id,created_at,symbol,timeframe,strategy,final_status,trade_id,order_id,reason_code,lineage_quality FROM decision_trace{where} ORDER BY created_at DESC, decision_id DESC LIMIT %s OFFSET %s", (*params, limit, offset))
            keys = ["decision_id","created_at","symbol","timeframe","strategy","final_status","trade_id","order_id","reason_code","lineage_quality"]
            return [dict(zip(keys, row)) for row in cur.fetchall()]
