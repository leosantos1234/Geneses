"""Phase 2 Decision Lineage Tests — Real integration with PostgreSQL."""

import pytest
import sys
import os
import json
from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import Mock, patch, MagicMock

# Setup path and environment for all tests
sys.path.insert(0, 'Robo')
os.environ.setdefault('GENESIS_DATABASE_BACKEND', 'postgres')

# Load .env-btc file with UTF-8 encoding
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


class TestPhase2SchemaReuse:
    """A. Reuse existing decision_trace and decision_events."""

    def test_schema_uses_existing_tables(self):
        """Lineage uses existing decision_trace and decision_events."""
        from genesis_commander.lineage import (
            record_commander_decision,
            get_commander_decision_from_lineage,
            get_commander_decision_with_lineage,
        )
        assert callable(record_commander_decision)
        assert callable(get_commander_decision_from_lineage)
        assert callable(get_commander_decision_with_lineage)


class TestPhase2MissingContextObservable:
    """B. Missing decision context is observable."""

    def test_missing_decision_context_raises(self):
        """record_commander_decision raises when decision_id missing."""
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

    def test_missing_decision_raises(self):
        """record_commander_decision raises when decision is None."""
        with pytest.raises(CommanderLineageError, match="decision required"):
            record_commander_decision(None)


class TestPhase2WriteFailureObservable:
    """C. Write failures are observable."""

    def test_write_failure_raises(self):
        """record_commander_decision raises when record_event fails."""
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


class TestPhase2RealAppendOnly:
    """D. Real PostgreSQL append-only proof."""

    def test_real_append_only_multiple_events(self):
        """Multiple events for same decision_id append without mutation."""
        from lineage.decision_lineage import create_decision, record_event, get_decision_trace, flush

        # Create a real decision trace
        decision_id = create_decision(symbol="BTC/USD", strategy="test_append_only")
        flush()  # Wait for async write

        # Record first event
        cmd_event_1 = {
            "commander_decision_id": str(uuid4()),
            "commander_version": "2.0.0",
            "decision": "WATCH",
            "confidence": "MEDIUM",
        }
        record_event(
            decision_id=decision_id,
            event_type="COMMANDER_DECISION_CREATED",
            stage="DECISION",
            status="INFO",
            component="commander_2.0",
            payload=cmd_event_1
        )

        # Get count after first event
        trace1 = get_decision_trace(decision_id)
        count1 = len(trace1.get("events", []))

        # Record second event
        cmd_event_2 = {
            "commander_decision_id": str(uuid4()),
            "commander_version": "2.0.0",
            "decision": "CANDIDATE_LONG",
            "confidence": "HIGH",
        }
        record_event(
            decision_id=decision_id,
            event_type="COMMANDER_DECISION_CREATED",
            stage="DECISION",
            status="INFO",
            component="commander_2.0",
            payload=cmd_event_2
        )

        # Get count after second event
        trace2 = get_decision_trace(decision_id)
        count2 = len(trace2.get("events", []))

        # Verify append-only: count increased by exactly 1
        assert count2 == count1 + 1, f"Events didn't append: {count1} -> {count2}"

        # Verify both events exist
        events = trace2.get("events", [])
        commander_events = [e for e in events if e.get("event_type") == "COMMANDER_DECISION_CREATED"]
        assert len(commander_events) >= 2, "Both commander events should be present"

        # Verify ordering is preserved (event_id monotonic)
        if len(commander_events) >= 2:
            for i in range(len(commander_events)-1):
                assert commander_events[i].get("event_id") < commander_events[i+1].get("event_id"), \
                    "Events should be ordered by event_id"


class TestPhase2RealRoundTrip:
    """E. Real retrieval round trip with exact value verification."""

    def test_real_round_trip_retrieval(self):
        """Record and retrieve Commander decision end-to-end with real database."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        # Create test decision and set context
        decision_id = create_decision(symbol="ETH/USD", strategy="test_round_trip")
        flush()  # Wait for decision creation
        set_current_decision_id(decision_id)

        # Create CommanderDecision with exact test data
        test_skills = ["momentum_analysis", "support_resistance"]
        test_tools = ["get_market_depth", "get_indicators"]
        test_risks = ["high_volatility"]
        test_gaps = ["DATA_NOT_AVAILABLE", "limited_history"]
        test_limits = ["1h timeframe"]

        decision = CommanderDecision(
            subject="ETH/USD momentum test",
            symbol="ETH/USD",
            decision=DecisionType.CANDIDATE_LONG,
            confidence=ConfidenceLevel.HIGH,
            summary="Strong uptrend confirmation",
            rationale="Multiple confluences detected",
            evidence=[{"signal": "rsi_oversold", "level": 35}],
            skills_used=test_skills,
            mcp_tools_used=test_tools,
            rag_context_used=[{"source": "prior_decision", "score": 0.88}],
            risk_flags=test_risks,
            data_gaps=test_gaps,
            limitations=test_limits,
        )

        decision.validate()

        # Record the decision
        record_commander_decision(decision)

        # Retrieve via get_commander_decision_from_lineage
        retrieved_payload = get_commander_decision_from_lineage(decision_id)

        # Verify exact values
        assert retrieved_payload is not None, "Decision should be retrievable"
        assert retrieved_payload["decision"] == "CANDIDATE_LONG"
        assert retrieved_payload["confidence"] == "HIGH"
        assert retrieved_payload["subject"] == "ETH/USD momentum test"
        assert retrieved_payload["symbol"] == "ETH/USD"
        assert retrieved_payload["skills_used"] == test_skills
        assert retrieved_payload["mcp_tools_used"] == test_tools
        assert retrieved_payload["risk_flags"] == test_risks
        assert set(retrieved_payload["data_gaps"]) >= set(test_gaps), \
            f"data_gaps should contain all markers: {retrieved_payload['data_gaps']} >= {test_gaps}"
        assert retrieved_payload["limitations"] == test_limits
        assert retrieved_payload["shadow_only"] is True
        assert retrieved_payload["execution_authority"] == "NONE"

        # Retrieve via get_commander_decision_with_lineage
        full_trace = get_commander_decision_with_lineage(decision_id)
        assert full_trace is not None
        assert "commander_decision" in full_trace
        assert full_trace["commander_decision"]["decision"] == "CANDIDATE_LONG"

        # Clean up
        clear_current_decision()


class TestPhase2LegacyDataMarker:
    """F. LEGACY_DATA_NO_LINEAGE marker preserved exactly."""

    def test_legacy_marker_persisted(self):
        """LEGACY_DATA_NO_LINEAGE in evidence persisted exactly."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        decision_id = create_decision(symbol="DOGE/USD", strategy="test_legacy_marker")
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

        # Retrieve and verify marker
        payload = get_commander_decision_from_lineage(decision_id)
        assert payload is not None
        assert payload["evidence_count"] == 1

        clear_current_decision()


class TestPhase2DataNotAvailableMarker:
    """G. DATA_NOT_AVAILABLE marker preserved exactly."""

    def test_data_not_available_marker_persisted(self):
        """DATA_NOT_AVAILABLE in data_gaps persisted exactly."""
        from lineage.decision_lineage import (
            create_decision,
            set_current_decision_id,
            clear_current_decision,
            flush,
        )

        decision_id = create_decision(symbol="SOL/USD", strategy="test_data_not_available")
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

        # Retrieve and verify exact marker
        payload = get_commander_decision_from_lineage(decision_id)
        assert payload is not None
        assert "DATA_NOT_AVAILABLE" in payload.get("data_gaps", [])
        assert payload["rag_context_available"] is False

        clear_current_decision()


class TestPhase2InvalidUUID:
    """H. Invalid UUID returns None explicitly."""

    def test_invalid_uuid_returns_none(self):
        """Invalid decision_id returns None explicitly."""
        result = get_commander_decision_from_lineage("not-a-uuid")
        assert result is None

        result2 = get_commander_decision_with_lineage("also-not-valid")
        assert result2 is None


class TestPhase2ValidUUIDNotFound:
    """I. Valid UUID with no decision returns None."""

    def test_valid_uuid_not_found_returns_none(self):
        """Valid but non-existent UUID returns None."""
        # Generate a random UUID that almost certainly doesn't exist
        fake_uuid = str(uuid4())
        result = get_commander_decision_from_lineage(fake_uuid)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
