"""Phase 4 real Commander MCP accounting — strict, no swallowed failures.

MCP_ISOLATED_TEST: RAG retrieval and lineage persistence are patched in these tests.
Registry, SkillExecutor, MCP adapter (owner-task worker) and the four Skill classes are real.
Recording wrappers only observe results; the real calls run.
"""

import asyncio
from unittest.mock import patch

import mcp.client.stdio as stdio_mod
import pytest
from mcp import ClientSession

import genesis_skills.mcp_adapter as adapter_mod
from genesis_commander.orchestrator import CommanderOrchestrator, SKILL_NOT_APPLICABLE_TRADE_ID_MARKER
from genesis_runtime import connect_operational_database
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

APPROVED_SKILLS = {
    "trade_decision_autopsy",
    "risk_review",
    "lstm_diagnostics",
    "system_health_review",
}
EXECUTABLE_WITHOUT_TRADE = APPROVED_SKILLS - {"trade_decision_autopsy"}


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


@pytest.fixture(autouse=True)
def clean_audit():
    get_audit_logger().clear()
    yield
    get_audit_logger().clear()


@pytest.fixture
def process_counts(monkeypatch):
    counts = {"stdio": 0, "session": 0}
    processes = []

    real_create_process = stdio_mod._create_platform_compatible_process
    real_stdio_client = adapter_mod.stdio_client
    real_session_cls = adapter_mod.ClientSession

    async def capture_process(*args, **kwargs):
        proc = await real_create_process(*args, **kwargs)
        processes.append(proc)
        return proc

    class CountingSession(real_session_cls):
        def __init__(self, *args, **kwargs):
            counts["session"] += 1
            super().__init__(*args, **kwargs)

    def counting_stdio_client(*args, **kwargs):
        counts["stdio"] += 1
        return real_stdio_client(*args, **kwargs)

    monkeypatch.setattr(stdio_mod, "_create_platform_compatible_process", capture_process)
    monkeypatch.setattr(adapter_mod, "ClientSession", CountingSession)
    monkeypatch.setattr(adapter_mod, "stdio_client", counting_stdio_client)
    return counts, processes


def _build_real_commander():
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
        captured.append((inputs["subject"], result))
        return result

    executor.execute = recording_execute
    return adapter, CommanderOrchestrator(executor), captured


def _expected_union_for(captured, subject):
    audit = get_audit_logger()
    union = set()
    run_ids = []
    for subj, result in captured:
        if subj != subject:
            continue
        assert result.audit_run_id is not None, f"{result.skill_id} returned no audit_run_id"
        record = audit.get_record(result.audit_run_id)
        assert record is not None, f"no audit record for run {result.audit_run_id}"
        assert record.status == AuditStatus.SUCCESS.value, f"{result.skill_id} audit {record.status}"
        assert record.skill_id == result.skill_id
        union |= set(record.tools_called)
        run_ids.append(result.audit_run_id)
    return union, run_ids


def _assert_completed_without_failure(captured):
    for _, result in captured:
        assert result.status in ("SUCCESS", "PARTIAL"), f"{result.skill_id}: {result.summary}"
        if result.status == "PARTIAL":
            assert result.data_gaps, f"{result.skill_id} PARTIAL without explicit data_gaps"
        assert "cancel scope" not in result.summary.lower()
        assert "different task" not in result.summary.lower()


def _real_trade_id():
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trades ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, "canonical trades table has no rows to use as a real trade_id"
    return str(row[0])


@pytest.mark.anyio
async def test_real_commander_without_trade_id_skips_autopsy_with_observable_gap(process_counts):
    adapter, orchestrator, captured = _build_real_commander()
    try:
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            decision = await orchestrator.run_shadow_decision(subject="phase4_no_trade")

        executed = {r.skill_id for _, r in captured}
        assert executed == EXECUTABLE_WITHOUT_TRADE
        _assert_completed_without_failure(captured)

        expected, run_ids = _expected_union_for(captured, "phase4_no_trade")
        assert len(run_ids) == 3
        assert expected, "at least one MCP tool must be attributed to the decision"
        assert expected <= adapter.ALLOWED_TOOLS
        assert set(decision.mcp_tools_used) == expected

        assert "trade_decision_autopsy" not in decision.skills_used
        assert set(decision.skills_used) == EXECUTABLE_WITHOUT_TRADE
        assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision.data_gaps
        assert not [r for r in get_audit_logger().list_records() if r.skill_id == "trade_decision_autopsy"]
    finally:
        await orchestrator.close()
    assert adapter.is_running is False
    counts, processes = process_counts
    assert counts["stdio"] == 1 and counts["session"] == 1
    assert all(p.returncode is not None for p in processes), "STDIO process leaked"


@pytest.mark.anyio
async def test_real_commander_with_trade_id_runs_autopsy():
    trade_id = _real_trade_id()
    adapter, orchestrator, captured = _build_real_commander()
    try:
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            decision = await orchestrator.run_shadow_decision(
                subject=f"phase4_trade_{trade_id}",
                analytical_context={"trade_id": trade_id},
            )

        assert {r.skill_id for _, r in captured} == APPROVED_SKILLS
        _assert_completed_without_failure(captured)
        assert "trade_decision_autopsy" in decision.skills_used
        assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER not in decision.data_gaps

        expected, _ = _expected_union_for(captured, f"phase4_trade_{trade_id}")
        assert set(decision.mcp_tools_used) == expected
    finally:
        await orchestrator.close()
    assert adapter.is_running is False


@pytest.mark.anyio
async def test_concurrent_commander_decisions_real_mcp_single_owner(process_counts):
    adapter, orchestrator, captured = _build_real_commander()
    try:
        with patch('genesis_commander.orchestrator.record_commander_decision'), \
             patch('genesis_commander.orchestrator.retrieve_rag', return_value=[]):
            decision_a, decision_b = await asyncio.gather(
                orchestrator.run_shadow_decision(subject="A"),
                orchestrator.run_shadow_decision(subject="B"),
            )

        assert len(captured) == 6
        assert {r.skill_id for subj, r in captured if subj == "A"} == EXECUTABLE_WITHOUT_TRADE
        assert {r.skill_id for subj, r in captured if subj == "B"} == EXECUTABLE_WITHOUT_TRADE
        _assert_completed_without_failure(captured)

        all_run_ids = [r.audit_run_id for _, r in captured]
        assert len(set(all_run_ids)) == 6, "every execution must have its own audit run"

        expected_a, ids_a = _expected_union_for(captured, "A")
        expected_b, ids_b = _expected_union_for(captured, "B")
        assert len(ids_a) == 3 and len(ids_b) == 3
        assert set(ids_a).isdisjoint(ids_b), "audit run ids must not be shared between decisions"
        assert set(decision_a.mcp_tools_used) == expected_a
        assert set(decision_b.mcp_tools_used) == expected_b
        assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision_a.data_gaps
        assert SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision_b.data_gaps

        assert adapter.worker_starts == 1
    finally:
        await orchestrator.close()
    assert adapter.is_running is False
    counts, processes = process_counts
    assert counts["stdio"] == 1, "one STDIO process must serve both decisions"
    assert counts["session"] == 1, "one ClientSession must serve both decisions"
    assert all(p.returncode is not None for p in processes), "STDIO process leaked"
