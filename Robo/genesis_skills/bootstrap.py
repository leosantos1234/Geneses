"""Bootstrap registry with default skills."""

from genesis_skills.registry import get_registry
from genesis_skills.skills.trade_decision_autopsy import TradeDecisionAutopsySkill
from genesis_skills.skills.risk_review import RiskReviewSkill
from genesis_skills.skills.lstm_diagnostics import LSTMDiagnosticsSkill
from genesis_skills.skills.system_health_review import SystemHealthReviewSkill


def bootstrap_default_registry():
    """Register all default skills in global registry."""
    registry = get_registry()

    # Register all 4 skills
    registry.register(TradeDecisionAutopsySkill.MANIFEST)
    registry.register(RiskReviewSkill.MANIFEST)
    registry.register(LSTMDiagnosticsSkill.MANIFEST)
    registry.register(SystemHealthReviewSkill.MANIFEST)

    # Verify count
    assert registry.count() == 4, f"Expected 4 skills, got {registry.count()}"

    return registry
