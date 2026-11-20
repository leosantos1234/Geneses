"""Standardized error codes and exceptions for Skills."""

from enum import Enum
from typing import Optional


class SkillErrorCode(Enum):
    """Standard error codes for Skills."""
    INVALID_SKILL_INPUT = "INVALID_SKILL_INPUT"
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_DISABLED = "SKILL_DISABLED"
    SKILL_TOOL_NOT_ALLOWED = "SKILL_TOOL_NOT_ALLOWED"
    MCP_TOOL_ERROR = "MCP_TOOL_ERROR"
    MCP_NOT_AVAILABLE = "MCP_NOT_AVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    SKILL_TIMEOUT = "SKILL_TIMEOUT"
    SKILL_INTERNAL_ERROR = "SKILL_INTERNAL_ERROR"
    SKILL_TOOL_CALL_LIMIT_EXCEEDED = "SKILL_TOOL_CALL_LIMIT_EXCEEDED"
    SKILL_RECURSION_NOT_ALLOWED = "SKILL_RECURSION_NOT_ALLOWED"


class SkillException(Exception):
    """Base exception for Skill errors."""

    def __init__(
        self,
        code: SkillErrorCode,
        message: str,
        details: Optional[dict] = None
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")


class SkillInputError(SkillException):
    """Invalid skill input."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(SkillErrorCode.INVALID_SKILL_INPUT, message, details)


class SkillNotFoundError(SkillException):
    """Skill not found in registry."""
    def __init__(self, skill_id: str):
        super().__init__(
            SkillErrorCode.SKILL_NOT_FOUND,
            f"Skill '{skill_id}' not found",
            {"skill_id": skill_id}
        )


class SkillDisabledError(SkillException):
    """Skill is disabled."""
    def __init__(self, skill_id: str):
        super().__init__(
            SkillErrorCode.SKILL_DISABLED,
            f"Skill '{skill_id}' is disabled",
            {"skill_id": skill_id}
        )


class ToolNotAllowedError(SkillException):
    """Tool is not in allowlist for this Skill."""
    def __init__(self, skill_id: str, tool_name: str):
        super().__init__(
            SkillErrorCode.SKILL_TOOL_NOT_ALLOWED,
            f"Tool '{tool_name}' not allowed for skill '{skill_id}'",
            {"skill_id": skill_id, "tool_name": tool_name}
        )


class MCPToolError(SkillException):
    """Error from MCP tool execution."""
    def __init__(self, tool_name: str, error_code: str, message: str):
        super().__init__(
            SkillErrorCode.MCP_TOOL_ERROR,
            f"MCP tool '{tool_name}' failed: {error_code}",
            {"tool_name": tool_name, "error_code": error_code, "message": message}
        )


class MCPNotAvailableError(SkillException):
    """MCP server not available."""
    def __init__(self, message: str = "MCP server not available"):
        super().__init__(SkillErrorCode.MCP_NOT_AVAILABLE, message)


class InsufficientEvidenceError(SkillException):
    """Not enough evidence to complete skill."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(SkillErrorCode.INSUFFICIENT_EVIDENCE, message, details)


class SkillTimeoutError(SkillException):
    """Skill execution timed out."""
    def __init__(self, skill_id: str, timeout_seconds: int):
        super().__init__(
            SkillErrorCode.SKILL_TIMEOUT,
            f"Skill '{skill_id}' timed out after {timeout_seconds}s",
            {"skill_id": skill_id, "timeout_seconds": timeout_seconds}
        )


class SkillInternalError(SkillException):
    """Internal skill error."""
    def __init__(self, skill_id: str, message: str, details: Optional[dict] = None):
        super().__init__(
            SkillErrorCode.SKILL_INTERNAL_ERROR,
            f"Internal error in skill '{skill_id}': {message}",
            details or {}
        )


class SkillExecutionError(SkillException):
    """Error during skill execution (MCP or other)."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__(
            SkillErrorCode.MCP_TOOL_ERROR,
            message,
            details or {}
        )


class SkillToolCallLimitExceededError(SkillException):
    """Maximum tool calls exceeded for skill."""
    def __init__(self, skill_id: str, max_calls: int):
        super().__init__(
            SkillErrorCode.SKILL_TOOL_CALL_LIMIT_EXCEEDED,
            f"Skill '{skill_id}' exceeded maximum tool calls ({max_calls})",
            {"skill_id": skill_id, "max_calls": max_calls}
        )


class SkillRecursionNotAllowedError(SkillException):
    """Skill recursion is not allowed."""
    def __init__(self, skill_id: str):
        super().__init__(
            SkillErrorCode.SKILL_RECURSION_NOT_ALLOWED,
            f"Recursive execution of skill '{skill_id}' is not allowed",
            {"skill_id": skill_id}
        )
