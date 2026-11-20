"""Phase 5 deterministic guardrail tests."""

from genesis_commander.contract import ConfidenceLevel, DecisionType
from genesis_commander.guardrails import (
    GUARDRAIL_BLOCKED,
    GUARDRAIL_INSUFFICIENT_EVIDENCE,
    GUARDRAIL_INVALID_RESULT,
    apply_deterministic_guardrails,
)


def _candidate(**overrides):
    result = {
        "decision": DecisionType.CANDIDATE_LONG,
        "confidence": ConfidenceLevel.MEDIUM,
        "summary": "Long candidate identified by Skills",
        "rationale": "Structured analytical signals suggest a potential opportunity",
    }
    result.update(overrides)
    return result


def test_normal_candidate_is_preserved():
    result = apply_deterministic_guardrails(_candidate(), evidence=[{"source": "skill", "signal": "long"}])
    assert result["decision"] is DecisionType.CANDIDATE_LONG


def test_actionable_without_evidence_fails_closed():
    result = apply_deterministic_guardrails(_candidate(), evidence=[])
    assert result["decision"] is DecisionType.INSUFFICIENT_DATA
    assert GUARDRAIL_INSUFFICIENT_EVIDENCE in result["data_gaps"]


def test_explicit_risk_block_downgrades_candidate():
    result = apply_deterministic_guardrails(_candidate(), evidence=[{"source": "skill"}], risk_flags=["RISK_BLOCKED"])
    assert result["decision"] is DecisionType.NO_ACTION
    assert GUARDRAIL_BLOCKED in result["risk_flags"]


def test_explicit_execution_block_in_evidence_downgrades_candidate():
    result = apply_deterministic_guardrails(_candidate(), evidence=[{"source": "skill", "risk_flags": ["EXECUTION_BLOCK"]}])
    assert result["decision"] is DecisionType.NO_ACTION


def test_invalid_decision_type_fails_closed():
    result = apply_deterministic_guardrails({"decision": "CANDIDATE_LONG"}, evidence=[{}])
    assert result["decision"] is DecisionType.INSUFFICIENT_DATA
    assert GUARDRAIL_INVALID_RESULT in result["risk_flags"]


def test_malformed_evidence_fails_closed():
    result = apply_deterministic_guardrails(_candidate(), evidence=["untrusted"])
    assert result["decision"] is DecisionType.INSUFFICIENT_DATA


def test_operational_language_fails_closed():
    result = apply_deterministic_guardrails(_candidate(summary="BUY now"), evidence=[{}])
    assert result["decision"] is DecisionType.INSUFFICIENT_DATA
    assert GUARDRAIL_INVALID_RESULT in result["risk_flags"]


def test_non_actionable_no_action_does_not_require_evidence():
    result = apply_deterministic_guardrails({
        "decision": DecisionType.NO_ACTION,
        "confidence": ConfidenceLevel.LOW,
        "summary": "No clear signal",
        "rationale": "Evidence does not support a decision",
    })
    assert result["decision"] is DecisionType.NO_ACTION


def test_guardrails_do_not_mutate_input():
    source = _candidate()
    apply_deterministic_guardrails(source, evidence=[{}], risk_flags=[])
    assert "risk_flags" not in source

