"""Commander 2.0 Orchestrator — Bounded shadow decision engine with fail-closed semantics."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Set, Tuple
from uuid import uuid4

from genesis_commander.contract import CommanderDecision, DecisionType, ConfidenceLevel, ExecutionAuthority
from genesis_commander.guardrails import apply_deterministic_guardrails
from genesis_commander.lineage import (
    record_commander_decision,
    CommanderLineageError,
    CommanderLineagePersistenceError,
)
from genesis_rag.rag_v1 import retrieve as retrieve_rag, RAGTimeoutError, RAGUnavailableError
from genesis_skills.registry import get_registry
from genesis_skills.errors import SkillNotFoundError, SkillDisabledError, SkillException, SkillRecursionNotAllowedError
from genesis_skills.audit import get_audit_logger


@dataclass
class _RunState:
    """Per-run state (isolated from concurrent requests)."""
    skill_call_count: int = 0
    rag_call_count: int = 0
    executed_skills: Set[str] = field(default_factory=set)
    skill_run_ids: Dict[str, str] = field(default_factory=dict)  # skill_id -> run_id correlation

# Configuration
MAX_SKILL_CALLS = 4
MAX_RAG_CALLS = 3
ORCHESTRATOR_TIMEOUT_SECONDS = 30.0
APPROVED_SKILLS = {
    "trade_decision_autopsy",
    "risk_review",
    "lstm_diagnostics",
    "system_health_review",
}
SKILL_NOT_APPLICABLE_TRADE_ID_MARKER = "SKILL_NOT_APPLICABLE_MISSING_INPUT:trade_decision_autopsy:trade_id"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Explicit fail-closed error hierarchy
class CommanderOrchestratorError(Exception):
    """Orchestrator execution error — observable to caller."""
    pass


class CommanderSkillExecutionError(CommanderOrchestratorError):
    """Skill infrastructure failure (not no-data)."""
    pass


class CommanderRAGExecutionError(CommanderOrchestratorError):
    """RAG infrastructure failure (not no-data)."""
    pass


class CommanderSkillCallLimitExceeded(CommanderOrchestratorError):
    """MAX_SKILL_CALLS exceeded."""
    pass


class CommanderRAGCallLimitExceeded(CommanderOrchestratorError):
    """MAX_RAG_CALLS exceeded."""
    pass


class CommanderOrchestrator:
    """Bounded shadow orchestrator for Commander 2.0."""

    def __init__(self, skill_executor: Any, mcp_client: Any = None):
        """Initialize orchestrator."""
        self.skill_executor = skill_executor
        self.mcp_client = mcp_client
        self.registry = get_registry()

    async def close(self):
        """Shut down the executor chain owned by this orchestrator."""
        await self.skill_executor.close()

    async def run_shadow_decision(
        self,
        subject: str,
        symbol: Optional[str] = None,
        as_of: Optional[datetime] = None,
        analytical_context: Optional[Dict[str, Any]] = None,
    ) -> CommanderDecision:
        """Run bounded shadow decision.

        Raises CommanderOrchestratorError subclasses for infrastructure failures.
        """
        try:
            return await asyncio.wait_for(
                self._run_orchestration(subject, symbol, as_of, analytical_context),
                timeout=ORCHESTRATOR_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.error("ORCHESTRATOR_TIMEOUT subject=%s", subject)
            raise CommanderOrchestratorError("Orchestrator timed out") from None

    async def _run_orchestration(
        self,
        subject: str,
        symbol: Optional[str] = None,
        as_of: Optional[datetime] = None,
        analytical_context: Optional[Dict[str, Any]] = None,
    ) -> CommanderDecision:
        """Execute orchestration workflow with fail-closed semantics."""
        # Create per-run state (isolated from concurrent requests)
        run_state = _RunState()

        # Validate request
        if not subject or not subject.strip():
            raise CommanderOrchestratorError("subject required")

        # Initialize aggregation state
        evidence = []
        skills_used_set: Set[str] = set()  # Track actual skills executed
        mcp_tools_used = []
        rag_context_used = []
        risk_flags = []
        data_gaps = []
        limitations = ["Shadow-only analysis", "No actual trading authority"]

        trade_id = (analytical_context or {}).get("trade_id")
        skill_ids = set(APPROVED_SKILLS)
        if not trade_id:
            skill_ids.discard("trade_decision_autopsy")
            data_gaps.append(SKILL_NOT_APPLICABLE_TRADE_ID_MARKER)

        # Phase 1: Execute approved Skills deterministically
        # FAIL-CLOSED: Infrastructure errors raise, don't get swallowed
        skill_evidence, actual_mcp_tools, actual_skills = await self._execute_approved_skills(
            subject, symbol, analytical_context, as_of, run_state, skill_ids
        )
        evidence.extend(skill_evidence)
        mcp_tools_used.extend(actual_mcp_tools)
        skills_used_set.update(actual_skills)

        # Phase 2: Retrieve bounded RAG context
        # FAIL-CLOSED: Timeout/unavailable raise, no-data preserved as marker
        rag_evidence, rag_prov = await self._retrieve_rag_context(subject, symbol, as_of, run_state)
        evidence.extend(rag_evidence)
        rag_context_used.extend(rag_prov)

        # Phase 3: Derive bounded decision deterministically
        # Decision derivation must not let untrusted RAG text directly control enum
        decision_result = self._derive_shadow_decision(
            subject, symbol, evidence, data_gaps
        )
        decision_result = apply_deterministic_guardrails(
            decision_result,
            evidence=evidence,
            risk_flags=risk_flags,
            data_gaps=data_gaps,
        )
        decision = decision_result["decision"]
        confidence = decision_result["confidence"]
        summary = decision_result["summary"]
        rationale = decision_result["rationale"]
        guarded_risk_flags = list(decision_result.get("risk_flags", risk_flags))
        guarded_data_gaps = list(dict.fromkeys(
            [*data_gaps, *decision_result.get("data_gaps", [])]
        ))

        # Phase 4: Create and validate CommanderDecision
        commander_decision = CommanderDecision(
            subject=subject,
            symbol=symbol,
            strategy_id=(analytical_context or {}).get("strategy_id"),
            strategy_name=(analytical_context or {}).get("strategy_name"),
            reference_price=(analytical_context or {}).get("reference_price"),
            market_regime=(analytical_context or {}).get("market_regime"),
            record_provenance=(analytical_context or {}).get("record_provenance", "UNKNOWN"),
            summary=summary,
            rationale=rationale,
            decision=decision,
            confidence=confidence,
            evidence=evidence,
            skills_used=sorted(list(skills_used_set)),  # Only actual skills executed
            mcp_tools_used=mcp_tools_used,
            rag_context_used=rag_context_used,
            risk_flags=guarded_risk_flags,
            data_gaps=guarded_data_gaps,
            limitations=limitations,
        )

        # Validate contract
        if not commander_decision.validate():
            raise CommanderOrchestratorError("CommanderDecision contract validation failed")

        # Verify shadow-only enforcement
        assert commander_decision.shadow_only is True
        assert commander_decision.execution_authority == ExecutionAuthority.NONE

        # Phase 5: Persist lineage — OBSERVABLE FAILURE (not silent)
        try:
            record_commander_decision(commander_decision)
            logger.info("COMMANDER_DECISION_PERSISTED")
        except CommanderLineageError as e:
            logger.error("LINEAGE_PERSISTENCE_FAILURE error=%s", str(e))
            raise CommanderLineagePersistenceError(f"Failed to persist decision: {str(e)}") from e

        return commander_decision

    async def _execute_approved_skills(
        self,
        subject: str,
        symbol: Optional[str] = None,
        analytical_context: Optional[Dict[str, Any]] = None,
        as_of: Optional[datetime] = None,
        run_state: Optional[_RunState] = None,
        skill_ids: Optional[Set[str]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str], Set[str]]:
        """Execute approved Skills deterministically.

        Returns:
            (evidence, mcp_tools, actual_skills_executed)

        Raises:
            CommanderSkillExecutionError for infrastructure failures (fail-closed)
        """
        if run_state is None:
            run_state = _RunState()

        evidence = []
        mcp_tools = []
        skills_executed: Set[str] = set()

        if skill_ids is None:
            skill_ids = APPROVED_SKILLS

        for skill_id in sorted(skill_ids):
            # Enforce MAX_SKILL_CALLS (fail-closed, not silent) — use per-run state
            if run_state.skill_call_count >= MAX_SKILL_CALLS:
                raise CommanderSkillCallLimitExceeded(f"MAX_SKILL_CALLS ({MAX_SKILL_CALLS}) exceeded")

            try:
                # Get manifest to verify enabled
                manifest = self.registry.get(skill_id)

                # Execute skill
                skill_inputs = {
                    "subject": subject,
                    "symbol": symbol,
                    "analytical_context": analytical_context or {},
                    "as_of": as_of.isoformat() if as_of else None,
                }
                if skill_id == "trade_decision_autopsy":
                    skill_inputs["trade_id"] = analytical_context["trade_id"]

                result = await self.skill_executor.execute(skill_id, skill_inputs)

                # EXACT AUDIT CORRELATION: Use skill_run_id from SkillResult (passed by executor)
                # This is the CANONICAL way to correlate execution to audit record
                audit_logger = get_audit_logger()
                current_audit = None

                if result and result.audit_run_id:
                    # Exact lookup: SkillExecutor returned the exact run ID
                    current_audit = audit_logger.get_record(result.audit_run_id)
                    if current_audit:
                        run_state.skill_run_ids[skill_id] = result.audit_run_id

                run_state.skill_call_count += 1
                skills_executed.add(skill_id)  # Track actual execution

                # Aggregate complete SkillResult (not just evidence)
                if result.evidence:
                    evidence.extend(result.evidence)

                # Extract MCP tools from audit logger using EXACT run correlation
                # Only includes tools called in THIS specific execution, no concurrency races
                if current_audit and current_audit.tools_called:
                    mcp_tools.extend(current_audit.tools_called)

            except SkillNotFoundError as e:
                logger.error("SKILL_NOT_FOUND skill_id=%s", skill_id)
                # Defect A fix: Fail-closed for unknown skill
                raise CommanderSkillExecutionError(f"Skill not found: {skill_id}") from e
            except SkillDisabledError as e:
                logger.error("SKILL_DISABLED skill_id=%s", skill_id)
                # Defect A fix: Fail-closed for disabled skill
                raise CommanderSkillExecutionError(f"Skill disabled: {skill_id}") from e
            except SkillRecursionNotAllowedError as e:
                logger.error("SKILL_RECURSION_ATTEMPTED skill_id=%s", skill_id)
                raise CommanderSkillExecutionError(f"Skill recursion detected: {skill_id}") from e
            except asyncio.TimeoutError as e:
                logger.error("SKILL_TIMEOUT skill_id=%s", skill_id)
                raise CommanderSkillExecutionError(f"Skill {skill_id} timed out") from e
            except SkillException as e:
                logger.error("SKILL_ERROR skill_id=%s error=%s", skill_id, str(e))
                raise CommanderSkillExecutionError(f"Skill {skill_id} execution failed: {str(e)}") from e
            except Exception as e:
                logger.error("SKILL_UNEXPECTED skill_id=%s error=%s", skill_id, type(e).__name__)
                raise CommanderSkillExecutionError(f"Unexpected skill error: {type(e).__name__}") from e

        return evidence, mcp_tools, skills_executed

    async def _retrieve_rag_context(
        self,
        subject: str,
        symbol: Optional[str] = None,
        as_of: Optional[datetime] = None,
        run_state: Optional[_RunState] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Retrieve bounded RAG context.

        Returns:
            (evidence, provenance)

        Raises:
            CommanderRAGExecutionError for infrastructure failures (fail-closed)

        Note:
            DATA_NOT_AVAILABLE marker is preserved exactly in evidence.
        """
        if run_state is None:
            run_state = _RunState()

        # Enforce MAX_RAG_CALLS (fail-closed, not silent) — use per-run state
        if run_state.rag_call_count >= MAX_RAG_CALLS:
            raise CommanderRAGCallLimitExceeded(f"MAX_RAG_CALLS ({MAX_RAG_CALLS}) exceeded")

        run_state.rag_call_count += 1

        try:
            results = retrieve_rag(
                symbol=symbol,
                as_of=as_of,
                max_results=10,
            )

            evidence = []
            provenance = []
            has_data_not_available = False

            for record in results:
                # Defect F fix: Preserve DATA_NOT_AVAILABLE marker exactly
                if record.get("marker") == "DATA_NOT_AVAILABLE":
                    # Mark that RAG returned no data (not same as no context at all)
                    has_data_not_available = True
                else:
                    # Preserve complete provenance
                    prov_entry = {
                        "rag_record_id": record.get("rag_record_id"),
                        "source_type": record.get("source_type"),
                        "source_id": record.get("source_id"),
                        "timestamp_utc": record.get("timestamp_utc"),
                        "content_hash": record.get("content_hash"),
                        "content_role": record.get("content_role", "UNTRUSTED_RETRIEVED_DATA"),
                    }
                    provenance.append(prov_entry)

                    # Add to evidence (untrusted marking preserved)
                    evidence.append({
                        "source": "rag",
                        "source_id": record.get("source_id"),
                        "content": record.get("content"),
                        "confidence": "MEDIUM",  # RAG content is bounded confidence
                        "content_role": record.get("content_role", "UNTRUSTED_RETRIEVED_DATA"),
                    })

            # Preserve DATA_NOT_AVAILABLE as evidence marker if found
            if has_data_not_available:
                evidence.append({
                    "marker": "DATA_NOT_AVAILABLE",
                    "source": "rag",
                    "content": "No matching RAG context available",
                })

            return evidence, provenance

        except RAGTimeoutError as e:
            logger.error("RAG_TIMEOUT")
            raise CommanderRAGExecutionError("RAG retrieve timed out") from e

        except RAGUnavailableError as e:
            logger.error("RAG_UNAVAILABLE")
            raise CommanderRAGExecutionError("RAG infrastructure unavailable") from e

        except Exception as e:
            logger.error("RAG_RETRIEVAL_FAILURE error=%s", type(e).__name__)
            raise CommanderRAGExecutionError(f"RAG retrieval failed: {type(e).__name__}") from e

    def _derive_shadow_decision(
        self,
        subject: str,
        symbol: Optional[str] = None,
        evidence: List[Dict[str, Any]] = None,
        data_gaps: List[str] = None,
    ) -> Dict[str, Any]:
        """Derive bounded shadow decision deterministically.

        CRITICAL: Untrusted RAG text CANNOT directly force decision enum.
        Decision derivation consumes only structured analytical signals.
        """
        evidence = evidence or []
        data_gaps = data_gaps or []

        # Fail-closed: insufficient evidence → INSUFFICIENT_DATA
        if len(evidence) == 0:
            return {
                "decision": DecisionType.INSUFFICIENT_DATA,
                "confidence": ConfidenceLevel.NOT_APPLICABLE,
                "summary": "Insufficient evidence",
                "rationale": "No evidence available for decision",
            }

        # Check for DATA_NOT_AVAILABLE marker (no data)
        has_data_not_available = any(
            e.get("marker") == "DATA_NOT_AVAILABLE" for e in evidence
        )
        if has_data_not_available and len(evidence) == 1:
            return {
                "decision": DecisionType.INSUFFICIENT_DATA,
                "confidence": ConfidenceLevel.NOT_APPLICABLE,
                "summary": "No context available",
                "rationale": "RAG returned no matching context",
            }

        # HARDENING: Extract only structured signals, not raw untrusted text
        # Untrusted RAG content tagged as UNTRUSTED_RETRIEVED_DATA cannot control enum
        trusted_evidence = [
            e for e in evidence
            if e.get("content_role") != "UNTRUSTED_RETRIEVED_DATA" or e.get("source") == "skill"
        ]

        # If only untrusted evidence, cannot make actionable decision
        if len(trusted_evidence) == 0:
            return {
                "decision": DecisionType.WATCH,  # Conservative: only WATCH allowed on untrusted data alone
                "confidence": ConfidenceLevel.LOW,
                "summary": "Insufficient trusted signal",
                "rationale": "Available context is read-only retrieved data; recommend human review",
            }

        # With trusted evidence, derive bounded decision
        # Heuristic: look for explicit fields, not raw text matching
        for ev in trusted_evidence:
            source = ev.get("source", "")
            confidence_level = ev.get("confidence", "").upper()

            # Structured skill signals override untrusted text
            if source == "skill":
                if "long" in str(ev.get("signal", "")).lower() and confidence_level == "HIGH":
                    return {
                        "decision": DecisionType.CANDIDATE_LONG,
                        "confidence": ConfidenceLevel.MEDIUM,
                        "summary": "Long candidate identified by Skills",
                        "rationale": "Structured analytical signals suggest potential long opportunity",
                    }
                elif "short" in str(ev.get("signal", "")).lower() and confidence_level == "HIGH":
                    return {
                        "decision": DecisionType.CANDIDATE_SHORT,
                        "confidence": ConfidenceLevel.MEDIUM,
                        "summary": "Short candidate identified by Skills",
                        "rationale": "Structured analytical signals suggest potential short opportunity",
                    }
                elif "risk" in str(ev.get("signal", "")).lower():
                    return {
                        "decision": DecisionType.WATCH,
                        "confidence": ConfidenceLevel.MEDIUM,
                        "summary": "Risk indicators detected",
                        "rationale": "Skills flagged risk factors requiring monitoring",
                    }

        # Default: no clear signal
        return {
            "decision": DecisionType.NO_ACTION,
            "confidence": ConfidenceLevel.LOW,
            "summary": "No clear actionable signal",
            "rationale": "Evidence does not support trading decision",
        }


# Global orchestrator instance
_global_orchestrator: Optional[CommanderOrchestrator] = None


def get_orchestrator() -> CommanderOrchestrator:
    """Get global Commander orchestrator with real MCP adapter."""
    global _global_orchestrator
    if _global_orchestrator is None:
        from genesis_skills.executor import SkillExecutor
        from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
        from genesis_skills.bootstrap import bootstrap_default_registry
        from genesis_skills.skills import (
            TradeDecisionAutopsySkill,
            RiskReviewSkill,
            LSTMDiagnosticsSkill,
            SystemHealthReviewSkill,
        )

        # Bootstrap registry with approved skills
        bootstrap_default_registry()

        skill_classes = {
            "trade_decision_autopsy": TradeDecisionAutopsySkill,
            "risk_review": RiskReviewSkill,
            "lstm_diagnostics": LSTMDiagnosticsSkill,
            "system_health_review": SystemHealthReviewSkill,
        }

        # Use canonical real MCP adapter (not mcp_client=None)
        mcp_client = MCPReadOnlyClientAdapter()
        executor = SkillExecutor(mcp_client=mcp_client, skill_classes=skill_classes)
        _global_orchestrator = CommanderOrchestrator(executor)

    return _global_orchestrator


def set_orchestrator(orchestrator: CommanderOrchestrator) -> None:
    """Set global Commander orchestrator (for testing)."""
    global _global_orchestrator
    _global_orchestrator = orchestrator


async def close_orchestrator() -> None:
    """Close global orchestrator and cleanup MCP adapter."""
    global _global_orchestrator
    if _global_orchestrator:
        try:
            await _global_orchestrator.close()
        finally:
            _global_orchestrator = None
