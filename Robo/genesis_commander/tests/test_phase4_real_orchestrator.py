"""Phase 4 Real Orchestrator Test - real registry, executor and MCP adapter; no swallowed failures.

MCP_ISOLATED_TEST: RAG retrieval and lineage persistence are patched in the decision test.
"""

from unittest.mock import patch

import pytest

from genesis_commander.orchestrator import get_orchestrator, close_orchestrator
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter


@pytest.fixture(scope="module", autouse=True)
def setup():
    bootstrap_default_registry()
    yield


@pytest.mark.anyio
async def test_real_orchestrator_has_bootstrapped_skills():
    orchestrator = get_orchestrator()
    try:
        expected = {
            "trade_decision_autopsy",
            "risk_review",
            "lstm_diagnostics",
            "system_health_review",
        }
        assert set(orchestrator.skill_executor.skill_classes.keys()) == expected
    finally:
        await close_orchestrator()


@pytest.mark.anyio
async def test_real_orchestrator_shadow_only_mcp_isolated():
    orchestrator = get_orchestrator()
    try:
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            decision = await orchestrator.run_shadow_decision(subject="test_shadow")
        assert decision.shadow_only is True
        assert decision.execution_authority.value == "NONE"
    finally:
        await close_orchestrator()


@pytest.mark.anyio
async def test_real_orchestrator_adapter_is_real_mcp_adapter():
    orchestrator = get_orchestrator()
    try:
        adapter = orchestrator.skill_executor.mcp_client
        assert isinstance(adapter, MCPReadOnlyClientAdapter)
        assert "get_system_health" in adapter.ALLOWED_TOOLS
        assert adapter.is_running is False
    finally:
        await close_orchestrator()
