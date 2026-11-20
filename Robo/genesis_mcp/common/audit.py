"""Audit logging para tools MCP."""
from __future__ import annotations

import logging
import sys
import time
from typing import Any

LOGGER = logging.getLogger("genesis.mcp.readonly")
if not LOGGER.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)


def audit(tool: str, request_id: str, started: float, status: str, result_count: int | None = None, error_code: str | None = None) -> None:
    LOGGER.info(
        "mcp_audit request_id=%s tool=%s duration_ms=%.2f status=%s result_count=%s error_code=%s",
        request_id, tool, (time.perf_counter() - started) * 1000.0, status,
        result_count if result_count is not None else "-", error_code or "-",
    )


def call_tool(tool: str, request_id: str, fn) -> Any:
    started = time.perf_counter()
    try:
        result = fn()
        count = getattr(result, "count", None)
        audit(tool, request_id, started, "success", count)
        return result
    except TimeoutError as exc:
        audit(tool, request_id, started, "failure", error_code="SERVICE_UNAVAILABLE")
        return exc
    except Exception as exc:
        LOGGER.warning("mcp_internal_error request_id=%s tool=%s type=%s", request_id, tool, type(exc).__name__)
        audit(tool, request_id, started, "failure", error_code="INTERNAL_ERROR")
        return exc
