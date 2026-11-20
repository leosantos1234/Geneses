"""Commander 2.0 decision lineage — append-only audit trail."""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from lineage.decision_lineage import (
    current_decision_id,
    flush as lineage_flush,
    record_event,
    get_decision_trace as lineage_get_decision_trace,
)
from genesis_commander.contract import CommanderDecision, DecisionType, ConfidenceLevel
from genesis_commander.persistence import persist_commander_decision

LINEAGE_CONFIRMATION_TIMEOUT_SECONDS = 5.0


class CommanderLineageError(Exception):
    """Raised when Commander lineage recording fails."""
    pass


class CommanderLineagePersistenceError(CommanderLineageError):
    """Commander decision lineage was not confirmed as persisted (fail-closed)."""
    pass


def record_commander_decision(decision: CommanderDecision) -> None:
    """Record Commander 2.0 decision in append-only lineage.

    Uses existing decision_trace/decision_events infrastructure.
    Stores CommanderDecision as COMMANDER_DECISION_EVENT payload.

    Raises:
        CommanderLineageError: if decision context is missing or write fails.
    """
    if not decision:
        raise CommanderLineagePersistenceError("decision required")

    decision_id = current_decision_id()
    if not decision_id:
        raise CommanderLineagePersistenceError("LINEAGE_CONTEXT_NOT_AVAILABLE: current_decision_id() is None")

    try:
        persist_commander_decision(decision, source_context_id=decision_id)
        payload = {
            "commander_decision_id": decision.commander_decision_id,
            "commander_version": decision.commander_version,
            "timestamp_utc": decision.timestamp_utc.isoformat(),
            "decision": decision.decision.value,
            "confidence": decision.confidence.value,
            "subject": decision.subject,
            "symbol": decision.symbol,
            "strategy_id": decision.strategy_id,
            "strategy_name": decision.strategy_name,
            "reference_price": decision.reference_price,
            "market_regime": decision.market_regime,
            "record_provenance": decision.record_provenance,
            "summary": decision.summary,
            "rationale": decision.rationale,
            "evidence": decision.evidence,
            "evidence_count": len(decision.evidence),
            "skills_used": decision.skills_used,
            "mcp_tools_used": decision.mcp_tools_used,
            "rag_context_available": len(decision.rag_context_used) > 0,
            "rag_context_count": len(decision.rag_context_used),
            "risk_flags": decision.risk_flags,
            "data_gaps": decision.data_gaps,
            "limitations": decision.limitations,
            "shadow_only": decision.shadow_only,
            "execution_authority": decision.execution_authority.value,
        }

        record_event(
            decision_id=decision_id,
            event_type="COMMANDER_DECISION_CREATED",
            stage="DECISION",
            status="INFO",
            component="commander_2.0",
            reason_code=None,
            payload=payload,
            duration_ms=None
        )
    except CommanderLineageError:
        raise
    except Exception as e:
        logging.error("COMMANDER_LINEAGE_WRITE_FAILURE error=%s", type(e).__name__)
        raise CommanderLineagePersistenceError(f"COMMANDER_LINEAGE_WRITE_FAILURE: {type(e).__name__}") from e

    _confirm_commander_decision(decision_id, decision)


def _confirm_commander_decision(decision_id: str, decision: CommanderDecision) -> None:
    """Prove the COMMANDER_DECISION_CREATED event was persisted, within a bounded wait."""
    if not lineage_flush(timeout=LINEAGE_CONFIRMATION_TIMEOUT_SECONDS):
        raise CommanderLineagePersistenceError(
            "LINEAGE_CONFIRMATION_TIMEOUT: lineage queue not drained within bound"
        )
    try:
        trace = lineage_get_decision_trace(decision_id)
    except Exception as e:
        raise CommanderLineagePersistenceError(
            f"LINEAGE_CONFIRMATION_READ_FAILED: {type(e).__name__}"
        ) from e
    if trace is None:
        raise CommanderLineagePersistenceError("LINEAGE_CONFIRMATION_FAILED: decision_trace missing")

    for event in trace.get("events", []):
        if event.get("event_type") != "COMMANDER_DECISION_CREATED":
            continue
        payload = event.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        if (
            payload.get("commander_decision_id") == decision.commander_decision_id
            and payload.get("commander_version") == decision.commander_version
        ):
            return
    raise CommanderLineagePersistenceError(
        "LINEAGE_CONFIRMATION_FAILED: COMMANDER_DECISION_CREATED with matching ids not persisted"
    )


def get_commander_decision_from_lineage(decision_id: str) -> Optional[dict]:
    """Retrieve Commander 2.0 decision from lineage.

    Returns the COMMANDER_DECISION_EVENT payload or None if not found.
    """
    try:
        UUID(str(decision_id))
    except (ValueError, TypeError):
        return None

    try:
        trace = lineage_get_decision_trace(decision_id)
        if not trace:
            return None

        events = trace.get("events", [])
        for event in events:
            if event.get("event_type") == "COMMANDER_DECISION_CREATED":
                payload = event.get("payload", {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                return payload

        return None
    except Exception as e:
        logging.warning("COMMANDER_LINEAGE_READ_FAILURE decision_id=%s error=%s", decision_id, type(e).__name__)
        return None


def get_commander_decision_with_lineage(decision_id: str) -> Optional[dict]:
    """Get full decision trace including Commander 2.0 decision.

    Returns trace dict with commander_decision embedded.
    """
    try:
        UUID(str(decision_id))
    except (ValueError, TypeError):
        return None

    try:
        trace = lineage_get_decision_trace(decision_id)
        if not trace:
            return None

        commander_data = get_commander_decision_from_lineage(decision_id)
        if commander_data:
            trace["commander_decision"] = commander_data

        return trace
    except Exception as e:
        logging.warning("COMMANDER_LINEAGE_READ_FAILURE decision_id=%s error=%s", decision_id, type(e).__name__)
        return None
