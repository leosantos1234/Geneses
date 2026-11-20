"""Canonical persistence for future CommanderDecision objects only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from genesis_runtime import connect_operational_database


class CommanderDecisionPersistenceError(RuntimeError):
    pass


class CommanderDecisionConflictError(CommanderDecisionPersistenceError):
    pass


SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS commander_decisions (
    commander_decision_id TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    symbol TEXT,
    decision_type TEXT NOT NULL,
    strategy_id TEXT,
    strategy_name TEXT,
    confidence TEXT,
    reference_price NUMERIC,
    market_regime TEXT,
    commander_version TEXT NOT NULL,
    source_context_id TEXT,
    decision_metadata TEXT NOT NULL DEFAULT '{}',
    rationale_metadata TEXT NOT NULL DEFAULT '{}',
    record_provenance TEXT NOT NULL DEFAULT 'UNKNOWN'
)
"""


def create_commander_decision_schema(connection) -> None:
    connection.execute(SQLITE_SCHEMA)
    connection.commit()


def _is_sqlite(connection) -> bool:
    return connection.__class__.__module__.startswith("sqlite3")


def _placeholder(connection) -> str:
    return "?" if _is_sqlite(connection) else "%s"


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":"))


def _metadata(decision) -> tuple[str, str]:
    decision_metadata = {
        "subject": decision.subject,
        "summary": decision.summary,
        "strategy_id": decision.strategy_id,
        "strategy_name": decision.strategy_name,
        "reference_price": decision.reference_price,
        "market_regime": decision.market_regime,
        "record_provenance": decision.record_provenance,
        "evidence": decision.evidence,
        "skills_used": decision.skills_used,
        "mcp_tools_used": decision.mcp_tools_used,
        "risk_flags": decision.risk_flags,
        "data_gaps": decision.data_gaps,
        "limitations": decision.limitations,
        "shadow_only": decision.shadow_only,
        "execution_authority": decision.execution_authority.value,
    }
    rationale_metadata = {"rationale": decision.rationale}
    return _json(decision_metadata), _json(rationale_metadata)


def _row_values(decision, source_context_id: str | None) -> tuple[Any, ...]:
    if decision.timestamp_utc.tzinfo is None or decision.timestamp_utc.utcoffset() != timezone.utc.utcoffset(decision.timestamp_utc):
        raise CommanderDecisionPersistenceError("timestamp_utc must be timezone-aware UTC")
    metadata, rationale = _metadata(decision)
    return (
        decision.commander_decision_id,
        decision.timestamp_utc.isoformat(),
        decision.symbol,
        decision.decision.value,
        decision.strategy_id,
        decision.strategy_name,
        decision.confidence.value if decision.confidence is not None else None,
        decision.reference_price,
        decision.market_regime,
        decision.commander_version,
        source_context_id,
        metadata,
        rationale,
        decision.record_provenance,
    )


def _timestamp_key(value: Any) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed.astimezone(timezone.utc).isoformat()


def _timestamp_utc(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise CommanderDecisionPersistenceError("persisted timestamp_utc is not timezone-aware")
    return parsed.astimezone(timezone.utc)


def _json_key(value: Any) -> str:
    return _json(value) if not isinstance(value, str) else value


def persist_commander_decision(decision, *, connection=None, source_context_id: str | None = None) -> bool:
    """Insert once; identical retries are no-ops, conflicting retries fail."""
    if decision is None:
        raise CommanderDecisionPersistenceError("decision required")
    decision.validate()
    owned = connection is None
    connection = connection or connect_operational_database()
    ph = _placeholder(connection)
    values = _row_values(decision, source_context_id)
    try:
        cur = connection.cursor()
        cur.execute(f"SELECT commander_decision_id,timestamp_utc,symbol,decision_type,commander_version,source_context_id,decision_metadata,rationale_metadata FROM commander_decisions WHERE commander_decision_id={ph}", (decision.commander_decision_id,))
        existing = cur.fetchone()
        if existing is not None:
            expected = (values[0], values[1], values[2], values[3], values[9], values[10], values[11], values[12])
            actual = (existing[0], _timestamp_key(existing[1]), existing[2], existing[3], existing[4], existing[5], _json_key(existing[6]), _json_key(existing[7]))
            if actual != expected:
                raise CommanderDecisionConflictError("conflicting commander_decision_id already persisted")
            return False
        columns = "commander_decision_id,timestamp_utc,symbol,decision_type,strategy_id,strategy_name,confidence,reference_price,market_regime,commander_version,source_context_id,decision_metadata,rationale_metadata,record_provenance"
        marks = ",".join([ph] * len(values))
        cur.execute(f"INSERT INTO commander_decisions ({columns}) VALUES ({marks})", values)
        if owned:
            connection.commit()
        return True
    except CommanderDecisionPersistenceError:
        if owned:
            connection.rollback()
        raise
    except Exception as exc:
        if owned:
            connection.rollback()
        raise CommanderDecisionPersistenceError(f"commander decision persistence failed: {type(exc).__name__}") from exc
    finally:
        if owned:
            connection.close()


def update_commander_decision_context(
    source_context_id: str,
    *,
    strategy_id: str | None = None,
    strategy_name: str | None = None,
    reference_price: Any = None,
    market_regime: str | None = None,
    connection=None,
) -> bool:
    """Persist newly known runtime context for a future decision only.

    The legacy worker selects its backtest winner after the Commander shadow
    decision is created.  This bounded enrichment uses the exact legacy
    source_context_id and never guesses or backfills historical rows.
    """
    if not source_context_id:
        raise CommanderDecisionPersistenceError("source_context_id required")
    values = (strategy_id, strategy_name, reference_price, market_regime, str(source_context_id))
    owned = connection is None
    connection = connection or connect_operational_database()
    try:
        cur = connection.cursor()
        cur.execute(
            """UPDATE commander_decisions
               SET strategy_id=COALESCE(%s,strategy_id),
                   strategy_name=COALESCE(%s,strategy_name),
                   reference_price=COALESCE(%s,reference_price),
                   market_regime=COALESCE(%s,market_regime)
             WHERE source_context_id=%s""",
            values,
        )
        changed = cur.rowcount > 0
        if owned:
            connection.commit()
        return changed
    except Exception as exc:
        if owned:
            connection.rollback()
        raise CommanderDecisionPersistenceError(
            f"commander decision context update failed: {type(exc).__name__}"
        ) from exc
    finally:
        if owned:
            connection.close()


def get_commander_decision(commander_decision_id: str, *, connection=None) -> dict[str, Any] | None:
    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    ph = _placeholder(connection)
    try:
        cur = connection.cursor()
        cur.execute(f"SELECT commander_decision_id,timestamp_utc,symbol,decision_type,strategy_id,strategy_name,confidence,reference_price,market_regime,commander_version,source_context_id,decision_metadata,rationale_metadata FROM commander_decisions WHERE commander_decision_id={ph}", (commander_decision_id,))
        row = cur.fetchone()
        if row is None:
            return None
        keys = ("commander_decision_id", "timestamp_utc", "symbol", "decision_type", "strategy_id", "strategy_name", "confidence", "reference_price", "market_regime", "commander_version", "source_context_id", "decision_metadata", "rationale_metadata")
        result = dict(zip(keys, row))
        result["timestamp_utc"] = _timestamp_utc(result["timestamp_utc"])
        for key in ("decision_metadata", "rationale_metadata"):
            if isinstance(result[key], str):
                result[key] = json.loads(result[key])
        return result
    finally:
        if owned:
            connection.close()


def list_commander_decisions(*, connection=None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    owned = connection is None
    connection = connection or connect_operational_database(read_only=True)
    try:
        cur = connection.cursor()
        cur.execute("SELECT commander_decision_id,timestamp_utc,symbol,decision_type,strategy_id,strategy_name,confidence,reference_price,market_regime,commander_version,source_context_id,decision_metadata,rationale_metadata FROM commander_decisions ORDER BY timestamp_utc,commander_decision_id LIMIT %s OFFSET %s" if not _is_sqlite(connection) else "SELECT commander_decision_id,timestamp_utc,symbol,decision_type,strategy_id,strategy_name,confidence,reference_price,market_regime,commander_version,source_context_id,decision_metadata,rationale_metadata FROM commander_decisions ORDER BY timestamp_utc,commander_decision_id LIMIT ? OFFSET ?", (max(1, int(limit)), max(0, int(offset))))
        keys = ("commander_decision_id", "timestamp_utc", "symbol", "decision_type", "strategy_id", "strategy_name", "confidence", "reference_price", "market_regime", "commander_version", "source_context_id", "decision_metadata", "rationale_metadata")
        rows = []
        for row in cur.fetchall():
            item = dict(zip(keys, row))
            item["timestamp_utc"] = _timestamp_utc(item["timestamp_utc"])
            for key in ("decision_metadata", "rationale_metadata"):
                if isinstance(item[key], str):
                    item[key] = json.loads(item[key])
            rows.append(item)
        return rows
    finally:
        if owned:
            connection.close()


__all__ = ["CommanderDecisionConflictError", "CommanderDecisionPersistenceError", "create_commander_decision_schema", "get_commander_decision", "list_commander_decisions", "persist_commander_decision", "update_commander_decision_context"]
