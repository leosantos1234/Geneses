"""Phase 4 legacy / missing-lineage markers — exact persisted roundtrip through real lineage."""

import pytest

from genesis_commander.contract import CommanderDecision, ConfidenceLevel, DecisionType
from genesis_commander.lineage import get_commander_decision_from_lineage, record_commander_decision
from lineage.decision_lineage import create_decision, flush

LEGACY_MARKER = "LEGACY_DATA_NO_LINEAGE"
EMPTY_MARKER = "DATA_NOT_AVAILABLE"


@pytest.mark.anyio
async def test_legacy_and_empty_markers_persist_exactly():
    decision_id = create_decision(strategy="commander_2.0", mode="shadow", context={"acceptance": "phase4_markers"})
    evidence = [
        {"marker": EMPTY_MARKER, "source": "rag", "content": "No matching RAG context available"},
        {"source": LEGACY_MARKER, "content": "Decision predates lineage capture"},
    ]
    decision = CommanderDecision(
        subject="phase4_markers",
        summary="marker roundtrip",
        rationale="marker roundtrip",
        decision=DecisionType.INSUFFICIENT_DATA,
        confidence=ConfidenceLevel.NOT_APPLICABLE,
        evidence=evidence,
        skills_used=[],
        mcp_tools_used=[],
        rag_context_used=[],
        risk_flags=[],
        data_gaps=[EMPTY_MARKER],
        limitations=["Shadow-only analysis"],
    )
    assert decision.validate()

    record_commander_decision(decision)
    assert flush(timeout=15)

    payload = get_commander_decision_from_lineage(decision_id)
    assert payload is not None
    assert payload["evidence"] == evidence
    assert payload["evidence"][0]["marker"] == EMPTY_MARKER
    assert payload["evidence"][1]["source"] == LEGACY_MARKER
    assert payload["data_gaps"] == [EMPTY_MARKER]

