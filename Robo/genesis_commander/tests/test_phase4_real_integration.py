"""Phase 4 real canonical integration tests — strict, no swallowed failures.

MCP_ISOLATED_TEST: decision tests patch RAG retrieval and lineage persistence only.
Registry, executor, MCP adapter and the four Skill classes are real.
"""

from unittest.mock import patch

import pytest

from genesis_commander.orchestrator import (
    get_orchestrator,
    close_orchestrator,
    SKILL_NOT_APPLICABLE_TRADE_ID_MARKER,
)
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.executor import SkillExecutor
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.registry import get_registry
from genesis_skills.skills import (
    TradeDecisionAutopsySkill,
    RiskReviewSkill,
    LSTMDiagnosticsSkill,
    SystemHealthReviewSkill,
)

APPROVED_SKILLS = {
    "trade_decision_autopsy",
    "risk_review",
    "lstm_diagnostics",
    "system_health_review",
}


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


@pytest.mark.anyio
async def test_real_registry_has_approved_skills_available():
    registry = get_registry()
    for skill_id in APPROVED_SKILLS:
        manifest = registry.get(skill_id)
        assert manifest.status.value == "AVAILABLE", f"{skill_id} is {manifest.status.value}"


@pytest.mark.anyio
async def test_real_executor_with_real_registry_and_skill_classes():
    adapter = MCPReadOnlyClientAdapter()
    skill_classes = {
        "trade_decision_autopsy": TradeDecisionAutopsySkill,
        "risk_review": RiskReviewSkill,
        "lstm_diagnostics": LSTMDiagnosticsSkill,
        "system_health_review": SystemHealthReviewSkill,
    }
    executor = SkillExecutor(mcp_client=adapter, skill_classes=skill_classes)
    try:
        assert executor.registry is get_registry()
        assert set(executor.skill_classes) == APPROVED_SKILLS
    finally:
        await executor.close()


@pytest.mark.anyio
async def test_real_decision_is_shadow_only_mcp_isolated():
    orchestrator = get_orchestrator()
    try:
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            decision = await orchestrator.run_shadow_decision(subject="test_shadow")
        assert decision.shadow_only is True
        assert decision.execution_authority.value == "NONE"
        assert set(decision.skills_used) == APPROVED_SKILLS - {"trade_decision_autopsy"}
        assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision.data_gaps
    finally:
        await close_orchestrator()


@pytest.mark.anyio
async def test_real_decision_starts_mcp_lazily_and_close_chain_stops_it():
    orchestrator = get_orchestrator()
    adapter = orchestrator.skill_executor.mcp_client
    try:
        assert adapter.is_running is False
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            await orchestrator.run_shadow_decision(subject="test_lazy_start")
        assert adapter.is_running is True
    finally:
        await orchestrator.close()
    assert adapter.is_running is False
