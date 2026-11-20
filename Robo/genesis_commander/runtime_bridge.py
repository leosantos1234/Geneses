"""Synchronous bridge from the legacy worker to Commander 2.0 shadow mode.

The worker is synchronous, while the canonical Commander pipeline is async.
This bridge deliberately exposes only shadow results and never executes orders.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from genesis_commander.orchestrator import close_orchestrator, get_orchestrator

logger = logging.getLogger(__name__)


async def _run_shadow(
    subject: str,
    symbol: str | None,
    analytical_context: dict[str, Any] | None,
    as_of: datetime | None,
) -> dict[str, Any]:
    orchestrator = get_orchestrator()
    try:
        decision = await orchestrator.run_shadow_decision(
            subject=subject,
            symbol=symbol,
            as_of=as_of,
            analytical_context=analytical_context or {},
        )
        result = decision.to_dict()
        # Keep the canonical identifier explicit at the synchronous boundary.
        # Downstream lineage/observability code must never infer it from time
        # or from a nearby decision row.
        result["commander_decision_id"] = str(decision.commander_decision_id)
        return result
    finally:
        await close_orchestrator()


def run_shadow_decision_sync(
    *,
    subject: str,
    symbol: str | None,
    analytical_context: dict[str, Any] | None = None,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Run one canonical Commander decision from the synchronous worker.

    Errors are returned as a fail-closed shadow result so the legacy worker can
    record the failure without ever treating it as execution authority.
    """
    try:
        return asyncio.run(_run_shadow(subject, symbol, analytical_context, as_of))
    except Exception as exc:  # pragma: no cover - exercised by real runtime
        logger.error("CANONICAL_COMMANDER_SHADOW_FAILURE error=%s", type(exc).__name__)
        return {
            "decision": "INSUFFICIENT_DATA",
            "confidence": "NOT_APPLICABLE",
            "summary": "Canonical Commander shadow execution failed",
            "rationale": "Fail-closed runtime bridge",
            "skills_used": [],
            "mcp_tools_used": [],
            "rag_context_used": [],
            "data_gaps": ["CANONICAL_COMMANDER_SHADOW_FAILURE"],
            "shadow_only": True,
            "execution_authority": "NONE",
        }
