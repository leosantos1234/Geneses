"""Phase 4 Commander lineage persistence observability — fail-closed, no swallowed write failures.

Test A uses real PostgreSQL end to end. Tests B and C inject a failure only at the Commander
lineage persistence boundary (the COMMANDER_DECISION_CREATED write, or its confirmation read);
the real database is never altered by the injected failure.
"""

import mcp.client.stdio as stdio_mod
import pytest

import genesis_commander.lineage as commander_lineage
from genesis_commander.contract import CommanderDecision, ConfidenceLevel, DecisionType
from genesis_commander.lineage import (
    CommanderLineageError,
    CommanderLineagePersistenceError,
    get_commander_decision_from_lineage,
    record_commander_decision,
)
from genesis_commander.orchestrator import CommanderOrchestrator
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.executor import SkillExecutor
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.skills import (
    TradeDecisionAutopsySkill,
    RiskReviewSkill,
    LSTMDiagnosticsSkill,
    SystemHealthReviewSkill,
)
from lineage import decision_lineage
from lineage.decision_lineage import create_decision, flush, get_decision_trace


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


def _real_executor(adapter):
    return SkillExecutor(
        mcp_client=adapter,
        skill_classes={
            "trade_decision_autopsy": TradeDecisionAutopsySkill,
            "risk_review": RiskReviewSkill,
            "lstm_diagnostics": LSTMDiagnosticsSkill,
            "system_health_review": SystemHealthReviewSkill,
        },
    )


def _drop_commander_event_write(monkeypatch):
    """Simulate the silent-failure outcome at the write boundary: the COMMANDER event is not persisted."""
    original_write = decision_lineage._write
    dropped = []

    def write_without_commander_event(kind, data):
        if kind == "event" and data.get("event_type") == "COMMANDER_DECISION_CREATED":
            dropped.append(data["decision_id"])
            return
        original_write(kind, data)

    monkeypatch.setattr(decision_lineage, "_write", write_without_commander_event)
    return dropped


def _sample_decision(subject):
    return CommanderDecision(
        subject=subject,
        summary="lineage observability",
        rationale="lineage observability",
        decision=DecisionType.INSUFFICIENT_DATA,
        confidence=ConfidenceLevel.NOT_APPLICABLE,
        evidence=[],
        skills_used=[],
        mcp_tools_used=[],
        rag_context_used=[],
        risk_flags=[],
        data_gaps=[],
        limitations=["Shadow-only analysis"],
    )


@pytest.mark.anyio
async def test_a_real_commander_lineage_success_is_confirmed_in_postgresql():
    adapter = MCPReadOnlyClientAdapter()
    orchestrator = CommanderOrchestrator(_real_executor(adapter))
    decision_id = create_decision(strategy="commander_2.0", mode="shadow", context={"acceptance": "A"})
    try:
        decision = await orchestrator.run_shadow_decision(subject="phase4_lineage_A")
    finally:
        await orchestrator.close()

    assert flush(timeout=15)
    trace = get_decision_trace(decision_id)
    assert trace is not None, "decision_trace row must exist"
    events = [e for e in trace["events"] if e["event_type"] == "COMMANDER_DECISION_CREATED"]
    assert len(events) == 1
    payload = get_commander_decision_from_lineage(decision_id)
    assert payload["commander_decision_id"] == decision.commander_decision_id
    assert payload["commander_version"] == decision.commander_version
    assert adapter.is_running is False


@pytest.mark.anyio
async def test_b_forced_commander_event_write_failure_raises_persistence_error(monkeypatch):
    decision_id = create_decision(strategy="commander_2.0", mode="shadow", context={"acceptance": "B1"})
    assert flush(timeout=15), "create must be persisted before the injected failure"
    dropped = _drop_commander_event_write(monkeypatch)

    decision = _sample_decision("phase4_lineage_B1")
    with pytest.raises(CommanderLineagePersistenceError, match="LINEAGE_CONFIRMATION_FAILED"):
        record_commander_decision(decision)
    assert dropped == [decision_id], "the injected failure must be the one that was exercised"


@pytest.mark.anyio
async def test_b_forced_confirmation_read_failure_raises_persistence_error(monkeypatch):
    create_decision(strategy="commander_2.0", mode="shadow", context={"acceptance": "B2"})
    assert flush(timeout=15)

    def failing_read(decision_id):
        raise RuntimeError("injected confirmation read failure")

    monkeypatch.setattr(commander_lineage, "lineage_get_decision_trace", failing_read)
    with pytest.raises(CommanderLineagePersistenceError, match="LINEAGE_CONFIRMATION_READ_FAILED"):
        record_commander_decision(_sample_decision("phase4_lineage_B2"))


@pytest.mark.anyio
async def test_b_missing_lineage_context_is_persistence_error():
    decision_lineage.clear_current_decision()
    with pytest.raises(CommanderLineagePersistenceError, match="LINEAGE_CONTEXT_NOT_AVAILABLE"):
        record_commander_decision(_sample_decision("phase4_lineage_B3"))
    assert issubclass(CommanderLineagePersistenceError, CommanderLineageError)


@pytest.mark.anyio
async def test_c_commander_propagates_lineage_failure_fail_closed(monkeypatch):
    processes = []
    real_create_process = stdio_mod._create_platform_compatible_process

    async def capture_process(*args, **kwargs):
        proc = await real_create_process(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(stdio_mod, "_create_platform_compatible_process", capture_process)

    adapter = MCPReadOnlyClientAdapter()
    orchestrator = CommanderOrchestrator(_real_executor(adapter))
    create_decision(strategy="commander_2.0", mode="shadow", context={"acceptance": "C"})
    assert flush(timeout=15)
    dropped = _drop_commander_event_write(monkeypatch)

    returned = None
    try:
        returned = await orchestrator.run_shadow_decision(subject="phase4_lineage_C")
    except CommanderLineagePersistenceError as exc:
        error = exc
    else:
        error = None
    finally:
        await orchestrator.close()

    assert returned is None, "a decision must not be returned when lineage persistence is not confirmed"
    assert error is not None, "the lineage failure must surface to the caller"
    assert "LINEAGE_CONFIRMATION_FAILED" in str(error)
    assert len(dropped) == 1
    assert adapter.is_running is False
    assert processes and all(p.returncode is not None for p in processes), "STDIO process leaked"
