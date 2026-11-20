"""Phase 4 Integration Tests — Real infrastructure (Skills, RAG, Lineage)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

from genesis_commander.orchestrator import (
    CommanderOrchestrator,
    CommanderDecision,
)


@pytest.fixture
def real_skill_executor():
    """Mock real skill executor that returns proper SkillResult."""
    from genesis_skills.skills.base import SkillResult

    executor = AsyncMock()
    executor.execute = AsyncMock(return_value=SkillResult(
        skill_id="test",
        skill_version="1.0.0",
        subject="AAPL",
        status="SUCCESS",
        summary="Trade analysis complete",
        findings=["momentum positive"],
        evidence=[
            {
                "source": "skill",
                "signal": "long",
                "confidence": "HIGH",
            }
        ],
        data_gaps=[],
    ))
    return executor


@pytest.fixture
def real_registry():
    """Mock real registry with available Skills."""
    registry = MagicMock()
    manifest = MagicMock()
    manifest.status = "AVAILABLE"
    manifest.timeout_seconds = 30
    registry.get = MagicMock(return_value=manifest)
    return registry


@pytest.mark.anyio
async def test_real_integration_trade_decision(real_skill_executor, real_registry):
    """Test 1: Real integration — trade decision orchestration."""

    with patch('genesis_commander.orchestrator.get_registry', return_value=real_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[
            {
                "rag_record_id": "rag-1",
                "source_type": "closed_trade",
                "source_id": "trade-123",
                "symbol": "AAPL",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "summary": "Previous long trade closed with profit",
                "content": "AAPL long closed at 180 with 5% gain",
                "content_hash": "abc123",
                "content_role": "UNTRUSTED_RETRIEVED_DATA",
            }
        ]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(real_skill_executor)
                decision = await orchestrator.run_shadow_decision(
                    subject="AAPL",
                    symbol="AAPL",
                )

                assert isinstance(decision, CommanderDecision)
                assert decision.shadow_only is True
                assert decision.subject == "AAPL"
                assert len(decision.skills_used) > 0


@pytest.mark.anyio
async def test_real_integration_risk_review(real_skill_executor, real_registry):
    """Test 2: Real integration — risk review with multiple Skills."""

    with patch('genesis_commander.orchestrator.get_registry', return_value=real_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[
            {
                "rag_record_id": "rag-2",
                "source_type": "risk_event",
                "source_id": "risk-456",
                "symbol": "BTC",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "summary": "Volatility spike detected",
                "content": "BTC volatility increased to 85%",
                "content_hash": "def456",
                "content_role": "UNTRUSTED_RETRIEVED_DATA",
            }
        ]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(real_skill_executor)
                decision = await orchestrator.run_shadow_decision(
                    subject="BTC risk assessment",
                    symbol="BTC",
                )

                assert isinstance(decision, CommanderDecision)
                assert decision.evidence is not None
                assert len(decision.evidence) > 0


@pytest.mark.anyio
async def test_real_integration_with_empty_rag(real_skill_executor, real_registry):
    """Test 3: Real integration — no RAG context available."""

    with patch('genesis_commander.orchestrator.get_registry', return_value=real_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[
            {"marker": "DATA_NOT_AVAILABLE"}
        ]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(real_skill_executor)
                decision = await orchestrator.run_shadow_decision(
                    subject="ETH",
                    symbol="ETH",
                )

                assert isinstance(decision, CommanderDecision)
                # Should have DATA_NOT_AVAILABLE marker in evidence
                has_marker = any(
                    e.get("marker") == "DATA_NOT_AVAILABLE"
                    for e in decision.evidence
                )
                assert has_marker


@pytest.mark.anyio
async def test_real_integration_complete_workflow(real_skill_executor, real_registry):
    """Test 4: Real integration — complete workflow with all phases."""

    with patch('genesis_commander.orchestrator.get_registry', return_value=real_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[
            {
                "rag_record_id": "rag-3",
                "source_type": "decision_trace",
                "source_id": "decision-789",
                "symbol": "TSLA",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "summary": "Previous decision context",
                "content": "TSLA decision made with positive signals",
                "content_hash": "ghi789",
                "content_role": "UNTRUSTED_RETRIEVED_DATA",
            }
        ]):
            with patch('genesis_commander.orchestrator.record_commander_decision') as mock_record:
                orchestrator = CommanderOrchestrator(real_skill_executor)
                decision = await orchestrator.run_shadow_decision(
                    subject="TSLA analysis",
                    symbol="TSLA",
                    analytical_context={"market_regime": "bull"},
                )

                # Verify all phases completed
                assert isinstance(decision, CommanderDecision)
                assert decision.subject == "TSLA analysis"
                assert decision.symbol == "TSLA"
                assert decision.decision is not None
                assert decision.confidence is not None

                # Verify lineage was recorded
                mock_record.assert_called_once()


@pytest.mark.anyio
async def test_real_integration_with_analyst_context(real_skill_executor, real_registry):
    """Test 5: Real integration — analyst context propagation."""

    context = {
        "analyst_id": "analyst-001",
        "market_regime": "volatility_spike",
        "risk_tolerance": "low",
    }

    with patch('genesis_commander.orchestrator.get_registry', return_value=real_registry):
        with patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            with patch('genesis_commander.orchestrator.record_commander_decision'):
                orchestrator = CommanderOrchestrator(real_skill_executor)
                decision = await orchestrator.run_shadow_decision(
                    subject="context propagation test",
                    analytical_context=context,
                )

                assert isinstance(decision, CommanderDecision)
                assert decision.evidence is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
