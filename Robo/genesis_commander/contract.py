"""Commander 2.0 Decision Contract — Shadow / Read-Only"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4


class DecisionType(Enum):
    """Allowed decision states for Commander 2.0 shadow mode."""
    NO_ACTION = "NO_ACTION"
    WATCH = "WATCH"
    CANDIDATE_LONG = "CANDIDATE_LONG"
    CANDIDATE_SHORT = "CANDIDATE_SHORT"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class ConfidenceLevel(Enum):
    """Categorical confidence for Commander 2.0."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ExecutionAuthority(Enum):
    """Execution authority for Commander 2.0."""
    NONE = "NONE"


RECORD_PROVENANCE_VALUES = frozenset({"PRODUCTION_RUNTIME", "TEST", "ACCEPTANCE", "SYNTHETIC", "UNKNOWN"})


@dataclass
class CommanderDecision:
    """Commander 2.0 shadow decision contract.

    All fields required. Never use operational verbs (BUY, SELL, OPEN, CLOSE, CANCEL).
    Always: shadow_only=true, execution_authority=NONE
    Fail-closed on insufficient evidence.
    """

    # Identity
    commander_decision_id: str = field(default_factory=lambda: str(uuid4()))
    commander_version: str = "2.0.0"
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Subject
    subject: str = ""
    symbol: Optional[str] = None
    strategy_id: Optional[str] = None
    strategy_name: Optional[str] = None
    reference_price: Optional[Any] = None
    market_regime: Optional[str] = None
    record_provenance: str = "UNKNOWN"

    # Decision
    decision: DecisionType = DecisionType.INSUFFICIENT_DATA
    confidence: ConfidenceLevel = ConfidenceLevel.NOT_APPLICABLE

    # Output
    summary: str = ""
    rationale: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    # Sources
    skills_used: List[str] = field(default_factory=list)
    mcp_tools_used: List[str] = field(default_factory=list)
    rag_context_used: List[Dict[str, Any]] = field(default_factory=list)

    # Risk/Gaps — use exact DATA_NOT_AVAILABLE marker
    risk_flags: List[str] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    # Required shadow constraints
    shadow_only: bool = True
    execution_authority: ExecutionAuthority = ExecutionAuthority.NONE

    def validate(self) -> bool:
        """Validate contract constraints. Fail-closed on insufficient evidence."""

        # MANDATORY: Required string fields non-empty
        if not self.commander_decision_id:
            raise ValueError("commander_decision_id required")
        if not self.commander_version:
            raise ValueError("commander_version required")
        if not self.subject:
            raise ValueError("subject required")
        if not self.summary:
            raise ValueError("summary required")
        if not self.rationale:
            raise ValueError("rationale required")

        # MANDATORY: timestamp must be timezone-aware UTC with zero offset
        if self.timestamp_utc is None:
            raise ValueError("timestamp_utc required")
        if self.timestamp_utc.tzinfo is None:
            raise ValueError("timestamp_utc must be timezone-aware (UTC)")
        if self.timestamp_utc.tzinfo != timezone.utc:
            raise ValueError("timestamp_utc must be UTC (offset must be zero)")

        # MANDATORY: decision and confidence must be enums
        if not isinstance(self.decision, DecisionType):
            raise ValueError("decision must be DecisionType enum")
        if not isinstance(self.confidence, ConfidenceLevel):
            raise ValueError("confidence must be ConfidenceLevel enum")

        # MANDATORY: collection types must be lists
        if not isinstance(self.evidence, list):
            raise ValueError("evidence must be list")
        if not isinstance(self.skills_used, list):
            raise ValueError("skills_used must be list")
        if not isinstance(self.mcp_tools_used, list):
            raise ValueError("mcp_tools_used must be list")
        if not isinstance(self.rag_context_used, list):
            raise ValueError("rag_context_used must be list")
        if not isinstance(self.risk_flags, list):
            raise ValueError("risk_flags must be list")
        if not isinstance(self.data_gaps, list):
            raise ValueError("data_gaps must be list")
        if not isinstance(self.limitations, list):
            raise ValueError("limitations must be list")
        if self.record_provenance not in RECORD_PROVENANCE_VALUES:
            raise ValueError("record_provenance invalid")

        # MANDATORY: evidence items must be dicts
        for item in self.evidence:
            if not isinstance(item, dict):
                raise ValueError("evidence items must be dicts")

        # MANDATORY: rag_context_used items must be dicts
        for item in self.rag_context_used:
            if not isinstance(item, dict):
                raise ValueError("rag_context_used items must be dicts")

        # CONTRACT: RAG unavailable -> explicit marker
        if not self.rag_context_used:
            if "DATA_NOT_AVAILABLE" not in self.data_gaps:
                self.data_gaps.append("DATA_NOT_AVAILABLE")

        # FAIL-CLOSED: Insufficient evidence normalizes to INSUFFICIENT_DATA
        actionable_decisions = [
            DecisionType.WATCH,
            DecisionType.CANDIDATE_LONG,
            DecisionType.CANDIDATE_SHORT,
        ]

        if self.decision in actionable_decisions:
            if not self.evidence or len(self.evidence) == 0:
                # Fail-closed: normalize to INSUFFICIENT_DATA
                self.decision = DecisionType.INSUFFICIENT_DATA
                self.confidence = ConfidenceLevel.NOT_APPLICABLE
                if "INSUFFICIENT_EVIDENCE" not in self.data_gaps:
                    self.data_gaps.append("INSUFFICIENT_EVIDENCE")

        # MANDATORY: shadow_only must be true
        if not self.shadow_only:
            raise ValueError("shadow_only must be True")

        # MANDATORY: execution_authority must be NONE
        if self.execution_authority != ExecutionAuthority.NONE:
            raise ValueError("execution_authority must be ExecutionAuthority.NONE")

        # MANDATORY: no operational verbs (word-boundary matching)
        operational_verbs = ["BUY", "SELL", "OPEN", "CLOSE", "CANCEL"]
        combined_text = f"{self.summary} {self.rationale}".upper()

        for verb in operational_verbs:
            # Word boundary: ensure it's a standalone verb
            pattern = rf'\b{re.escape(verb)}\b'
            if re.search(pattern, combined_text):
                raise ValueError(f"Operational verb '{verb}' not allowed in shadow mode")

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "commander_decision_id": self.commander_decision_id,
            "commander_version": self.commander_version,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "subject": self.subject,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "reference_price": self.reference_price,
            "market_regime": self.market_regime,
            "record_provenance": self.record_provenance,
            "decision": self.decision.value,
            "confidence": self.confidence.value,
            "summary": self.summary,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "skills_used": self.skills_used,
            "mcp_tools_used": self.mcp_tools_used,
            "rag_context_used": self.rag_context_used,
            "risk_flags": self.risk_flags,
            "data_gaps": self.data_gaps,
            "limitations": self.limitations,
            "shadow_only": self.shadow_only,
            "execution_authority": self.execution_authority.value,
        }


@dataclass
class ShadowEvaluation:
    """Post-execution evaluation of shadow decision.

    Missing/unavailable fields use DATA_NOT_AVAILABLE in serialization.
    """

    decision_id: str
    decision_timestamp: datetime
    decision: str
    symbol: Optional[str]
    reference_price: Optional[float] = None
    evaluation_horizon: Optional[str] = None
    outcome_timestamp: Optional[datetime] = None
    outcome_price: Optional[float] = None
    return_pct: Optional[float] = None
    direction_correct: Optional[bool] = None
    max_favorable_excursion: Optional[float] = None
    max_adverse_excursion: Optional[float] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary with DATA_NOT_AVAILABLE for missing values."""
        result = {
            "decision_id": self.decision_id,
            "timestamp": self.decision_timestamp.isoformat() if self.decision_timestamp else "DATA_NOT_AVAILABLE",
            "decision_timestamp": self.decision_timestamp.isoformat() if self.decision_timestamp else "DATA_NOT_AVAILABLE",
            "decision": self.decision,
            "symbol": self.symbol if self.symbol else "DATA_NOT_AVAILABLE",
        }

        # Optional fields: use DATA_NOT_AVAILABLE when None
        for field_name in ['reference_price', 'evaluation_horizon', 'outcome_timestamp',
                          'outcome_price', 'return_pct', 'direction_correct',
                          'max_favorable_excursion', 'max_adverse_excursion', 'notes']:
            val = getattr(self, field_name)
            if val is None:
                result[field_name] = "DATA_NOT_AVAILABLE"
            elif isinstance(val, datetime):
                result[field_name] = val.isoformat()
            else:
                result[field_name] = val

        result["MFE"] = result["max_favorable_excursion"]
        result["MAE"] = result["max_adverse_excursion"]

        return result
