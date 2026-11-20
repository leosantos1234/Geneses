"""Phase 4 Real Registry Test - Fail-hard verification of canonical skills."""

import pytest
from genesis_skills.registry import get_registry
from genesis_skills.bootstrap import bootstrap_default_registry


@pytest.fixture(scope="module", autouse=True)
def bootstrap_registry():
    """Bootstrap registry with approved skills before running tests."""
    bootstrap_default_registry()
    yield


class TestRealCanonicalRegistry:
    """Real registry MUST have all approved skills available. No mocking, no exceptions swallowed."""

    def test_real_registry_all_approved_skills_must_exist(self):
        """
        FAIL-HARD TEST: Registry must have all approved skills.

        If any skill is missing: TEST FAILS.
        No try/except, no logging warnings, no passing anyway.
        """
        registry = get_registry()

        approved_skills = {
            "trade_decision_autopsy",
            "risk_review",
            "lstm_diagnostics",
            "system_health_review",
        }

        missing_skills = []
        unavailable_skills = []

        for skill_id in approved_skills:
            # Must not raise exception
            manifest = registry.get(skill_id)

            # Must exist
            if manifest is None:
                missing_skills.append(skill_id)
            # Must be AVAILABLE
            elif manifest.status.value != "AVAILABLE":
                unavailable_skills.append(f"{skill_id}({manifest.status.value})")

        # Fail if any missing
        assert len(missing_skills) == 0, \
            f"Skills not found in registry: {missing_skills}"

        # Fail if any unavailable
        assert len(unavailable_skills) == 0, \
            f"Skills not available: {unavailable_skills}"

    def test_real_registry_skill_manifests_have_required_fields(self):
        """Verify skill manifests have all required fields."""
        registry = get_registry()

        skill_ids = [
            "trade_decision_autopsy",
            "risk_review",
            "lstm_diagnostics",
            "system_health_review",
        ]

        for skill_id in skill_ids:
            manifest = registry.get(skill_id)

            # Required fields
            assert hasattr(manifest, 'skill_id'), f"{skill_id}: missing skill_id"
            assert hasattr(manifest, 'version'), f"{skill_id}: missing version"
            assert hasattr(manifest, 'status'), f"{skill_id}: missing status"
            assert hasattr(manifest, 'timeout_seconds'), f"{skill_id}: missing timeout_seconds"

            # Validate values
            assert manifest.version, f"{skill_id}: version cannot be empty"
            assert manifest.timeout_seconds > 0, f"{skill_id}: timeout must be positive"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
