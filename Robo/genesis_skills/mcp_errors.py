"""Centralized MCP error normalization."""

from enum import Enum
from typing import Dict, Any, Optional


class MCPErrorCode(Enum):
    """Standard MCP error codes."""
    NOT_FOUND = "NOT_FOUND"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    READ_ONLY_VIOLATION = "READ_ONLY_VIOLATION"


def normalize_mcp_error(raw_error: Any) -> tuple[MCPErrorCode, str]:
    """
    Normalize MCP error to standard code + message.

    Args:
        raw_error: Error from MCP tool

    Returns:
        (MCPErrorCode, message)
    """
    if isinstance(raw_error, dict):
        error_code = raw_error.get("error", "INTERNAL_ERROR")
        message = raw_error.get("message", str(raw_error))
    else:
        error_code = str(raw_error)
        message = str(raw_error)

    # Classify
    if error_code == "NOT_FOUND":
        return MCPErrorCode.NOT_FOUND, message
    elif error_code == "INVALID_ARGUMENT":
        return MCPErrorCode.INVALID_ARGUMENT, message
    elif "DATABASE_UNAVAILABLE" in error_code:
        return MCPErrorCode.DATABASE_UNAVAILABLE, message
    elif "UNAVAILABLE" in error_code:
        return MCPErrorCode.SERVICE_UNAVAILABLE, message
    elif "READ_ONLY" in error_code:
        return MCPErrorCode.READ_ONLY_VIOLATION, message
    else:
        return MCPErrorCode.INTERNAL_ERROR, message


def should_fail_skill(error_code: MCPErrorCode) -> bool:
    """Determine if error should fail the skill."""
    return error_code in [
        MCPErrorCode.DATABASE_UNAVAILABLE,
        MCPErrorCode.SERVICE_UNAVAILABLE,
        MCPErrorCode.INTERNAL_ERROR,
        MCPErrorCode.READ_ONLY_VIOLATION,
    ]


def is_expected_not_found(error_code: MCPErrorCode) -> bool:
    """Check if NOT_FOUND is expected/normal."""
    return error_code == MCPErrorCode.NOT_FOUND
