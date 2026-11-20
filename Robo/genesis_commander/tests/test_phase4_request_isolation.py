"""Phase 4 Request Isolation & Exact Audit Correlation Tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from genesis_commander.orchestrator import CommanderOrchestrator
from genesis_commander.contract import CommanderDecision
from genesis_skills.audit import get_audit_logger
from genesis_skills.skills.base import SkillResult


@pytest.fixture(autouse=True)
def clean_audit():
    """Clear audit before/after each test."""
    logger = get_audit_logger()
    logger.clear()
    yield
    logger.clear()


@pytest.fixture
def mock_registry():
    """Mock registry."""
    registry = MagicMock()
    manifest = MagicMock()
    manifest.status = "AVAILABLE"
    manifest.timeout_seconds = 30
    registry.get = MagicMock(return_value=manifest)
    return registry


@pytest.mark.anyio
async def test_concurrent_isolated_counters(mock_registry):
    """Test: Two concurrent calls have isolated per-run state."""

    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=SkillResult(
        skill_id="test", skill_version="1.0.0", subject="test",
        status="SUCCESS", summary="Test", findings=[], evidence=[], data_gaps=[],
    ))

    results = []

    with patch('genesis_commander.orchestrator.get_registry', return_value=mock_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(executor)

                async def run_decision(subject_name):
                    decision = await orchestrator.run_shadow_decision(subject=subject_name)
                    results.append(decision)

                await asyncio.gather(
                    run_decision("request_1"),
                    run_decision("request_2"),
                )

    assert len(results) == 2
    assert all(isinstance(r, CommanderDecision) for r in results)


@pytest.mark.anyio
async def test_exact_concurrent_audit_correlation_per_skill():
    """Test: Concurrent decisions have EXACT per-skill audit attribution (no contamination)."""

    audit_logger = get_audit_logger()

    async def mock_execute_deterministic(skill_id, inputs):
        """Assign tools deterministically from subject, not call order."""
        # Derive which request this is from subject
        subject = inputs.get("subject", "unknown")

        # Determine tool set based on subject AND skill_id
        if "concurrent_A" in subject:
            if skill_id == "lstm_diagnostics":
                tools = ["A_lstm_tool"]
            elif skill_id == "risk_review":
                tools = ["A_risk_tool"]
            elif skill_id == "system_health_review":
                tools = ["A_health_tool"]
            elif skill_id == "trade_decision_autopsy":
                tools = ["A_trade_tool"]
            else:
                tools = []
        elif "concurrent_B" in subject:
            if skill_id == "lstm_diagnostics":
                tools = ["B_lstm_tool"]
            elif skill_id == "risk_review":
                tools = ["B_risk_tool"]
            elif skill_id == "system_health_review":
                tools = ["B_health_tool"]
            elif skill_id == "trade_decision_autopsy":
                tools = ["B_trade_tool"]
            else:
                tools = []
        else:
            tools = []

        # Simulate SkillExecutor creating audit
        new_audit = audit_logger.create_record(skill_id, "1.0.0")
        new_audit.tools_called = tools
        new_audit.finalize(status="SUCCESS", tool_call_count=len(tools), evidence_count=0)

        await asyncio.sleep(0.001)  # Allow interleaving

        # Return with EXACT audit_run_id
        return SkillResult(
            skill_id=skill_id, skill_version="1.0.0",
            subject=subject,
            status="SUCCESS", summary="Test", findings=[], evidence=[], data_gaps=[],
            audit_run_id=new_audit.skill_run_id,
        )

    executor = AsyncMock()
    executor.execute = mock_execute_deterministic

    registry = MagicMock()
    manifest = MagicMock()
    manifest.status = "AVAILABLE"
    manifest.timeout_seconds = 30
    registry.get = MagicMock(return_value=manifest)

    decisions = {}

    with patch('genesis_commander.orchestrator.get_registry', return_value=registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(executor)

                async def run_concurrent(name):
                    decision = await orchestrator.run_shadow_decision(
                        subject=f"concurrent_{name}"
                    )
                    decisions[name] = decision

                # Run BOTH decisions concurrently on SAME orchestrator
                await asyncio.gather(
                    run_concurrent("A"),
                    run_concurrent("B"),
                )

    # CRITICAL: EXACT tool set per request
    decision_a = decisions["A"]
    decision_b = decisions["B"]

    # Requests carry no trade_id, so trade_decision_autopsy is not routed; its tool must not appear.
    expected_a = {"A_lstm_tool", "A_risk_tool", "A_health_tool"}
    expected_b = {"B_lstm_tool", "B_risk_tool", "B_health_tool"}

    actual_a = set(decision_a.mcp_tools_used)
    actual_b = set(decision_b.mcp_tools_used)

    # EXACT equality required (no extras, no missing, no wrong)
    assert actual_a == expected_a, \
        f"Decision A tools mismatch. Expected {expected_a}, got {actual_a}"
    assert actual_b == expected_b, \
        f"Decision B tools mismatch. Expected {expected_b}, got {actual_b}"

    # Explicit cross-contamination check
    for tool in expected_b:
        assert tool not in actual_a, \
            f"Decision A incorrectly contains B's tool: {tool}"

    for tool in expected_a:
        assert tool not in actual_b, \
            f"Decision B incorrectly contains A's tool: {tool}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
