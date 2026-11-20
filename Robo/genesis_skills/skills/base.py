"""Base class for all analytical Skills."""

from abc import ABC, abstractmethod
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from genesis_skills.manifest import SkillManifest, SkillConfidence
from genesis_skills.errors import (
    ToolNotAllowedError, SkillInputError, SkillExecutionError,
    SkillToolCallLimitExceededError
)
from genesis_skills.mcp_errors import normalize_mcp_error


@dataclass
class SkillResult:
    """Result from skill execution."""
    skill_id: str
    skill_version: str
    subject: str
    status: str  # "SUCCESS", "PARTIAL", "FAILED"
    summary: str
    findings: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    data_gaps: List[str] = field(default_factory=list)
    confidence: str = SkillConfidence.NOT_APPLICABLE.value
    risk_flags: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    audit_run_id: Optional[str] = None  # Execution audit run ID (for exact MCP tool correlation)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "subject": self.subject,
            "status": self.status,
            "summary": self.summary,
            "findings": self.findings,
            "evidence": self.evidence,
            "data_gaps": self.data_gaps,
            "confidence": self.confidence,
            "risk_flags": self.risk_flags,
            "recommendations": self.recommendations,
            "limitations": self.limitations,
        }


class BaseSkill(ABC):
    """Base class for analytical Skills."""

    def __init__(self, manifest: SkillManifest, mcp_client: Any):
        """
        Initialize skill.

        Args:
            manifest: SkillManifest defining the skill
            mcp_client: MCP client for tool calls
        """
        self.manifest = manifest
        self.mcp_client = mcp_client
        self.tool_call_count = 0
        self._audit_record = None  # Set by executor

        # Validate manifest
        manifest.validate()

    def validate_inputs(self, inputs: Dict[str, Any]) -> bool:
        """Validate input parameters."""
        for input_def in self.manifest.inputs:
            if input_def.required and input_def.name not in inputs:
                raise SkillInputError(
                    f"Required input '{input_def.name}' missing",
                    {"missing_input": input_def.name}
                )
        return True

    def validate_tool_allowed(self, tool_name: str) -> bool:
        """Check if tool is allowed for this skill."""
        if tool_name not in self.manifest.allowed_mcp_tools:
            raise ToolNotAllowedError(self.manifest.skill_id, tool_name)
        return True

    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call MCP tool with authorization check and error normalization.

        Args:
            tool_name: Name of tool to call
            arguments: Tool arguments

        Returns:
            Tool response

        Raises:
            SkillToolCallLimitExceededError: If max tool calls exceeded
            ToolNotAllowedError: If tool not in allowlist
            SkillExecutionError: If MCP error is critical
        """
        # Check allowlist
        self.validate_tool_allowed(tool_name)

        # Check tool call limit (N+1 is blocked)
        if self.tool_call_count >= self.manifest.max_tool_calls:
            raise SkillToolCallLimitExceededError(
                self.manifest.skill_id,
                self.manifest.max_tool_calls
            )

        self.tool_call_count += 1

        # Update audit record
        if self._audit_record:
            if tool_name not in self._audit_record.tools_called:
                self._audit_record.tools_called.append(tool_name)

        # Call MCP tool via STDIO
        mcp_result = await self.mcp_client.call(tool_name, arguments)

        # Normalize MCPToolResult dataclass to dict for Skills consumption
        if hasattr(mcp_result, 'success') and not isinstance(mcp_result, dict):
            # MCPToolResult dataclass — convert to dict
            mcp_result = {
                "success": mcp_result.success,
                "tool_name": mcp_result.tool_name,
                "data": mcp_result.data,
                "error_code": mcp_result.error_code,
                "error_message": mcp_result.error_message,
                "request_id": mcp_result.request_id,
            }

        # STDIO returns MCP tool payloads as JSON text blocks. Decode them so
        # Skills consume the same dictionary contract as direct MCP calls.
        if isinstance(mcp_result, dict) and isinstance(mcp_result.get("data"), dict):
            text_payload = mcp_result["data"].get("text")
            if isinstance(text_payload, str):
                try:
                    mcp_result = json.loads(text_payload)
                except json.JSONDecodeError as exc:
                    raise SkillExecutionError(
                        "MCP returned invalid JSON payload",
                        {"mcp_error_code": "INVALID_RESPONSE", "skill_id": self.manifest.skill_id},
                    ) from exc

        # Normalize and validate response
        if isinstance(mcp_result, dict):
            if "error" in mcp_result:
                # MCP returned error — normalize it
                error_code = mcp_result.get("error", "INTERNAL_ERROR")
                error_msg = mcp_result.get("message", "Unknown MCP error")
                normalized = normalize_mcp_error(error_code)
                raise SkillExecutionError(
                    f"MCP error [{error_code}]: {error_msg}",
                    {"mcp_error_code": error_code, "skill_id": self.manifest.skill_id}
                )
            elif "success" in mcp_result and not mcp_result["success"]:
                # Fake success detected — fail closed
                error_code = mcp_result.get("error_code", "MCP_NOT_AVAILABLE")
                normalized = normalize_mcp_error(error_code)
                raise SkillExecutionError(
                    f"MCP error [{error_code}]: {mcp_result.get('error_message', 'MCP failure')}",
                    {"mcp_error_code": error_code, "skill_id": self.manifest.skill_id}
                )

        return mcp_result

    @abstractmethod
    async def execute(self, inputs: Dict[str, Any]) -> SkillResult:
        """
        Execute the skill.

        Args:
            inputs: Input parameters

        Returns:
            SkillResult
        """
        pass

    def _create_evidence(
        self,
        tool: str,
        request_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create evidence record.

        Args:
            tool: Tool that provided evidence
            request_id: Request ID if available
            data: Evidence data
            note: Optional contextual note

        Returns:
            Evidence record
        """
        record = {
            "tool": tool,
            "request_id": request_id or "NOT_AVAILABLE",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        if note:
            record["note"] = note
        return record
