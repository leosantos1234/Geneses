"""Pure deterministic guardrails for Commander 2.0 shadow outputs."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .contract import ConfidenceLevel, DecisionType

GUARDRAIL_BLOCKED = "DETERMINISTIC_GUARDRAIL_BLOCKED"
GUARDRAIL_INSUFFICIENT_EVIDENCE = "DETERMINISTIC_GUARDRAIL_INSUFFICIENT_EVIDENCE"
GUARDRAIL_INVALID_RESULT = "DETERMINISTIC_GUARDRAIL_INVALID_RESULT"

_ACTIONABLE = {DecisionType.WATCH, DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT}
_BLOCKING_FLAGS = {
    "RISK_BLOCKED", "RISK_GUARD_BLOCKED", "EXECUTION_BLOCK",
    "EXECUTION_BLOCKED", "DATABASE_UNAVAILABLE",
}
_OPERATIONAL_VERBS = re.compile(r"\b(?:BUY|SELL|OPEN|CLOSE|CANCEL)\b", re.IGNORECASE)


def _as_list(value: Optional[Iterable[Any]]) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        return [value]
    return list(value)


def _safe_result(reason: str, *, data_gaps: List[str], risk_flags: List[str]) -> Dict[str, Any]:
    return {
        "decision": DecisionType.INSUFFICIENT_DATA,
        "confidence": ConfidenceLevel.NOT_APPLICABLE,
        "summary": "Insufficient evidence",
        "rationale": reason,
        "data_gaps": data_gaps,
        "risk_flags": risk_flags,
    }


def apply_deterministic_guardrails(
    result: Mapping[str, Any],
    *,
    evidence: Optional[Iterable[Mapping[str, Any]]] = None,
    risk_flags: Optional[Iterable[str]] = None,
    data_gaps: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Return a safe shadow result without invoking any operational component.

    Explicit blocks produced by the existing risk/execution layers are
    consumed, not re-evaluated. No order, position, leverage, or persistence
    operation is reachable from this function.
    """
    output = deepcopy(dict(result))
    flags = [str(item) for item in _as_list(risk_flags)]
    gaps = [str(item) for item in _as_list(data_gaps)]
    items = list(evidence or [])
    output["risk_flags"] = flags
    output["data_gaps"] = gaps

    decision = output.get("decision")
    if not isinstance(decision, DecisionType):
        return _safe_result("Guardrail rejected an invalid decision type", data_gaps=gaps, risk_flags=flags + [GUARDRAIL_INVALID_RESULT])

    if any(not isinstance(item, Mapping) for item in items):
        return _safe_result("Guardrail rejected malformed evidence", data_gaps=gaps, risk_flags=flags + [GUARDRAIL_INVALID_RESULT])

    if decision in _ACTIONABLE and not items:
        if GUARDRAIL_INSUFFICIENT_EVIDENCE not in gaps:
            gaps.append(GUARDRAIL_INSUFFICIENT_EVIDENCE)
        return _safe_result("No evidence is available for a shadow assessment", data_gaps=gaps, risk_flags=flags)

    normalized_flags = {flag.upper().strip() for flag in flags}
    evidence_flags = {
        str(flag).upper().strip()
        for item in items
        for flag in _as_list(item.get("risk_flags"))
    }
    if normalized_flags.intersection(_BLOCKING_FLAGS) or evidence_flags.intersection(_BLOCKING_FLAGS):
        if GUARDRAIL_BLOCKED not in flags:
            flags.append(GUARDRAIL_BLOCKED)
        return {
            "decision": DecisionType.NO_ACTION,
            "confidence": ConfidenceLevel.LOW,
            "summary": "Deterministic guardrail blocked the shadow candidate",
            "rationale": "An explicit risk or execution block is present; no operational action is authorized",
            "risk_flags": flags,
            "data_gaps": gaps,
        }

    text = f"{output.get('summary', '')} {output.get('rationale', '')}"
    if _OPERATIONAL_VERBS.search(text):
        return _safe_result("Guardrail rejected operational language in a shadow result", data_gaps=gaps, risk_flags=flags + [GUARDRAIL_INVALID_RESULT])

    return output

