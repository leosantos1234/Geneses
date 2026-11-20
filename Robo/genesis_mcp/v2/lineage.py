"""MCP V2 decision lineage tools: get_decision_trace, list_decisions, get_commander_decision."""
from __future__ import annotations

import os
import uuid
from typing import Annotated, Any

from pydantic import Field

from genesis_runtime import database_backend
from lineage.decision_lineage import get_decision_trace as lineage_get_decision_trace
from lineage.decision_lineage import list_decisions as lineage_list_decisions

from genesis_mcp.common import (
    CommanderDecisionResponse,
    DecisionTrace,
    DecisionTraceResponse,
    ErrorInfo,
    ListDecisionsResponse,
    call_tool,
)
from genesis_mcp.common.validators import valid_filter, valid_limit, valid_offset, valid_time


def _canonical_database_error() -> ErrorInfo | None:
    if database_backend() != "postgres":
        return ErrorInfo(
            code="DATABASE_UNAVAILABLE",
            message="MCP V2 lineage exige PostgreSQL canônico; SQLite não é uma fonte permitida"
        )
    return None


def _error(code: str, message: str) -> ErrorInfo:
    return ErrorInfo(code=code, message=message)


def get_decision_trace_impl(decision_id: str, request_id: str) -> DecisionTraceResponse:
    """Consulta trilha completa de decisão append-only."""
    def run() -> DecisionTraceResponse:
        canonical_error = _canonical_database_error()
        if canonical_error:
            return DecisionTraceResponse(request_id=request_id, source="postgresql", error=canonical_error)

        try:
            uuid.UUID(str(decision_id))
        except (ValueError, TypeError):
            return DecisionTraceResponse(
                request_id=request_id,
                source="postgresql",
                error=_error("INVALID_ARGUMENT", "decision_id deve ser UUID válida")
            )

        invalid = valid_filter(decision_id, "decision_id")
        if invalid:
            return DecisionTraceResponse(request_id=request_id, source="postgresql", error=invalid)

        raw = lineage_get_decision_trace(decision_id)
        if not raw:
            return DecisionTraceResponse(
                request_id=request_id,
                source="postgresql",
                error=_error("NOT_FOUND", "decision_id não encontrado")
            )

        trace = DecisionTrace(**raw)
        return DecisionTraceResponse(request_id=request_id, source="postgresql", trace=trace)

    result = call_tool("get_decision_trace", request_id, run)
    if isinstance(result, Exception):
        return DecisionTraceResponse(
            request_id=request_id,
            source="postgresql",
            error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar trilha de decisão")
        )
    return result


def list_decisions_impl(
    symbol: str | None = None,
    strategy: str | None = None,
    status: str | None = None,
    reason_code: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    trade_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
    offset: Annotated[int, Field(ge=0)] = 0,
    request_id: str = "",
) -> ListDecisionsResponse:
    """Lista decisões com paginação."""
    def run() -> ListDecisionsResponse:
        canonical_error = _canonical_database_error()
        if canonical_error:
            return ListDecisionsResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                error=canonical_error
            )

        for name, value in (
            ("symbol", symbol), ("strategy", strategy), ("status", status),
            ("reason_code", reason_code), ("trade_id", trade_id)
        ):
            invalid = valid_filter(value, name)
            if invalid:
                return ListDecisionsResponse(
                    request_id=request_id,
                    source="postgresql",
                    filters={},
                    count=0,
                    total=0,
                    has_more=False,
                    error=invalid
                )

        for name, value in (("start_time", start_time), ("end_time", end_time)):
            invalid = valid_time(value, name)
            if invalid:
                return ListDecisionsResponse(
                    request_id=request_id,
                    source="postgresql",
                    filters={},
                    count=0,
                    total=0,
                    has_more=False,
                    error=invalid
                )

        if start_time and end_time and start_time > end_time:
            return ListDecisionsResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                error=_error("INVALID_ARGUMENT", "start_time deve ser anterior a end_time")
            )

        invalid_limit = valid_limit(limit, "limit", 1, 500)
        if invalid_limit:
            return ListDecisionsResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                error=invalid_limit
            )

        invalid_offset = valid_offset(offset, "offset")
        if invalid_offset:
            return ListDecisionsResponse(
                request_id=request_id,
                source="postgresql",
                filters={},
                count=0,
                total=0,
                has_more=False,
                error=invalid_offset
            )

        rows = lineage_list_decisions(
            symbol=symbol,
            strategy=strategy,
            status=status,
            reason_code=reason_code,
            start_time=start_time,
            end_time=end_time,
            trade_id=trade_id,
            limit=limit+1,  # Query limit+1 to determine has_more
            offset=offset
        )

        # Determine has_more by checking if we got more than limit
        has_more = len(rows) > limit
        actual_rows = rows[:limit]  # Return only the limit

        filters = {
            "symbol": symbol,
            "strategy": strategy,
            "status": status,
            "reason_code": reason_code,
            "start_time": start_time,
            "end_time": end_time,
            "trade_id": trade_id,
            "limit": limit,
            "offset": offset,
        }

        return ListDecisionsResponse(
            request_id=request_id,
            source="postgresql",
            filters=filters,
            count=len(actual_rows),
            total=None,  # None when using LIMIT+1 strategy (total count not calculated)
            has_more=has_more,
            decisions=actual_rows
        )

    result = call_tool("list_decisions", request_id, run)
    if isinstance(result, Exception):
        return ListDecisionsResponse(
            request_id=request_id,
            source="postgresql",
            filters={},
            count=0,
            total=0,
            has_more=False,
            error=_error("DATABASE_UNAVAILABLE", "Não foi possível listar decisões")
        )
    return result


def get_commander_decision_impl(decision_id: str, request_id: str) -> CommanderDecisionResponse:
    """Consulta decisão do Comandante para um decision_id."""
    def run() -> CommanderDecisionResponse:
        canonical_error = _canonical_database_error()
        if canonical_error:
            return CommanderDecisionResponse(request_id=request_id, source="postgresql", error=canonical_error)

        try:
            uuid.UUID(str(decision_id))
        except (ValueError, TypeError):
            return CommanderDecisionResponse(
                request_id=request_id,
                source="postgresql",
                error=_error("INVALID_ARGUMENT", "decision_id deve ser UUID válida")
            )

        invalid = valid_filter(decision_id, "decision_id")
        if invalid:
            return CommanderDecisionResponse(request_id=request_id, source="postgresql", error=invalid)

        raw = lineage_get_decision_trace(decision_id)
        if not raw:
            return CommanderDecisionResponse(
                request_id=request_id,
                source="postgresql",
                error=_error("NOT_FOUND", "decision_id não encontrado")
            )

        events = raw.get("events", [])
        commander_event = None
        for event in events:
            if event.get("event_type") in ("COMMANDER_ALLOWED", "COMMANDER_BLOCKED"):
                commander_event = event
                break

        commander_status = None
        reason_code = None
        if commander_event:
            commander_status = "allowed" if commander_event.get("event_type") == "COMMANDER_ALLOWED" else "blocked"
            reason_code = commander_event.get("reason_code")

        from ..common import CommanderDecision

        decision = CommanderDecision(
            decision_id=raw.get("decision_id"),
            created_at=raw.get("created_at"),
            symbol=raw.get("symbol"),
            strategy=raw.get("strategy"),
            commander_status=commander_status,
            reason_code=reason_code,
            trade_id=raw.get("trade_id")
        )

        return CommanderDecisionResponse(request_id=request_id, source="postgresql", decision=decision)

    result = call_tool("get_commander_decision", request_id, run)
    if isinstance(result, Exception):
        return CommanderDecisionResponse(
            request_id=request_id,
            source="postgresql",
            error=_error("DATABASE_UNAVAILABLE", "Não foi possível consultar decisão do comandante")
        )
    return result
