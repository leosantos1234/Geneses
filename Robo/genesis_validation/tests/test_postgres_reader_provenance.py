import sqlite3
from datetime import datetime, timezone

from genesis_commander.contract import CommanderDecision, ConfidenceLevel, DecisionType
from genesis_commander.persistence import create_commander_decision_schema, persist_commander_decision


def test_historical_unknown_is_not_fabricated_and_provenance_is_persisted():
    connection = sqlite3.connect(":memory:")
    create_commander_decision_schema(connection)
    decision = CommanderDecision(
        commander_decision_id="acceptance-1",
        timestamp_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        subject="acceptance",
        symbol="BTC/USD",
        decision=DecisionType.CANDIDATE_LONG,
        confidence=ConfidenceLevel.HIGH,
        summary="candidate",
        rationale="structured evidence",
        evidence=[{"source": "test"}],
        record_provenance="ACCEPTANCE",
    )
    persist_commander_decision(decision, connection=connection)
    row = connection.execute("SELECT record_provenance, decision_metadata FROM commander_decisions").fetchone()
    assert row[0] == "ACCEPTANCE"
    assert '"record_provenance":"ACCEPTANCE"' in row[1]
