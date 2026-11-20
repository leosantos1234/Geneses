from decimal import Decimal
import sys

import pytest

sys.path.insert(0, "Robo")

from genesis_validation.contracts import PromotionState, SampleSizeStatus
from genesis_validation.promotion import PromotionCriteriaConfig, PromotionEvidence, assess_promotion


def evidence(**overrides):
    values = dict(
        sample_size_status=SampleSizeStatus.MODERATE_SAMPLE,
        net_pnl=Decimal("10"), expectancy_net_pnl=Decimal("1"), profit_factor=Decimal("1.5"),
        drawdown_acceptable=True, loss_concentration_acceptable=True, tail_loss_acceptable=True, exposure_acceptable=True,
        robustness_interval_status="POSITIVE_INTERVAL", robustness_stability_status="STABLE",
        robustness_sign_consistency_status="CONSISTENT_POSITIVE", robustness_outlier_status="ROBUST_TO_OUTLIERS",
        walk_forward_valid_fold_count=3, walk_forward_stability_status="CONSISTENT", walk_forward_leakage_free=True,
        oos_sample_size_status=SampleSizeStatus.MODERATE_SAMPLE, oos_generalization_status="GENERALIZES",
        oos_net_pnl=Decimal("4"), oos_expectancy=Decimal("0.4"), benchmark_dominated=False,
        unresolved_high_severity_oos_errors=False, high_confidence_underperforms=False,
    )
    values.update(overrides)
    return PromotionEvidence(**values)


def test_complete_evidence_is_manual_candidate_only():
    result = assess_promotion(evidence())
    assert result.promotion_state is PromotionState.CANDIDATE_FOR_NEXT_STAGE
    assert result.manual_review_required is True
    assert "AUTO" not in " ".join(result.rationale_codes)


def test_missing_criteria_keep_shadow_and_never_false_pass():
    result = assess_promotion(PromotionEvidence())
    assert result.promotion_state is PromotionState.KEEP_SHADOW
    assert result.insufficient_criteria


@pytest.mark.parametrize("field", ["net_pnl", "expectancy_net_pnl", "oos_net_pnl", "oos_expectancy"])
def test_negative_economics_reject(field):
    result = assess_promotion(evidence(**{field: Decimal("-0.01")}))
    assert result.promotion_state is PromotionState.REJECT
    assert result.failed_criteria


def test_insufficient_main_or_oos_sample_does_not_pass():
    assert assess_promotion(evidence(sample_size_status=SampleSizeStatus.LIMITED_SAMPLE)).promotion_state is PromotionState.NEEDS_IMPROVEMENT
    assert assess_promotion(evidence(oos_sample_size_status=SampleSizeStatus.LIMITED_SAMPLE)).promotion_state is PromotionState.NEEDS_IMPROVEMENT


def test_unstable_walk_forward_is_needs_improvement():
    result = assess_promotion(evidence(walk_forward_stability_status="UNSTABLE"))
    assert result.promotion_state is PromotionState.NEEDS_IMPROVEMENT


def test_failed_oos_generalization_is_rejected():
    result = assess_promotion(evidence(oos_generalization_status="FAILS_TO_GENERALIZE"))
    assert result.promotion_state is PromotionState.REJECT


@pytest.mark.parametrize("field", ["benchmark_dominated", "unresolved_high_severity_oos_errors", "high_confidence_underperforms"])
def test_benchmark_errors_and_confidence_are_not_overridden(field):
    result = assess_promotion(evidence(**{field: True}))
    assert result.promotion_state is (PromotionState.REJECT if field != "high_confidence_underperforms" else PromotionState.NEEDS_IMPROVEMENT)


@pytest.mark.parametrize("field", ["nondeterministic_validation", "source_mutated", "paper_to_live_path", "real_exchange_call", "security_integrity_failure"])
def test_hard_blockers_reject(field):
    result = assess_promotion(evidence(**{field: True}))
    assert result.promotion_state is PromotionState.REJECT
    assert result.hard_blockers


def test_walk_forward_leakage_is_hard_blocker():
    result = assess_promotion(evidence(walk_forward_leakage_free=False))
    assert result.promotion_state is PromotionState.REJECT
    assert result.hard_blockers


def test_risk_criteria_are_required_not_fabricated():
    result = assess_promotion(evidence(drawdown_acceptable=None))
    assert result.promotion_state is PromotionState.KEEP_SHADOW
    assert "drawdown_acceptable" in result.insufficient_criteria


def test_config_cannot_enable_automatic_promotion():
    with pytest.raises(ValueError):
        PromotionCriteriaConfig(auto_promotion=True)


def test_no_live_state_or_opaque_score_exists():
    result = assess_promotion(evidence())
    assert result.promotion_state.value in {"REJECT", "KEEP_SHADOW", "NEEDS_IMPROVEMENT", "CANDIDATE_FOR_NEXT_STAGE"}
    assert "score" not in str(result).lower()
    assert "live" not in str(result).lower()
