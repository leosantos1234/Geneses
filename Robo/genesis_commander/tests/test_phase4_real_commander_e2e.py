"""Phase 4 real Commander end-to-end — no patches for MCP, registry, RAG or lineage.

Real components: canonical registry, SkillExecutor, eligible Skills, owner-task MCP adapter,
Phase 3 RAG (PostgreSQL), Phase 2 lineage (decision_trace / decision_events).
"""

import asyncio

import pytest

from genesis_commander.contract import ConfidenceLevel, DecisionType, ExecutionAuthority
from genesis_commander.lineage import get_commander_decision_from_lineage
from genesis_commander.orchestrator import CommanderOrchestrator, SKILL_NOT_APPLICABLE_TRADE_ID_MARKER
from genesis_skills.audit import AuditStatus, get_audit_logger
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.executor import SkillExecutor
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.skills import (
    TradeDecisionAutopsySkill,
    RiskReviewSkill,
    LSTMDiagnosticsSkill,
    SystemHealthReviewSkill,
)
from lineage.decision_lineage import create_decision, flush, get_decision_trace

APPROVED_SKILLS = {"trade_decision_autopsy", "risk_review", "lstm_diagnostics", "system_health_review"}
EXECUTABLE_WITHOUT_TRADE = APPROVED_SKILLS - {"trade_decision_autopsy"}
DECISION_ENUM = {member.value for member in DecisionType}
CONFIDENCE_ENUM = {member.value for member in ConfidenceLevel}


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


@pytest.mark.anyio
async def test_real_commander_end_to_end_with_real_rag_and_lineage_roundtrip():
    get_audit_logger().clear()
    adapter = MCPReadOnlyClientAdapter()
    executor = SkillExecutor(
        mcp_client=adapter,
        skill_classes={
            "trade_decision_autopsy": TradeDecisionAutopsySkill,
            "risk_review": RiskReviewSkill,
            "lstm_diagnostics": LSTMDiagnosticsSkill,
            "system_health_review": SystemHealthReviewSkill,
        },
    )
    captured = []
    real_execute = executor.execute

    async def recording_execute(skill_id, inputs):
        result = await real_execute(skill_id, inputs)
        captured.append(result)
        return result

    executor.execute = recording_execute
    orchestrator = CommanderOrchestrator(executor)

    decision_id = create_decision(
        strategy="commander_2.0",
        mode="shadow",
        context={"acceptance": "phase4_real_commander_e2e"},
    )
    try:
        decision = await orchestrator.run_shadow_decision(subject="phase4_e2e")
    finally:
        await orchestrator.close()
    assert adapter.is_running is False

    # A-D: shadow-only, no execution authority, enum-bounded outputs
    assert decision.shadow_only is True
    assert decision.execution_authority == ExecutionAuthority.NONE
    assert decision.decision.value in DECISION_ENUM
    assert decision.confidence.value in CONFIDENCE_ENUM

    # F: only approved, eligible Skills; autopsy skipped deterministically (no trade_id)
    assert {r.skill_id for r in captured} == EXECUTABLE_WITHOUT_TRADE
    assert set(decision.skills_used) == EXECUTABLE_WITHOUT_TRADE
    assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision.data_gaps
    for result in captured:
        assert result.status in ("SUCCESS", "PARTIAL"), f"{result.skill_id}: {result.summary}"

    # G: MCP accounting exact via audit_run_id
    audit = get_audit_logger()
    expected_tools = set()
    for result in captured:
        assert result.audit_run_id is not None
        record = audit.get_record(result.audit_run_id)
        assert record is not None and record.status == AuditStatus.SUCCESS.value
        expected_tools |= set(record.tools_called)
    assert expected_tools and expected_tools <= adapter.ALLOWED_TOOLS
    assert set(decision.mcp_tools_used) == expected_tools

    # H: RAG context is real provenance-backed context OR exact DATA_NOT_AVAILABLE
    if decision.rag_context_used:
        for prov in decision.rag_context_used:
            for field in ("rag_record_id", "source_type", "source_id", "timestamp_utc", "content_hash"):
                assert prov.get(field), f"RAG provenance field missing: {field}"
            assert prov["content_role"] == "UNTRUSTED_RETRIEVED_DATA"
    else:
        assert any(e.get("marker") == "DATA_NOT_AVAILABLE" for e in decision.evidence)

    # I/J: lineage persistence and exact roundtrip through the canonical lineage API
    assert flush(timeout=15), "lineage writes did not drain"
    payload = get_commander_decision_from_lineage(decision_id)
    assert payload is not None, "persisted COMMANDER_DECISION_CREATED payload must be retrievable"
    assert payload["commander_decision_id"] == decision.commander_decision_id
    assert payload["commander_version"] == decision.commander_version
    assert payload["timestamp_utc"] == decision.timestamp_utc.isoformat()
    assert payload["subject"] == decision.subject
    assert payload["symbol"] == decision.symbol
    assert payload["decision"] == decision.decision.value
    assert payload["confidence"] == decision.confidence.value
    assert payload["shadow_only"] is True
    assert payload["execution_authority"] == "NONE"

    trace = get_decision_trace(decision_id)
    assert trace is not None
    event_types = [event["event_type"] for event in trace["events"]]
    assert "COMMANDER_DECISION_CREATED" in event_types
