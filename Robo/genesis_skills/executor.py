"""Skill executor — orchestrates skill execution."""

from typing import Dict, Any, Type
import asyncio

from genesis_skills.registry import get_registry
from genesis_skills.manifest import SkillStatus
from genesis_skills.skills.base import BaseSkill, SkillResult
from genesis_skills.errors import (
    SkillNotFoundError, SkillDisabledError, SkillInternalError,
    SkillTimeoutError, SkillException, SkillRecursionNotAllowedError
)
from genesis_skills.audit import get_audit_logger, AuditStatus
from genesis_skills.recursion import is_skill_executing, mark_skill_executing, reset_skill_execution


class SkillExecutor:
    """Executes skills with safety and audit."""

    def __init__(self, mcp_client: Any, skill_classes: Dict[str, Type[BaseSkill]]):
        """
        Initialize executor.

        Args:
            mcp_client: MCP client for tool calls
            skill_classes: Mapping of skill_id to Skill class
        """
        self.mcp_client = mcp_client
        self.skill_classes = skill_classes
        self.registry = get_registry()

    async def execute(
        self,
        skill_id: str,
        inputs: Dict[str, Any]
    ) -> SkillResult:
        """
        Execute a skill with audit and recursion guard.

        Args:
            skill_id: Skill to execute
            inputs: Input parameters

        Returns:
            SkillResult
        """
        # Create audit record
        audit_logger = get_audit_logger()

        # Check recursion guard
        if is_skill_executing(skill_id):
            raise SkillRecursionNotAllowedError(skill_id)

        # Get manifest
        try:
            manifest = self.registry.get(skill_id)
        except (SkillNotFoundError, SkillDisabledError) as e:
            return self._error_result(skill_id, str(e))

        # Create audit record
        audit_record = audit_logger.create_record(skill_id, manifest.version)

        # Check status
        if manifest.status != SkillStatus.AVAILABLE:
            audit_record.finalize(
                status=AuditStatus.FAILED.value,
                tool_call_count=0,
                evidence_count=0,
                error_code="SKILL_DISABLED",
                error_message=f"Skill status: {manifest.status.value}"
            )
            return self._error_result(skill_id, f"Skill status: {manifest.status.value}")

        # Get skill class
        if skill_id not in self.skill_classes:
            audit_record.finalize(
                status=AuditStatus.FAILED.value,
                tool_call_count=0,
                evidence_count=0,
                error_code="SKILL_INTERNAL_ERROR",
                error_message="Skill class not found"
            )
            return self._error_result(skill_id, f"Skill class not found")

        # Create skill instance
        try:
            skill_class = self.skill_classes[skill_id]
            skill = skill_class(manifest, self.mcp_client)
            skill._audit_record = audit_record
        except Exception as e:
            audit_record.finalize(
                status=AuditStatus.FAILED.value,
                tool_call_count=0,
                evidence_count=0,
                error_code="SKILL_INTERNAL_ERROR",
                error_message=f"Failed to create skill: {str(e)}"
            )
            return self._error_result(skill_id, f"Failed to create skill: {str(e)}")

        # Mark skill as executing (get token for cleanup)
        recursion_token = mark_skill_executing(skill_id)

        try:
            # Execute with timeout
            timeout = manifest.timeout_seconds
            result = await asyncio.wait_for(
                skill.execute(inputs),
                timeout=timeout
            )

            if result is not None and result.skill_id != skill_id:
                audit_record.finalize(
                    status=AuditStatus.FAILED.value,
                    tool_call_count=skill.tool_call_count,
                    evidence_count=0,
                    error_code="SKILL_ID_MISMATCH",
                    error_message=f"Result skill_id '{result.skill_id}' != requested '{skill_id}'"
                )
                return self._error_result(
                    skill_id,
                    f"SKILL_ID_MISMATCH: result for '{result.skill_id}' returned for '{skill_id}'"
                )

            # Finalize audit on success
            audit_record.finalize(
                status=AuditStatus.SUCCESS.value,
                tool_call_count=skill.tool_call_count,
                evidence_count=len(result.evidence) if result else 0
            )
            # Attach exact audit run ID for precise MCP tool correlation
            if result:
                result.audit_run_id = audit_record.skill_run_id
            return result

        except asyncio.TimeoutError:
            audit_record.finalize(
                status=AuditStatus.TIMEOUT.value,
                tool_call_count=skill.tool_call_count,
                evidence_count=0,
                error_code="SKILL_TIMEOUT",
                error_message=f"Skill timed out after {manifest.timeout_seconds}s"
            )
            return self._error_result(
                skill_id,
                f"Skill timed out after {manifest.timeout_seconds}s"
            )

        except SkillException as e:
            audit_record.finalize(
                status=AuditStatus.FAILED.value,
                tool_call_count=skill.tool_call_count,
                evidence_count=0,
                error_code=e.code.value if hasattr(e, 'code') else "SKILL_INTERNAL_ERROR",
                error_message=str(e)
            )
            return self._error_result(skill_id, str(e))

        except Exception as e:
            audit_record.finalize(
                status=AuditStatus.FAILED.value,
                tool_call_count=skill.tool_call_count,
                evidence_count=0,
                error_code="SKILL_INTERNAL_ERROR",
                error_message=f"Unexpected error: {str(e)}"
            )
            return self._error_result(
                skill_id,
                f"Unexpected error: {str(e)}"
            )
        finally:
            # Reset skill execution using token (immutable ContextVar)
            reset_skill_execution(recursion_token)

    async def close(self):
        """Shut down the MCP adapter owned by this executor."""
        await self.mcp_client.close()

    def _error_result(self, skill_id: str, error_message: str) -> SkillResult:
        """Create error result."""
        return SkillResult(
            skill_id=skill_id,
            skill_version="1.0.0",
            subject="error",
            status="FAILED",
            summary=error_message,
            findings=[],
            evidence=[],
            data_gaps=[error_message],
        )
