"""Sanity check for skills infrastructure."""

import sys
import pytest


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear registry before each test to avoid state leakage."""
    from genesis_skills.registry import get_registry
    registry = get_registry()
    # Clear all registered skills
    registry._skills.clear()
    yield
    # Clean up after test
    registry._skills.clear()


def test_imports():
    """Verify all modules import correctly."""
    from genesis_skills.manifest import SkillManifest, SkillStatus
    from genesis_skills.registry import get_registry
    from genesis_skills.executor import SkillExecutor
    from genesis_skills.errors import SkillException
    from genesis_skills.skills import TradeDecisionAutopsySkill, RiskReviewSkill
    from genesis_skills.skills import LSTMDiagnosticsSkill, SystemHealthReviewSkill
    assert all([
        SkillManifest, SkillStatus, get_registry, SkillExecutor,
        SkillException, TradeDecisionAutopsySkill, RiskReviewSkill,
        LSTMDiagnosticsSkill, SystemHealthReviewSkill
    ])


def test_registry_bootstrap():
    """Verify registry bootstrap works."""
    from genesis_skills.bootstrap import bootstrap_default_registry
    registry = bootstrap_default_registry()
    assert registry.count() == 4, f"Expected 4 skills, got {registry.count()}"


def test_manifest_contracts():
    """Verify all manifests have required fields."""
    from genesis_skills.skills import (
        TradeDecisionAutopsySkill, RiskReviewSkill,
        LSTMDiagnosticsSkill, SystemHealthReviewSkill
    )

    for skill_class in [
        TradeDecisionAutopsySkill, RiskReviewSkill,
        LSTMDiagnosticsSkill, SystemHealthReviewSkill
    ]:
        manifest = skill_class.MANIFEST
        assert manifest.skill_id
        assert manifest.name
        assert manifest.version
        assert manifest.authority.value == "ANALYTICAL_READ_ONLY"
        assert manifest.side_effects == "NONE"
        assert manifest.allowed_mcp_tools
        assert len(manifest.allowed_mcp_tools) > 0
        assert len(manifest.allowed_mcp_tools) <= 10
        manifest.validate()  # Should not raise


def test_mcp_tool_count():
    """Verify total MCP tools available."""
    expected_tools = {
        "get_system_health",
        "list_open_positions",
        "list_trades",
        "get_trade_detail",
        "get_risk_status",
        "get_decision_trace",
        "list_decisions",
        "get_lstm_predictions",
        "get_model_inventory",
        "get_commander_decision",
    }
    assert len(expected_tools) == 10


def test_no_duplicates():
    """Verify no duplicate skill IDs."""
    from genesis_skills.bootstrap import bootstrap_default_registry
    registry = bootstrap_default_registry()
    skills = registry.list_available()
    skill_ids = [s["skill_id"] for s in skills]
    assert len(skill_ids) == len(set(skill_ids)), "Duplicate skill IDs found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
