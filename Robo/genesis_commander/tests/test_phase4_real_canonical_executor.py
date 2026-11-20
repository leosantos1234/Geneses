"""Phase 4 real canonical executor acceptance — no mocks for registry, executor or MCP."""

import pytest

from genesis_skills.audit import AuditStatus, get_audit_logger
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.executor import SkillExecutor
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.skills import SystemHealthReviewSkill
from genesis_skills.skills.base import BaseSkill, SkillResult


@pytest.fixture(scope="module", autouse=True)
def bootstrapped_registry():
    bootstrap_default_registry()
    yield


def _inputs(subject):
    return {"subject": subject, "symbol": None, "analytical_context": {}, "as_of": None}


@pytest.mark.anyio
async def test_real_system_health_review_end_to_end():
    adapter = MCPReadOnlyClientAdapter()
    executor = SkillExecutor(
        mcp_client=adapter,
        skill_classes={"system_health_review": SystemHealthReviewSkill},
    )
    try:
        result = await executor.execute("system_health_review", _inputs("phase4_real_executor"))

        assert result.status == "SUCCESS", result.summary
        assert result.skill_id == "system_health_review"
        assert result.audit_run_id is not None

        record = get_audit_logger().get_record(result.audit_run_id)
        assert record is not None, "audit record must round-trip by audit_run_id"
        assert record.skill_run_id == result.audit_run_id
        assert record.skill_id == "system_health_review"
        assert record.status == AuditStatus.SUCCESS.value
        assert record.tools_called, "Skill must have called at least one MCP tool"
        assert set(record.tools_called) <= adapter.ALLOWED_TOOLS
    finally:
        await executor.close()


@pytest.mark.anyio
async def test_skill_id_mismatch_fails_closed():
    class MismatchedSkill(BaseSkill):
        async def execute(self, inputs):
            return SkillResult(
                skill_id="risk_review",
                skill_version="1.0.0",
                subject=inputs["subject"],
                status="SUCCESS",
                summary="wrong skill",
            )

    adapter = MCPReadOnlyClientAdapter()
    executor = SkillExecutor(
        mcp_client=adapter,
        skill_classes={"system_health_review": MismatchedSkill},
    )
    try:
        result = await executor.execute("system_health_review", _inputs("phase4_mismatch"))

        assert result.status == "FAILED"
        assert result.skill_id == "system_health_review"
        assert "SKILL_ID_MISMATCH" in result.summary
        assert result.audit_run_id is None
        assert adapter.is_running is False

        records = [
            r for r in get_audit_logger().list_records()
            if r.skill_id == "system_health_review" and r.error_code == "SKILL_ID_MISMATCH"
        ]
        assert len(records) == 1
        assert records[0].status == AuditStatus.FAILED.value
    finally:
        await executor.close()
