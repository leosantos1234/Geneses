"""Phase 2 Decision Lineage Tests — Complete acceptance criteria."""

import pytest
import sys
import os
import json
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock, patch

# Setup path and environment
sys.path.insert(0, 'Robo')
os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

env_file = os.path.join('Robo', '.env-btc')
if os.path.exists(env_file):
    with open(env_file, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key] = value

from genesis_commander.contract import (
    CommanderDecision,
    DecisionType,
    ConfidenceLevel,
    ExecutionAuthority,
)
from genesis_commander.lineage import (
    record_commander_decision,
    get_commander_decision_from_lineage,
    get_commander_decision_with_lineage,
    CommanderLineageError,
)


# Business state capture functions
def capture_business_state():
    """Capture current row counts for core tables."""
    try:
        from genesis_runtime import database_url
        import psycopg

        conn = psycopg.connect(database_url(), connect_timeout=5)
        conn.execute("SET default_transaction_read_only = on")

        state = {}
        tables = ['trades', 'predictions_lstm', 'mr_fsm_state']

        with conn.cursor() as cur:
            for table in tables:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cur.fetchone()[0]
                    state[table] = count
                except:
                    state[table] = None

        conn.close()
        return state
    except Exception as e:
        pytest.skip(f"Cannot capture business state: {type(e).__name__}")


class TestPhase2Acceptance:
    """Complete Phase 2 acceptance criteria."""

    @pytest.fixture(autouse=True)
    def business_state_capture(self):
        """Capture business state before and after tests."""
        self.state_before = capture_business_state()
        yield
        self.state_after = capture_business_state()

        # Verify no business-state mutations
        for table in self.state_before.keys():
            if self.state_before[table] is not None:
                assert self.state_before[table] == self.state_after[table], \
                    f"{table}: {self.state_before[table]} -> {self.state_after[table]}"

    def test_01_schema_reuse(self):
        """A. Schema reuse - existing tables."""
        from genesis_commander.lineage import (
            record_commander_decision,
            get_commander_decision_from_lineage,
            get_commander_decision_with_lineage,
        )
        assert callable(record_commander_decision)
        assert callable(get_commander_decision_from_lineage)
        assert callable(get_commander_decision_with_lineage)

    def test_02_missing_context_observable(self):
        """B. Missing decision context is observable."""
        decision = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test",
            decision=DecisionType.WATCH,
        )
        decision.validate()

        with patch("genesis_commander.lineage.current_decision_id", return_value=None):
            with pytest.raises(CommanderLineageError, match="LINEAGE_CONTEXT_NOT_AVAILABLE"):
                record_commander_decision(decision)

    def test_03_write_failure_observable(self):
        """C. Write failures are observable."""
        decision = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test",
            decision=DecisionType.WATCH,
        )
        decision.validate()

        with patch("genesis_commander.lineage.current_decision_id", return_value="test-id"):
            with patch("genesis_commander.lineage.record_event", side_effect=RuntimeError("DB error")):
                with pytest.raises(CommanderLineageError, match="COMMANDER_LINEAGE_WRITE_FAILURE"):
                    record_commander_decision(decision)

    def test_04_real_append_only(self):
        """D. Real PostgreSQL append-only proof."""
        from lineage.decision_lineage import create_decision, record_event, get_decision_trace, flush

        decision_id = create_decision(symbol="BTC/USD", strategy="test_append_only")
        flush()

        # First event
        record_event(
            decision_id=decision_id,
            event_type="COMMANDER_DECISION_CREATED",
            stage="DECISION",
            status="INFO",
            component="commander_2.0",
            payload={"test": 1}
        )
        flush()

        trace1 = get_decision_trace(decision_id)
        count1 = len(trace1.get("events", []))

        # Second event
        record_event(
            decision_id=decision_id,
            event_type="COMMANDER_DECISION_CREATED",
            stage="DECISION",
            status="INFO",
            component="commander_2.0",
            payload={"test": 2}
        )
        flush()

        trace2 = get_decision_trace(decision_id)
        count2 = len(trace2.get("events", []))

        # Verify append-only
        assert count2 == count1 + 1

    def test_05_legacy_marker_exact(self):
        """E. LEGACY_DATA_NO_LINEAGE marker persisted exactly in evidence."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        decision_id = create_decision(symbol="DOGE/USD", strategy="test_legacy")
        flush()
        set_current_decision_id(decision_id)

        decision = CommanderDecision(
            subject="legacy data test",
            summary="Using pre-commander data",
            rationale="Historical context",
            decision=DecisionType.WATCH,
            confidence=ConfidenceLevel.LOW,
            evidence=[
                {
                    "source": "LEGACY_DATA_NO_LINEAGE",
                    "note": "pre-v2.0 system record",
                }
            ],
        )

        decision.validate()
        record_commander_decision(decision)
        flush()

        # Retrieve and verify EXACT marker in evidence
        payload = get_commander_decision_from_lineage(decision_id)
        assert payload is not None, "Decision should be retrievable"
        assert payload["evidence_count"] == 1
        assert len(payload["evidence"]) == 1, "Evidence list should be persisted"
        assert payload["evidence"][0]["source"] == "LEGACY_DATA_NO_LINEAGE", \
            "LEGACY_DATA_NO_LINEAGE marker must persist exactly in evidence"

        clear_current_decision()

    def test_06_data_not_available_exact(self):
        """F. DATA_NOT_AVAILABLE marker persisted exactly in data_gaps."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        decision_id = create_decision(symbol="SOL/USD", strategy="test_data_na")
        flush()
        set_current_decision_id(decision_id)

        decision = CommanderDecision(
            subject="data unavailable test",
            summary="RAG offline",
            rationale="Testing graceful degradation",
            decision=DecisionType.INSUFFICIENT_DATA,
            confidence=ConfidenceLevel.NOT_APPLICABLE,
            rag_context_used=[],
            data_gaps=["DATA_NOT_AVAILABLE"],
        )

        decision.validate()
        record_commander_decision(decision)
        flush()

        # Retrieve and verify exact marker
        payload = get_commander_decision_from_lineage(decision_id)
        assert payload is not None
        assert "DATA_NOT_AVAILABLE" in payload.get("data_gaps", []), \
            "DATA_NOT_AVAILABLE marker must be in data_gaps"
        assert payload["rag_context_available"] is False

        clear_current_decision()

    def test_07_roundtrip_exact_values(self):
        """G. Round-trip exact value persistence including IDs and versions."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        decision_id = create_decision(symbol="ETH/USD", strategy="test_roundtrip")
        flush()
        set_current_decision_id(decision_id)

        # Create decision with exact test data
        test_skills = ["momentum_analysis", "support_resistance"]
        test_tools = ["get_market_depth", "get_indicators"]
        test_risks = ["high_volatility"]
        test_gaps = ["DATA_NOT_AVAILABLE"]
        test_evidence = [{"signal": "rsi_oversold", "level": 35}]

        decision = CommanderDecision(
            subject="ETH/USD momentum test",
            symbol="ETH/USD",
            decision=DecisionType.CANDIDATE_LONG,
            confidence=ConfidenceLevel.HIGH,
            summary="Strong uptrend confirmation",
            rationale="Multiple confluences detected",
            evidence=test_evidence,
            skills_used=test_skills,
            mcp_tools_used=test_tools,
            rag_context_used=[{"source": "prior_decision", "score": 0.88}],
            risk_flags=test_risks,
            data_gaps=test_gaps,
            limitations=["1h timeframe"],
        )

        decision.validate()

        # Capture commander identity values BEFORE write
        expected_commander_id = decision.commander_decision_id
        expected_commander_version = decision.commander_version

        # Record the decision
        record_commander_decision(decision)
        flush()

        # Retrieve and verify ALL exact values
        retrieved = get_commander_decision_from_lineage(decision_id)
        assert retrieved is not None, "Decision should be retrievable"

        # Verify identity fields (critical for lineage)
        assert retrieved["commander_decision_id"] == expected_commander_id, \
            "commander_decision_id must persist exactly"
        assert retrieved["commander_version"] == expected_commander_version, \
            "commander_version must persist exactly"

        # Verify decision fields
        assert retrieved["decision"] == "CANDIDATE_LONG"
        assert retrieved["confidence"] == "HIGH"
        assert retrieved["subject"] == "ETH/USD momentum test"
        assert retrieved["symbol"] == "ETH/USD"

        # Verify list fields
        assert retrieved["skills_used"] == test_skills
        assert retrieved["mcp_tools_used"] == test_tools
        assert retrieved["risk_flags"] == test_risks
        assert retrieved["evidence"] == test_evidence, \
            "Evidence list must be persisted exactly"
        assert retrieved["evidence_count"] == len(test_evidence)

        # Verify markers
        assert "DATA_NOT_AVAILABLE" in retrieved.get("data_gaps", [])
        assert retrieved["shadow_only"] is True
        assert retrieved["execution_authority"] == "NONE"

        # Verify full trace retrieval
        full_trace = get_commander_decision_with_lineage(decision_id)
        assert full_trace is not None
        assert "commander_decision" in full_trace
        assert full_trace["commander_decision"]["commander_decision_id"] == expected_commander_id

        clear_current_decision()

    def test_08_invalid_uuid(self):
        """H. Invalid UUID returns None."""
        result = get_commander_decision_from_lineage("not-a-uuid")
        assert result is None

    def test_09_uuid_not_found(self):
        """I. Valid UUID not found returns None."""
        fake_uuid = str(uuid4())
        result = get_commander_decision_from_lineage(fake_uuid)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
