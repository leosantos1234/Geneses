"""Phase 4 Commander timeout — deterministic, fail-closed, observable, no process leak."""

import asyncio
import time
from unittest.mock import patch

import mcp.client.stdio as stdio_mod
import pytest

import genesis_commander.orchestrator as orchestrator_module
from genesis_commander.orchestrator import CommanderOrchestrator, CommanderOrchestratorError
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.executor import SkillExecutor
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.skills import (
    TradeDecisionAutopsySkill,
    RiskReviewSkill,
    LSTMDiagnosticsSkill,
    SystemHealthReviewSkill,
)


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


@pytest.mark.anyio
async def test_commander_timeout_is_fail_closed_observable_and_leak_free(monkeypatch):
    processes = []
    real_create_process = stdio_mod._create_platform_compatible_process

    async def capture_process(*args, **kwargs):
        proc = await real_create_process(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(stdio_mod, "_create_platform_compatible_process", capture_process)
    monkeypatch.setattr(orchestrator_module, "ORCHESTRATOR_TIMEOUT_SECONDS", 0.05)

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
    orchestrator = CommanderOrchestrator(executor)

    # Cold MCP startup uses the normal timeout; only the orchestration deadline is shortened.
    warm = await adapter.call("get_system_health", {})
    assert warm.success, warm.error_message

    slow_calls = []

    async def slow_execute(skill_id, inputs):
        slow_calls.append(skill_id)
        await asyncio.sleep(5)

    executor.execute = slow_execute

    with patch('genesis_commander.orchestrator.record_commander_decision') as lineage_write:
        started = time.monotonic()
        with pytest.raises(CommanderOrchestratorError, match="timed out"):
            await orchestrator.run_shadow_decision(subject="phase4_timeout")
        elapsed = time.monotonic() - started

    assert elapsed < 2.0, f"orchestration did not stop at the deadline (elapsed={elapsed:.3f}s)"
    assert lineage_write.call_count == 0, "a timed-out decision must not be persisted"
    assert slow_calls, "the slow Skill path must have been entered"

    await orchestrator.close()
    assert adapter.is_running is False
    assert len(processes) == 1
    assert processes[0].returncode is not None, "STDIO process leaked after timeout"
