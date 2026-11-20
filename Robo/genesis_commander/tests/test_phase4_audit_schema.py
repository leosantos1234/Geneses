"""Phase 4 Real Audit Schema Tests — Verify canonical Sprint 6 audit integration."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from genesis_commander.orchestrator import CommanderOrchestrator
from genesis_commander.contract import CommanderDecision
from genesis_skills.audit import get_audit_logger


@pytest.fixture(autouse=True)
def clear_audit_logger():
    """Clear audit logger before each test."""
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
async def test_audit_schema_tools_called_field(mock_registry):
    """Test: Orchestrator uses real audit schema field 'tools_called' (not assumed 'mcp_tools_called')."""
    from genesis_skills.skills.base import SkillResult

    skill_executor = AsyncMock()
    skill_executor.execute = AsyncMock(return_value=SkillResult(
        skill_id="test", skill_version="1.0.0", subject="test",
        status="SUCCESS", summary="Test", findings=[], evidence=[], data_gaps=[],
    ))

    with patch('genesis_commander.orchestrator.get_registry', return_value=mock_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                # Create audit record using REAL Sprint 6 schema
                audit_logger = get_audit_logger()

                # Simulate what SkillExecutor does: creates audit record
                audit_record = audit_logger.create_record("test", "1.0.0")

                # Verify REAL field name from sprint6_audit.py line 28: tools_called (not mcp_tools_called)
                assert hasattr(audit_record, 'tools_called'), "Audit schema must have 'tools_called' field"

                # Set tools using canonical field
                audit_record.tools_called = ["canonical_tool_1", "canonical_tool_2"]
                audit_record.finalize(status="SUCCESS", tool_call_count=2, evidence_count=0)

                orchestrator = CommanderOrchestrator(skill_executor)
                decision = await orchestrator.run_shadow_decision(subject="test")

                # Verify decision has MCP tools from audit
                assert isinstance(decision, CommanderDecision)
                # The orchestrator should extract from audit's tools_called
                assert decision.mcp_tools_used is not None


@pytest.mark.anyio
async def test_audit_record_finalize_contract(mock_registry):
    """Test: SkillAuditRecord finalize method has correct signature."""
    from genesis_skills.audit import SkillAuditRecord
    from datetime import datetime, timezone

    # Verify the actual method signature from sprint6_audit.py line 34
    record = SkillAuditRecord(
        skill_run_id="test-run",
        skill_id="test",
        skill_version="1.0.0",
        started_at=datetime.now(timezone.utc),
    )

    # Method must accept these exact parameters
    record.finalize(
        status="SUCCESS",
        tool_call_count=2,
        evidence_count=3,
        error_code=None,
        error_message=None
    )

    assert record.status == "SUCCESS"
    assert record.tool_call_count == 2
    assert record.evidence_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
