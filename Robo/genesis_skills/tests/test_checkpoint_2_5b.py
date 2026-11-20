"""Checkpoint 2.5B — Complete validation before Phase 3."""

import pytest
from genesis_skills.manifest import SkillStatus, SkillAuthority
from genesis_skills.registry import get_registry
from genesis_skills.bootstrap import bootstrap_default_registry
from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
from genesis_skills.mcp_errors import normalize_mcp_error, MCPErrorCode
from genesis_skills.skills import (
    TradeDecisionAutopsySkill, RiskReviewSkill,
    LSTMDiagnosticsSkill, SystemHealthReviewSkill
)


class TestCheckpoint25B:
    """Comprehensive checkpoint 2.5B validation."""

    def test_01_files_layout(self):
        """FILES_LAYOUT = PASS"""
        assert TradeDecisionAutopsySkill
        assert RiskReviewSkill
        assert LSTMDiagnosticsSkill
        assert SystemHealthReviewSkill

    def test_02_manifest_contract(self):
        """MANIFEST_CONTRACT = PASS (procedure field exists)"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            assert hasattr(manifest, 'procedure')
            assert manifest.to_dict()  # Full dict includes all fields

    def test_03_registry_bootstrap(self):
        """REGISTRY_BOOTSTRAP = PASS"""
        registry = bootstrap_default_registry()
        assert registry.count() == 4

    def test_04_registry_idempotent(self):
        """REGISTRY_BOOTSTRAP_IDEMPOTENT = PASS"""
        registry1 = bootstrap_default_registry()
        count1 = registry1.count()
        registry2 = bootstrap_default_registry()
        count2 = registry2.count()
        assert count1 == count2 == 4

    def test_05_mcp_adapter(self):
        """MCP_CLIENT_ADAPTER = PASS"""
        adapter = MCPReadOnlyClientAdapter()
        assert adapter.ALLOWED_TOOLS
        assert len(adapter.ALLOWED_TOOLS) == 10
        # Verify no 11th tool
        assert "undefined_tool" not in adapter.ALLOWED_TOOLS

    def test_06_mcp_error_normalization(self):
        """MCP_ERROR_NORMALIZATION = PASS"""
        error_dict = {"error": "NOT_FOUND", "message": "Not found"}
        code, msg = normalize_mcp_error(error_dict)
        assert code == MCPErrorCode.NOT_FOUND

    def test_07_authority(self):
        """AUTHORITY = ANALYTICAL_READ_ONLY"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            assert manifest.authority == SkillAuthority.ANALYTICAL_READ_ONLY

    def test_08_side_effects(self):
        """SIDE_EFFECTS = NONE"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            assert manifest.side_effects == "NONE"

    def test_09_mcp_tool_count(self):
        """MCP_TOOL_COUNT_EXPECTED = 10"""
        adapter = MCPReadOnlyClientAdapter()
        expected = {
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
        assert adapter.ALLOWED_TOOLS == expected
        assert len(adapter.ALLOWED_TOOLS) == 10

    def test_10_no_11th_tool(self):
        """Verify no 11th tool added"""
        adapter = MCPReadOnlyClientAdapter()
        # Skills should not add new tools
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            for tool in manifest.allowed_mcp_tools:
                assert tool in adapter.ALLOWED_TOOLS

    def test_11_manifest_complete(self):
        """MANIFEST_CONTRACT_COMPLETE = 4/4"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            # All required fields present
            assert manifest.skill_id
            assert manifest.name
            assert manifest.version
            assert manifest.status == SkillStatus.AVAILABLE
            assert manifest.allowed_mcp_tools
            assert manifest.preconditions is not None
            assert manifest.success_criteria is not None
            assert manifest.failure_conditions is not None
            assert manifest.abort_conditions is not None
            assert manifest.limitations is not None
            assert manifest.evidence_requirements is not None

    def test_12_skill_status_available(self):
        """All skills AVAILABLE"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            assert skill_class.MANIFEST.status == SkillStatus.AVAILABLE

    def test_13_registry_duplicate_protection(self):
        """Idempotent registration without silent overwrites"""
        registry = get_registry()
        registry._skills.clear()  # Reset for test

        # First registration
        registry.register(TradeDecisionAutopsySkill.MANIFEST)
        assert registry.count() == 1

        # Re-register same skill (idempotent)
        registry.register(TradeDecisionAutopsySkill.MANIFEST)
        assert registry.count() == 1  # No duplicate

    def test_14_imports_ok(self):
        """IMPORTS_OK = YES"""
        from genesis_skills.manifest import SkillManifest
        from genesis_skills.registry import SkillRegistry
        from genesis_skills.executor import SkillExecutor
        from genesis_skills.errors import SkillException
        from genesis_skills.mcp_adapter import MCPReadOnlyClientAdapter
        from genesis_skills.mcp_errors import normalize_mcp_error
        from genesis_skills.bootstrap import bootstrap_default_registry
        assert all([
            SkillManifest, SkillRegistry, SkillExecutor, SkillException,
            MCPReadOnlyClientAdapter, normalize_mcp_error, bootstrap_default_registry
        ])

    def test_15_recursion_protection(self):
        """RECURSION_PROTECTION = PASS (no skill-to-skill calls)"""
        # Skills should only call MCP tools, not other skills
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            # Verify no skill_ids in allowed_mcp_tools
            manifest = skill_class.MANIFEST
            skill_ids = [s.MANIFEST.skill_id for s in [
                TradeDecisionAutopsySkill, RiskReviewSkill,
                LSTMDiagnosticsSkill, SystemHealthReviewSkill
            ]]
            for tool in manifest.allowed_mcp_tools:
                assert tool not in skill_ids

    def test_16_valid_manifests(self):
        """All manifests validate"""
        for skill_class in [TradeDecisionAutopsySkill, RiskReviewSkill,
                           LSTMDiagnosticsSkill, SystemHealthReviewSkill]:
            manifest = skill_class.MANIFEST
            assert manifest.validate()


def run_checkpoint_2_5b():
    """Run full checkpoint 2.5B validation."""
    pytest.main([__file__, "-v", "--tb=short"])


if __name__ == "__main__":
    run_checkpoint_2_5b()
