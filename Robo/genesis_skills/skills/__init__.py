"""Genesis Analytical Skills."""

from genesis_skills.skills.base import BaseSkill, SkillResult
from genesis_skills.skills.trade_decision_autopsy import TradeDecisionAutopsySkill
from genesis_skills.skills.risk_review import RiskReviewSkill
from genesis_skills.skills.lstm_diagnostics import LSTMDiagnosticsSkill
from genesis_skills.skills.system_health_review import SystemHealthReviewSkill

__all__ = [
    "BaseSkill",
    "SkillResult",
    "TradeDecisionAutopsySkill",
    "RiskReviewSkill",
    "LSTMDiagnosticsSkill",
    "SystemHealthReviewSkill",
]

# Skill registry mapping
SKILL_CLASSES = {
    "trade_decision_autopsy": TradeDecisionAutopsySkill,
    "risk_review": RiskReviewSkill,
    "lstm_diagnostics": LSTMDiagnosticsSkill,
    "system_health_review": SystemHealthReviewSkill,
}
