"""Commander 2.0 Contract Tests — Phase 1 Validation"""

import pytest
from datetime import datetime, timezone, timedelta
from genesis_commander.contract import (
    CommanderDecision,
    DecisionType,
    ConfidenceLevel,
    ExecutionAuthority,
    ShadowEvaluation,
)


class TestCommanderDecisionSchema:
    """A. Decision schema valid."""

    def test_valid_decision_schema(self):
        """Valid decision contract with all required fields."""
        decision = CommanderDecision(
            subject="test",
            symbol="BTC/USD",
            decision=DecisionType.WATCH,
            confidence=ConfidenceLevel.MEDIUM,
            summary="Test summary",
            rationale="Test rationale",
        )

        assert decision.validate()
        assert decision.shadow_only is True
        assert decision.execution_authority == ExecutionAuthority.NONE


class TestDecisionEnum:
    """B. Enum restricted to allowed values."""

    def test_allowed_decisions(self):
        """Only allowed decision types."""
        allowed = [
            DecisionType.NO_ACTION,
            DecisionType.WATCH,
            DecisionType.CANDIDATE_LONG,
            DecisionType.CANDIDATE_SHORT,
            DecisionType.INSUFFICIENT_DATA,
        ]

        for decision_type in allowed:
            d = CommanderDecision(decision=decision_type)
            assert d.decision == decision_type

    def test_no_operational_verbs_in_enum(self):
        """No BUY/SELL/OPEN/CLOSE/CANCEL."""
        enum_values = [e.value for e in DecisionType]
        assert "BUY" not in enum_values
        assert "SELL" not in enum_values
        assert "OPEN" not in enum_values
        assert "CLOSE" not in enum_values
        assert "CANCEL" not in enum_values


class TestConfidenceEnum:
    """C. Confidence categorical, not numeric."""

    def test_confidence_is_enum(self):
        """Confidence must be ConfidenceLevel enum."""
        allowed = [
            ConfidenceLevel.HIGH,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.LOW,
            ConfidenceLevel.NOT_APPLICABLE,
        ]

        for conf in allowed:
            d = CommanderDecision(
                subject="test",
                summary="Test",
                rationale="Test",
                confidence=conf
            )
            assert isinstance(d.confidence, ConfidenceLevel)
            assert d.validate()

    def test_numeric_confidence_rejected(self):
        """Numeric confidence (float) rejected."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test"
        )
        d.confidence = 0.85

        with pytest.raises(ValueError):
            d.validate()


class TestShadowOnly:
    """D. shadow_only mandatory true."""

    def test_shadow_only_default_true(self):
        """shadow_only defaults to True."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test"
        )
        assert d.shadow_only is True

    def test_shadow_only_false_rejected(self):
        """shadow_only=false rejected."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test",
            shadow_only=False
        )

        with pytest.raises(ValueError, match="shadow_only must be True"):
            d.validate()


class TestExecutionAuthority:
    """E. execution_authority mandatory NONE."""

    def test_execution_authority_default_none(self):
        """execution_authority defaults to NONE."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test"
        )
        assert d.execution_authority == ExecutionAuthority.NONE

    def test_execution_authority_none_only(self):
        """execution_authority only NONE allowed."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test"
        )
        assert d.validate()
        assert d.execution_authority == ExecutionAuthority.NONE


class TestOperationalLanguageRejected:
    """F. Operational language (BUY/SELL/OPEN/CLOSE/CANCEL) rejected."""

    def test_buy_rejected(self):
        """BUY in text rejected."""
        d = CommanderDecision(
            subject="test",
            summary="We should BUY this",
            rationale="Consider this action"
        )

        with pytest.raises(ValueError, match="Operational verb 'BUY' not allowed"):
            d.validate()

    def test_sell_rejected(self):
        """SELL in text rejected."""
        d = CommanderDecision(
            subject="test",
            summary="Consider action",
            rationale="Plan to SELL position"
        )

        with pytest.raises(ValueError, match="Operational verb 'SELL' not allowed"):
            d.validate()

    def test_cancel_rejected(self):
        """CANCEL in text rejected."""
        d = CommanderDecision(
            subject="test",
            summary="CANCEL all orders",
            rationale="Consider this"
        )

        with pytest.raises(ValueError, match="Operational verb 'CANCEL' not allowed"):
            d.validate()

    def test_word_boundary_avoids_false_positive(self):
        """Word boundary avoids false positive (OPENED should not match OPEN)."""
        d = CommanderDecision(
            subject="test",
            summary="Market OPENED higher today",
            rationale="Price movement OPENED new opportunities"
        )

        # Should NOT raise error - OPENED is not the verb OPEN
        assert d.validate()


class TestInsufficientDataWatchFailClosed:
    """G. WATCH + no evidence -> INSUFFICIENT_DATA."""

    def test_watch_no_evidence_fail_closed(self):
        """WATCH with no evidence normalizes to INSUFFICIENT_DATA."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.WATCH,
            evidence=[],
            summary="Watch for changes",
            rationale="Monitor situation"
        )

        # validate() should normalize WATCH to INSUFFICIENT_DATA
        d.validate()
        assert d.decision == DecisionType.INSUFFICIENT_DATA
        assert d.confidence == ConfidenceLevel.NOT_APPLICABLE
        assert "INSUFFICIENT_EVIDENCE" in d.data_gaps


class TestInsufficientDataCandidateLongFailClosed:
    """H. CANDIDATE_LONG + no evidence -> INSUFFICIENT_DATA."""

    def test_candidate_long_no_evidence_fail_closed(self):
        """CANDIDATE_LONG with no evidence normalizes to INSUFFICIENT_DATA."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.CANDIDATE_LONG,
            evidence=[],
            summary="Consider long position",
            rationale="Bullish setup detected"
        )

        d.validate()
        assert d.decision == DecisionType.INSUFFICIENT_DATA
        assert d.confidence == ConfidenceLevel.NOT_APPLICABLE
        assert "INSUFFICIENT_EVIDENCE" in d.data_gaps


class TestInsufficientDataCandidateShortFailClosed:
    """I. CANDIDATE_SHORT + no evidence -> INSUFFICIENT_DATA."""

    def test_candidate_short_no_evidence_fail_closed(self):
        """CANDIDATE_SHORT with no evidence normalizes to INSUFFICIENT_DATA."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.CANDIDATE_SHORT,
            evidence=[],
            summary="Consider short position",
            rationale="Bearish setup detected"
        )

        d.validate()
        assert d.decision == DecisionType.INSUFFICIENT_DATA
        assert d.confidence == ConfidenceLevel.NOT_APPLICABLE
        assert "INSUFFICIENT_EVIDENCE" in d.data_gaps


class TestCandidateWithEvidenceRemainCandidate:
    """J. Candidate with evidence remains candidate."""

    def test_candidate_long_with_evidence(self):
        """CANDIDATE_LONG with evidence remains CANDIDATE_LONG."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.CANDIDATE_LONG,
            evidence=[{"signal": "rsi_oversold", "strength": 0.8}],
            summary="Strong long signal",
            rationale="Technical analysis supports"
        )

        d.validate()
        assert d.decision == DecisionType.CANDIDATE_LONG

    def test_watch_with_evidence(self):
        """WATCH with evidence remains WATCH."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.WATCH,
            evidence=[{"signal": "pivot_level", "price": 50000}],
            summary="Monitor pivot",
            rationale="Key level identified"
        )

        d.validate()
        assert d.decision == DecisionType.WATCH


class TestRAGDataNotAvailable:
    """K. RAG unavailable -> exact DATA_NOT_AVAILABLE path."""

    def test_rag_empty_adds_marker(self):
        """Empty RAG context adds DATA_NOT_AVAILABLE marker."""
        d = CommanderDecision(
            subject="test",
            rag_context_used=[],
            summary="Test",
            rationale="Test"
        )

        d.validate()
        assert "DATA_NOT_AVAILABLE" in d.data_gaps

    def test_rag_populated_no_marker(self):
        """Populated RAG context does not add DATA_NOT_AVAILABLE marker."""
        rag_record = {
            "rag_record_id": "abc123",
            "source_type": "prior_decision",
            "score": 0.85,
        }
        d = CommanderDecision(
            subject="test",
            rag_context_used=[rag_record],
            summary="Test",
            rationale="Test"
        )

        d.validate()
        assert "DATA_NOT_AVAILABLE" not in d.data_gaps


class TestRequiredFields:
    """L. Required fields non-empty."""

    def test_empty_commander_decision_id_rejected(self):
        """Empty commander_decision_id rejected."""
        d = CommanderDecision(
            commander_decision_id="",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="commander_decision_id required"):
            d.validate()

    def test_empty_commander_version_rejected(self):
        """Empty commander_version rejected."""
        d = CommanderDecision(
            commander_version="",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="commander_version required"):
            d.validate()

    def test_empty_subject_rejected(self):
        """Empty subject rejected."""
        d = CommanderDecision(
            subject="",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="subject required"):
            d.validate()

    def test_empty_summary_rejected(self):
        """Empty summary rejected."""
        d = CommanderDecision(
            subject="test",
            summary="",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="summary required"):
            d.validate()

    def test_empty_rationale_rejected(self):
        """Empty rationale rejected."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale=""
        )

        with pytest.raises(ValueError, match="rationale required"):
            d.validate()


class TestTimestampValidation:
    """M. Timestamp must be UTC."""

    def test_naive_timestamp_rejected(self):
        """Naive datetime (no timezone) rejected."""
        d = CommanderDecision(
            timestamp_utc=datetime(2026, 1, 1, 12, 0, 0),
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="must be timezone-aware"):
            d.validate()

    def test_utc_timestamp_accepted(self):
        """UTC timezone accepted."""
        d = CommanderDecision(
            timestamp_utc=datetime.now(timezone.utc),
            subject="test",
            summary="Test",
            rationale="Test"
        )

        assert d.validate()

    def test_non_utc_timezone_rejected(self):
        """Non-UTC timezone (e.g., EST) rejected."""
        est = timezone(timedelta(hours=-5))
        d = CommanderDecision(
            timestamp_utc=datetime.now(est),
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="must be UTC"):
            d.validate()


class TestCollectionTypes:
    """N. Collection types must be lists with proper items."""

    def test_evidence_not_list_rejected(self):
        """evidence must be list."""
        d = CommanderDecision(
            evidence="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="evidence must be list"):
            d.validate()

    def test_evidence_with_non_dict_items_rejected(self):
        """evidence items must be dicts."""
        d = CommanderDecision(
            evidence=["string item"],
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="evidence items must be dicts"):
            d.validate()

    def test_rag_context_not_list_rejected(self):
        """rag_context_used must be list."""
        d = CommanderDecision(
            rag_context_used="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="rag_context_used must be list"):
            d.validate()

    def test_rag_context_with_non_dict_items_rejected(self):
        """rag_context_used items must be dicts."""
        d = CommanderDecision(
            rag_context_used=["string item"],
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="rag_context_used items must be dicts"):
            d.validate()

    def test_skills_used_not_list_rejected(self):
        """skills_used must be list."""
        d = CommanderDecision(
            skills_used="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="skills_used must be list"):
            d.validate()

    def test_risk_flags_not_list_rejected(self):
        """risk_flags must be list."""
        d = CommanderDecision(
            risk_flags="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="risk_flags must be list"):
            d.validate()

    def test_data_gaps_not_list_rejected(self):
        """data_gaps must be list."""
        d = CommanderDecision(
            data_gaps="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="data_gaps must be list"):
            d.validate()

    def test_limitations_not_list_rejected(self):
        """limitations must be list."""
        d = CommanderDecision(
            limitations="not a list",
            subject="test",
            summary="Test",
            rationale="Test"
        )

        with pytest.raises(ValueError, match="limitations must be list"):
            d.validate()


class TestShadowEvaluationSerialization:
    """O. ShadowEvaluation serialization with DATA_NOT_AVAILABLE."""

    def test_missing_reference_price_serialized(self):
        """Missing reference_price -> DATA_NOT_AVAILABLE."""
        eval = ShadowEvaluation(
            decision_id="d1",
            decision_timestamp=datetime.now(timezone.utc),
            decision="WATCH",
            symbol="BTC/USD",
            reference_price=None
        )

        d = eval.to_dict()
        assert d["reference_price"] == "DATA_NOT_AVAILABLE"

    def test_missing_outcome_timestamp_serialized(self):
        """Missing outcome_timestamp -> DATA_NOT_AVAILABLE."""
        eval = ShadowEvaluation(
            decision_id="d1",
            decision_timestamp=datetime.now(timezone.utc),
            decision="WATCH",
            symbol="BTC/USD",
            outcome_timestamp=None
        )

        d = eval.to_dict()
        assert d["outcome_timestamp"] == "DATA_NOT_AVAILABLE"

    def test_missing_direction_correct_serialized(self):
        """Missing direction_correct -> DATA_NOT_AVAILABLE."""
        eval = ShadowEvaluation(
            decision_id="d1",
            decision_timestamp=datetime.now(timezone.utc),
            decision="WATCH",
            symbol="BTC/USD",
            direction_correct=None
        )

        d = eval.to_dict()
        assert d["direction_correct"] == "DATA_NOT_AVAILABLE"

    def test_missing_notes_serialized(self):
        """Missing notes -> DATA_NOT_AVAILABLE."""
        eval = ShadowEvaluation(
            decision_id="d1",
            decision_timestamp=datetime.now(timezone.utc),
            decision="WATCH",
            symbol="BTC/USD",
            notes=None
        )

        d = eval.to_dict()
        assert d["notes"] == "DATA_NOT_AVAILABLE"

    def test_available_fields_preserved(self):
        """Available fields are preserved in serialization."""
        ts = datetime.now(timezone.utc)
        eval = ShadowEvaluation(
            decision_id="d1",
            decision_timestamp=ts,
            decision="WATCH",
            symbol="BTC/USD",
            reference_price=50000.0,
            outcome_price=51000.0,
            return_pct=2.0
        )

        d = eval.to_dict()
        assert d["reference_price"] == 50000.0
        assert d["outcome_price"] == 51000.0
        assert d["return_pct"] == 2.0


class TestToDict:
    """P. Convert to dict for persistence."""

    def test_to_dict_serializable(self):
        """Decision serializable to dict."""
        d = CommanderDecision(
            subject="test",
            decision=DecisionType.WATCH,
            confidence=ConfidenceLevel.HIGH,
        )

        d_dict = d.to_dict()

        assert d_dict["commander_decision_id"] == d.commander_decision_id
        assert d_dict["decision"] == "WATCH"
        assert d_dict["confidence"] == "HIGH"
        assert d_dict["shadow_only"] is True
        assert d_dict["execution_authority"] == "NONE"


class TestDefaults:
    """Q. Verify all defaults correct."""

    def test_all_defaults_valid(self):
        """Default decision is valid."""
        d = CommanderDecision(
            subject="test",
            summary="Test",
            rationale="Test"
        )

        assert d.validate()
        assert d.shadow_only is True
        assert d.execution_authority == ExecutionAuthority.NONE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
