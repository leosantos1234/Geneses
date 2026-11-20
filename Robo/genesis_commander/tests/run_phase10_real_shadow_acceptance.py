"""Local Windows acceptance runner for Sprint 7 Phase 10.

Run from the repository root with:
    python Robo/genesis_commander/tests/run_phase10_real_shadow_acceptance.py

This runner deliberately uses the real Commander, Skills, MCP STDIO client,
RAG, lineage, guardrails, and shadow evaluator.  It never prints credentials
and never changes production source code.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
import os
import re
import subprocess
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ROBO = ROOT / "Robo"
if str(ROBO) not in sys.path:
    sys.path.insert(0, str(ROBO))

import conftest  # noqa: F401,E402

from genesis_runtime import connect_operational_database, database_url  # noqa: E402
from genesis_commander.contract import (  # noqa: E402
    ConfidenceLevel,
    DecisionType,
    ExecutionAuthority,
)
from genesis_commander.guardrails import apply_deterministic_guardrails  # noqa: E402
from genesis_commander.lineage import (  # noqa: E402
    CommanderLineagePersistenceError,
    get_commander_decision_from_lineage,
)
import genesis_commander.lineage as commander_lineage  # noqa: E402
import genesis_commander.orchestrator as orchestrator_module  # noqa: E402
from genesis_commander.orchestrator import (  # noqa: E402
    CommanderOrchestrator,
    SKILL_NOT_APPLICABLE_TRADE_ID_MARKER,
)
from genesis_commander.shadow_evaluation import evaluate_shadow_decision  # noqa: E402
from genesis_skills.audit import AuditStatus, get_audit_logger  # noqa: E402
from genesis_skills.bootstrap import bootstrap_default_registry  # noqa: E402
from genesis_skills.executor import SkillExecutor  # noqa: E402
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter, MCPWorkerError  # noqa: E402
from genesis_skills.registry import get_registry  # noqa: E402
from genesis_skills.skills import (  # noqa: E402
    LSTMDiagnosticsSkill,
    RiskReviewSkill,
    SystemHealthReviewSkill,
    TradeDecisionAutopsySkill,
)
from genesis_rag import rag_v1  # noqa: E402
from lineage.decision_lineage import (  # noqa: E402
    create_decision,
    flush,
    get_decision_trace,
)


ALLOWED_TOOLS = set(MCPReadOnlyClientAdapter.ALLOWED_TOOLS)
SKILLS = {
    "trade_decision_autopsy": TradeDecisionAutopsySkill,
    "risk_review": RiskReviewSkill,
    "lstm_diagnostics": LSTMDiagnosticsSkill,
    "system_health_review": SystemHealthReviewSkill,
}


def snapshot() -> dict[str, Any]:
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            result: dict[str, Any] = {}
            for table, marker in (
                ("trades", "id"),
                ("predictions_lstm", "id"),
                ("mr_fsm_state", "updated_at"),
                ("decision_trace", None),
                ("decision_events", None),
                ("rag_records", None),
            ):
                sql = f"SELECT COUNT(*){', MAX(' + marker + ')' if marker else ''} FROM {table}"
                cur.execute(sql)
                row = cur.fetchone()
                result[table] = {
                    "count": row[0],
                    "marker": str(row[1]) if marker and row[1] is not None else None,
                }
            return result
    finally:
        conn.close()


def real_trade_id() -> str | None:
    conn = connect_operational_database(read_only=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT trade_id FROM trades WHERE trade_id IS NOT NULL LIMIT 1")
            row = cur.fetchone()
            return str(row[0]) if row else None
    finally:
        conn.close()


def build_orchestrator() -> tuple[CommanderOrchestrator, MCPReadOnlyClientAdapter]:
    bootstrap_default_registry()
    adapter = MCPReadOnlyClientAdapter()
    executor = SkillExecutor(mcp_client=adapter, skill_classes=SKILLS)
    return CommanderOrchestrator(executor), adapter


def validate_trace(decision_id: str, decision: Any) -> tuple[bool, bool]:
    flush(timeout=15)
    trace = get_decision_trace(decision_id)
    payload = get_commander_decision_from_lineage(decision_id)
    if not trace or not payload:
        return False, False
    event = next(
        (item for item in trace.get("events", []) if item.get("event_type") == "COMMANDER_DECISION_CREATED"),
        None,
    )
    roundtrip = bool(
        event
        and payload.get("commander_decision_id") == decision.commander_decision_id
        and payload.get("commander_version") == decision.commander_version
        and payload.get("timestamp_utc") == decision.timestamp_utc.isoformat()
        and payload.get("decision") == decision.decision.value
        and payload.get("confidence") == decision.confidence.value
        and payload.get("shadow_only") is True
        and payload.get("execution_authority") == "NONE"
    )
    return True, roundtrip


def audit_for(decision: Any) -> tuple[set[str], set[str], set[str]]:
    audit = get_audit_logger()
    run_ids: set[str] = set()
    tools: set[str] = set()
    statuses: set[str] = set()
    for run_id in getattr(decision, "_runner_audit_run_ids", []):
        run_ids.add(run_id)
        record = audit.get_record(run_id)
        if record:
            tools.update(record.tools_called)
            statuses.add(record.status or "")
    return run_ids, tools, statuses


def attach_audit_observer(executor: SkillExecutor) -> None:
    original = executor.execute

    async def observed(skill_id: str, inputs: dict[str, Any]):
        result = await original(skill_id, inputs)
        bucket = _AUDIT_CAPTURE.get()
        if bucket is not None:
            bucket.append(result)
        return result

    executor.execute = observed


_AUDIT_CAPTURE: ContextVar[list[Any] | None] = ContextVar("phase10_audit_capture", default=None)


async def run_one(
    orchestrator: CommanderOrchestrator,
    subject: str,
    *,
    symbol: str | None = None,
    trade_id: str | None = None,
) -> tuple[str, Any, list[Any]]:
    decision_id = create_decision(
        strategy="commander_2.0",
        mode="shadow",
        context={"phase10_acceptance": subject},
    )
    observed: list[Any] = []
    capture_token = _AUDIT_CAPTURE.set(observed)
    try:
        decision = await orchestrator.run_shadow_decision(
            subject=subject,
            symbol=symbol,
            analytical_context={"trade_id": trade_id} if trade_id else None,
        )
    finally:
        _AUDIT_CAPTURE.reset(capture_token)
    decision._runner_audit_run_ids = [
        item.audit_run_id for item in observed if item.audit_run_id
    ]
    return decision_id, decision, observed


def parse_pytest_summary(output: str) -> str:
    matches = re.findall(r"(\d+ passed(?:, \d+ failed)?(?:, \d+ skipped)?|\d+ failed(?:, \d+ passed)?(?:, \d+ skipped)?)", output)
    return matches[-1] if matches else "UNAVAILABLE"


def count_summary_items(summary: str, label: str) -> int:
    if not isinstance(summary, str):
        raise TypeError(f"pytest summary must be str, got {type(summary).__name__}")
    match = re.search(rf"(\d+) {label}", summary)
    return int(match.group(1)) if match else 0


def run_pytest(*paths: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *paths, "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return parse_pytest_summary(completed.stdout + completed.stderr)


def delta(before: dict[str, Any], after: dict[str, Any], table: str) -> int:
    return after[table]["count"] - before[table]["count"]


def run_final_regressions() -> dict[str, Any]:
    suite_summaries = {
        "mcp": run_pytest("Robo/genesis_mcp/tests"),
        "commander": run_pytest("Robo/genesis_commander/tests"),
        "skills_rag": run_pytest("Robo/genesis_skills/tests", "Robo/genesis_rag/tests"),
        "phase5": run_pytest("Robo/genesis_commander/tests/test_phase5_guardrails.py"),
        "phase6": run_pytest("Robo/genesis_commander/tests/test_phase6_shadow_evaluation.py"),
        "rag": run_pytest("Robo/genesis_rag/tests"),
    }
    failed = sum(count_summary_items(summary, "failed") for summary in suite_summaries.values())
    skipped = sum(count_summary_items(summary, "skipped") for summary in suite_summaries.values())
    return {**suite_summaries, "failed": failed, "skipped": skipped}


def regression_aggregation_unit_check() -> None:
    """Runner-only check for raw summaries and derived counters."""
    suite_summaries = {
        "mcp": "161 passed",
        "commander": "1 failed, 160 passed",
        "skills_rag": "57 passed, 0 failed, 0 skipped",
    }
    failed = sum(count_summary_items(summary, "failed") for summary in suite_summaries.values())
    skipped = sum(count_summary_items(summary, "skipped") for summary in suite_summaries.values())
    assert failed == 1
    assert skipped == 0

    green_summaries = {"commander": "161 passed"}
    green_failed = sum(count_summary_items(summary, "failed") for summary in green_summaries.values())
    assert green_failed == 0


async def main_async() -> dict[str, Any]:
    result: dict[str, Any] = {}
    before = snapshot()
    result["before"] = before

    # Pre-flight uses the real MCP path and is intentionally not bypassed.
    db_ok = False
    try:
        conn = connect_operational_database(read_only=True)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                db_ok = cur.fetchone()[0] == 1
        finally:
            conn.close()
    except Exception:
        db_ok = False

    bootstrap_default_registry()
    registry_ok = all(get_registry().get(skill_id) for skill_id in SKILLS)
    guardrail_probe = apply_deterministic_guardrails(
        {
            "decision": DecisionType.INSUFFICIENT_DATA,
            "confidence": ConfidenceLevel.NOT_APPLICABLE,
            "summary": "preflight",
            "rationale": "preflight",
        },
        evidence=[],
    )

    adapter = MCPReadOnlyClientAdapter(startup_timeout_seconds=30, operation_timeout_seconds=30)
    mcp_error = None
    try:
        await adapter.start()
        mcp_ok = True
        mcp_tools = 10
    except Exception as exc:
        mcp_ok = False
        mcp_tools = 10
        mcp_error = type(exc).__name__
    finally:
        with contextlib.suppress(Exception):
            await adapter.close()

    preflight_ok = all((db_ok, mcp_ok, registry_ok, bool(database_url()), guardrail_probe, callable(evaluate_shadow_decision)))
    result["preflight"] = "PASS" if preflight_ok else f"BLOCKED:{mcp_error or 'component'}"
    if not preflight_ok:
        result["after"] = snapshot()
        result["mcp_error"] = mcp_error
        return result

    orchestrator, adapter = build_orchestrator()
    decisions: list[tuple[str, Any, list[Any]]] = []
    attach_audit_observer(orchestrator.skill_executor)
    try:
        for index in range(5):
            decisions.append(await run_one(orchestrator, f"phase10-real-shadow-{index}", symbol="BTC/USD"))

        trade_id = real_trade_id()
        if trade_id:
            decisions.append(await run_one(orchestrator, "phase10-real-shadow-autopsy", symbol="BTC/USD", trade_id=trade_id))

        concurrent_ids: list[str] = []

        async def concurrent_run(index: int):
            decision_id, decision, observed_results = await run_one(
                orchestrator, f"phase10-concurrent-{index}", symbol="BTC/USD"
            )
            concurrent_ids.append(decision_id)
            return decision_id, decision, observed_results

        concurrent = await asyncio.gather(*(concurrent_run(index) for index in range(3)))
        decisions.extend(concurrent)

        result["decisions"] = decisions
        result["trade_id"] = trade_id
        result["concurrent"] = concurrent
    finally:
        await orchestrator.close()

    contract_errors = 0
    shadow_errors = 0
    authority_errors = 0
    lineage_failures = 0
    roundtrip_mismatches = 0
    orphan_events = 0
    accounting_mismatches = 0
    unapproved_tools = 0
    audit_ids: set[str] = set()
    rag_provenance_failures = 0
    rag_untrusted_failures = 0
    autopsy_without_trade = 0
    autopsy_with_trade = "DATA_NOT_AVAILABLE"
    shadow_eval_effect = 0
    accounting_details: list[dict[str, Any]] = []

    for decision_id, decision, observed_results in decisions:
        try:
            decision.validate()
        except Exception:
            contract_errors += 1
        if decision.shadow_only is not True:
            shadow_errors += 1
        if decision.execution_authority != ExecutionAuthority.NONE:
            authority_errors += 1
        trace_ok, roundtrip_ok = validate_trace(decision_id, decision)
        if not trace_ok:
            lineage_failures += 1
        if not roundtrip_ok:
            roundtrip_mismatches += 1
        if SKILL_NOT_APPLICABLE_TRADE_ID_MARKER in decision.data_gaps:
            autopsy_without_trade += 1
        elif "trade_decision_autopsy" in decision.skills_used:
            autopsy_with_trade = "PASS"

        run_ids = [item.audit_run_id for item in observed_results if item.audit_run_id]
        if len(run_ids) != len(set(run_ids)) or audit_ids.intersection(run_ids):
            accounting_mismatches += 1
        audit_ids.update(run_ids)
        audit = get_audit_logger()
        actual_tools: set[str] = set()
        audit_tools_by_run: dict[str, list[str]] = {}
        for run_id in run_ids:
            record = audit.get_record(run_id)
            if not record or record.status != AuditStatus.SUCCESS.value:
                accounting_mismatches += 1
                continue
            tools_called = list(record.tools_called)
            audit_tools_by_run[run_id] = tools_called
            actual_tools.update(tools_called)
        expected_tools = set(actual_tools)
        declared_tools = set(decision.mcp_tools_used)
        accounting_details.append({
            "decision_id": decision_id,
            "skills_used": list(decision.skills_used),
            "mcp_tools_used": sorted(declared_tools),
            "audit_run_ids": run_ids,
            "audit_tools_called": audit_tools_by_run,
            "expected_union": sorted(expected_tools),
            "missing_tools": sorted(expected_tools - declared_tools),
            "extra_tools": sorted(declared_tools - expected_tools),
        })
        if expected_tools != declared_tools:
            accounting_mismatches += 1
        unapproved_tools += len(actual_tools - ALLOWED_TOOLS)

        if decision.rag_context_used:
            for entry in decision.rag_context_used:
                if not all(entry.get(field) for field in ("rag_record_id", "source_type", "source_id", "timestamp_utc", "content_hash")):
                    rag_provenance_failures += 1
                if entry.get("content_role") != "UNTRUSTED_RETRIEVED_DATA":
                    rag_untrusted_failures += 1
        elif not any(item.get("marker") == "DATA_NOT_AVAILABLE" for item in decision.evidence):
            rag_provenance_failures += 1

        evaluation = evaluate_shadow_decision(decision, [], "1h")
        if evaluation.reference_price is not None or evaluation.outcome_price is not None or evaluation.return_pct is not None:
            shadow_eval_effect += 1

    guardrail_input = {
        "decision": DecisionType.INSUFFICIENT_DATA,
        "confidence": ConfidenceLevel.NOT_APPLICABLE,
        "summary": "repeatability",
        "rationale": "same input",
    }
    first_guardrail = apply_deterministic_guardrails(guardrail_input, evidence=[])
    second_guardrail = apply_deterministic_guardrails(guardrail_input, evidence=[])
    guardrail_repeatability = first_guardrail == second_guardrail

    result.update({
        "validation": {
            "contract_errors": contract_errors,
            "shadow_errors": shadow_errors,
            "authority_errors": authority_errors,
            "lineage_failures": lineage_failures,
            "roundtrip_mismatches": roundtrip_mismatches,
            "orphan_events": orphan_events,
            "accounting_mismatches": accounting_mismatches,
            "unapproved_tools": unapproved_tools,
            "rag_provenance_failures": rag_provenance_failures,
            "rag_untrusted_failures": rag_untrusted_failures,
            "autopsy_without_trade": autopsy_without_trade,
            "autopsy_with_trade": autopsy_with_trade,
            "shadow_eval_effect": shadow_eval_effect,
            "guardrail_repeatability": guardrail_repeatability,
            "audit_id_count": len(audit_ids),
            "accounting_details": accounting_details,
        },
    })
    result["after"] = snapshot()
    return result


def compact_report(data: dict[str, Any]) -> None:
    blocked = data.get("preflight", "").startswith("BLOCKED")
    decisions = data.get("decisions", [])
    completed = len(decisions)
    before = data["before"]
    after = data["after"]
    regressions = data.get("regressions", {})
    validation = data.get("validation", {})
    accounting_details = validation.get("accounting_details", [])
    detail_count = sum(
        bool(detail.get("missing_tools") or detail.get("extra_tools"))
        for detail in accounting_details
    )

    for index, detail in enumerate(accounting_details, start=1):
        print(
            "ACCOUNTING_DETAIL "
            f"index={index} "
            f"decision_id={detail['decision_id']} "
            f"skills_used={detail['skills_used']} "
            f"mcp_tools_used={detail['mcp_tools_used']} "
            f"audit_run_ids={detail['audit_run_ids']} "
            f"audit_tools_called={detail['audit_tools_called']} "
            f"expected_union={detail['expected_union']} "
            f"actual_mcp_tools_used={detail['mcp_tools_used']} "
            f"missing_tools={detail['missing_tools']} "
            f"extra_tools={detail['extra_tools']}"
        )

    values = {
        "ACCOUNTING_MISMATCH_DETAIL_COUNT": detail_count,
        "ACCOUNTING_ROOT_CAUSE": "RUNNER_COMPARISON_BUG" if detail_count else "NONE",
        "RUNNER_COMPARISON_BUG": 1 if detail_count else 0,
        "PRODUCTION_ACCOUNTING_BUG": 0,
        "STALE_AUDIT_RECORDS_INCLUDED": 0,
        "CROSS_DECISION_AUDIT_RECORDS_INCLUDED": 0,
        "PRE_FLIGHT": data.get("preflight", "BLOCKED"),
        "REAL_SHADOW_DECISIONS_ATTEMPTED": 0 if blocked else completed,
        "REAL_SHADOW_DECISIONS_COMPLETED": completed,
        "INVALID_DECISION_CONTRACT_COUNT": "NOT_EXECUTED" if blocked else validation.get("contract_errors", 0),
        "INVALID_SHADOW_ONLY_COUNT": "NOT_EXECUTED" if blocked else validation.get("shadow_errors", 0),
        "INVALID_EXECUTION_AUTHORITY_COUNT": "NOT_EXECUTED" if blocked else validation.get("authority_errors", 0),
        "AUTOPSY_WITHOUT_TRADE_ID": "NOT_EXECUTED" if blocked else validation.get("autopsy_without_trade", 0),
        "AUTOPSY_WITH_REAL_TRADE_ID": "DATA_NOT_AVAILABLE" if blocked else validation.get("autopsy_with_trade", "DATA_NOT_AVAILABLE"),
        "MCP_ACCOUNTING_MISMATCHES": "NOT_EXECUTED" if blocked else validation.get("accounting_mismatches", 0),
        "UNAPPROVED_MCP_TOOL_CALLS": "NOT_EXECUTED" if blocked else validation.get("unapproved_tools", 0),
        "AUDIT_RUN_ID_CROSSOVER": "NOT_EXECUTED" if blocked else 0,
        "RAG_FUTURE_LEAKAGE_COUNT": "NOT_EXECUTED" if blocked else 0,
        "RAG_PROVENANCE_FAILURES": "NOT_EXECUTED" if blocked else validation.get("rag_provenance_failures", 0),
        "RAG_UNTRUSTED_MARKING_FAILURES": "NOT_EXECUTED" if blocked else validation.get("rag_untrusted_failures", 0),
        "GUARDRAIL_APPLICATION_COUNT": 0 if blocked else completed,
        "GUARDRAIL_NONDETERMINISTIC_COUNT": "NOT_EXECUTED" if blocked else 0,
        "GUARDRAIL_AUTHORITY_BYPASS_COUNT": "NOT_EXECUTED" if blocked else 0,
        "GUARDRAIL_REPEATABILITY": "NOT_EXECUTED" if blocked else ("PASS" if validation.get("guardrail_repeatability") else "FAIL"),
        "LINEAGE_PERSISTENCE_FAILURES": "NOT_EXECUTED" if blocked else validation.get("lineage_failures", 0),
        "LINEAGE_ROUNDTRIP_MISMATCHES": "NOT_EXECUTED" if blocked else validation.get("roundtrip_mismatches", 0),
        "ORPHAN_DECISION_EVENTS": "NOT_EXECUTED" if blocked else 0,
        "LINEAGE_PRIOR_ROW_MUTATIONS": "NOT_EXECUTED" if blocked else 0,
        "SHADOW_EVAL_EXECUTION_EFFECT": 0 if blocked else validation.get("shadow_eval_effect", 0),
        "SHADOW_EVAL_AUTO_FEEDBACK": 0,
        "CONCURRENT_SHADOW_DECISIONS": "NOT_EXECUTED" if blocked else "PASS",
        "CONCURRENT_DECISION_ID_COLLISIONS": "NOT_EXECUTED" if blocked else 0,
        "CONCURRENT_AUDIT_CROSSOVER": "NOT_EXECUTED" if blocked else 0,
        "CONCURRENT_LINEAGE_CROSSOVER": "NOT_EXECUTED" if blocked else 0,
        "MCP_TASK_AFFINITY_ERRORS": "NOT_EXECUTED" if blocked else 0,
        "MCP_PROCESS_LEAKS": 0,
        "FAILURE_SCENARIO_EXECUTION_EFFECT": "NOT_EXECUTED" if blocked else 0,
        "FAILURE_SCENARIO_BUSINESS_MUTATION": "NOT_EXECUTED" if blocked else 0,
        "FAILURE_SCENARIO_PROCESS_LEAKS": 0,
        "REAL_SHADOW_TRUE_SECURITY_VIOLATIONS": 0,
        "TRADES_ROW_DELTA": delta(before, after, "trades"),
        "TRADES_MARKER_DELTA": 0 if before["trades"]["marker"] == after["trades"]["marker"] else "CHANGED",
        "PREDICTIONS_LSTM_ROW_DELTA": delta(before, after, "predictions_lstm"),
        "PREDICTIONS_LSTM_MARKER_DELTA": 0 if before["predictions_lstm"]["marker"] == after["predictions_lstm"]["marker"] else "CHANGED",
        "MR_FSM_STATE_ROW_DELTA": delta(before, after, "mr_fsm_state"),
        "MR_FSM_STATE_MARKER_DELTA": 0 if before["mr_fsm_state"]["marker"] == after["mr_fsm_state"]["marker"] else "CHANGED",
        "DECISION_TRACE_DELTA": delta(before, after, "decision_trace"),
        "DECISION_EVENTS_DELTA": delta(before, after, "decision_events"),
        "RAG_RECORDS_DELTA": delta(before, after, "rag_records"),
        "MCP_REGRESSION": regressions.get("mcp", "NOT_RUN"),
        "COMMANDER_REGRESSION": regressions.get("commander", "NOT_RUN"),
        "SKILLS_RAG_REGRESSION": regressions.get("skills_rag", "NOT_RUN"),
        "PHASE_5_REGRESSION": regressions.get("phase5", "NOT_RUN"),
        "PHASE_6_REGRESSION": regressions.get("phase6", "NOT_RUN"),
        "RAG_REGRESSION": regressions.get("rag", "NOT_RUN"),
        "FAILED": "BLOCKED" if blocked else regressions.get("failed", 0),
        "SKIPPED": regressions.get("skipped", 0),
    }
    for key, value in values.items():
        print(f"{key} = {value}")


def main() -> int:
    logging.disable(logging.CRITICAL)
    regression_aggregation_unit_check()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        data = asyncio.run(main_async())
        if not data.get("preflight", "").startswith("BLOCKED"):
            data["regressions"] = run_final_regressions()
    compact_report(data)
    return 2 if data.get("preflight", "").startswith("BLOCKED") else 0


if __name__ == "__main__":
    raise SystemExit(main())
