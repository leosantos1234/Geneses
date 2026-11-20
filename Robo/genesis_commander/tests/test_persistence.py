from datetime import datetime, timezone
import sqlite3
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_commander.contract import CommanderDecision, ConfidenceLevel, DecisionType
from genesis_commander.persistence import CommanderDecisionConflictError, create_commander_decision_schema, get_commander_decision, list_commander_decisions, persist_commander_decision


def decision(kind=DecisionType.CANDIDATE_LONG, *, decision_id="d-1", stamp=None, confidence=ConfidenceLevel.HIGH):
    return CommanderDecision(
        commander_decision_id=decision_id, timestamp_utc=stamp or datetime(2026, 1, 1, tzinfo=timezone.utc),
        subject="BTC/USD", symbol="BTC/USD", decision=kind, confidence=confidence,
        summary="shadow decision", rationale="bounded evidence", evidence=[{"source": "test"}],
    )


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    create_commander_decision_schema(conn)
    yield conn
    conn.close()


@pytest.mark.parametrize("kind", list(DecisionType))
def test_all_canonical_decision_types_are_preserved(connection, kind):
    item = decision(kind, decision_id=f"{kind.value}-1", confidence=ConfidenceLevel.NOT_APPLICABLE if kind in {DecisionType.NO_ACTION, DecisionType.INSUFFICIENT_DATA} else ConfidenceLevel.HIGH)
    assert persist_commander_decision(item, connection=connection) is True
    assert get_commander_decision(item.commander_decision_id, connection=connection)["decision_type"] == kind.value


def test_utc_timestamp_strategy_missing_and_confidence_preserved(connection):
    item = decision(DecisionType.WATCH, decision_id="utc-1", confidence=ConfidenceLevel.NOT_APPLICABLE)
    persist_commander_decision(item, connection=connection)
    stored = get_commander_decision("utc-1", connection=connection)
    assert stored["timestamp_utc"].tzinfo is not None
    assert stored["strategy_id"] is None and stored["strategy_name"] is None
    assert stored["confidence"] == "NOT_APPLICABLE"


def test_duplicate_identical_write_is_idempotent(connection):
    item = decision(decision_id="same")
    assert persist_commander_decision(item, connection=connection) is True
    assert persist_commander_decision(item, connection=connection) is False
    assert len(list_commander_decisions(connection=connection)) == 1


def test_conflicting_duplicate_is_rejected(connection):
    persist_commander_decision(decision(decision_id="conflict"), connection=connection)
    with pytest.raises(CommanderDecisionConflictError):
        persist_commander_decision(decision(DecisionType.CANDIDATE_SHORT, decision_id="conflict"), connection=connection)


def test_retrieval_is_chronological(connection):
    persist_commander_decision(decision(decision_id="late", stamp=datetime(2026, 1, 2, tzinfo=timezone.utc)), connection=connection)
    persist_commander_decision(decision(decision_id="early", stamp=datetime(2026, 1, 1, tzinfo=timezone.utc)), connection=connection)
    assert [item["commander_decision_id"] for item in list_commander_decisions(connection=connection)] == ["early", "late"]


def test_source_context_and_metadata_roundtrip(connection):
    item = decision(decision_id="metadata")
    persist_commander_decision(item, connection=connection, source_context_id="ctx-1")
    stored = get_commander_decision("metadata", connection=connection)
    assert stored["source_context_id"] == "ctx-1"
    assert stored["decision_metadata"]["shadow_only"] is True


def test_paper_lineage_uses_same_commander_decision_id():
    from genesis_paper.contracts import PaperOrder
    order = PaperOrder(commander_decision_id="commander-1", symbol="BTC/USD", requested_quantity=1)
    assert order.commander_decision_id == "commander-1"


def test_historical_rows_are_not_backfilled(connection):
    assert list_commander_decisions(connection=connection) == []
