"""Deterministic, manual-only promotion criteria for Sprint 9 Phase 14.

This module assesses supplied validation evidence.  It does not query or write
production data, alter Commander decisions, or promote anything automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .contracts import PromotionState, SampleSizeStatus


_SAMPLE_ORDER = {
    SampleSizeStatus.INSUFFICIENT_SAMPLE: 0,
    SampleSizeStatus.LIMITED_SAMPLE: 1,
    SampleSizeStatus.MODERATE_SAMPLE: 2,
    SampleSizeStatus.STRONGER_SAMPLE: 3,
}


@dataclass(frozen=True)
class PromotionCriteriaConfig:
    """Explicit thresholds and policies; no opaque score is used."""

    minimum_sample_status: SampleSizeStatus = SampleSizeStatus.MODERATE_SAMPLE
    minimum_expectancy: Decimal = Decimal("0")
    minimum_net_pnl: Decimal = Decimal("0")
    minimum_profit_factor: Decimal | None = Decimal("1")
    required_robustness_interval_status: str = "POSITIVE_INTERVAL"
    allowed_robustness_stability: tuple[str, ...] = ("STABLE",)
    required_sign_consistency: tuple[str, ...] = ("CONSISTENT_POSITIVE",)
    allowed_walk_forward_stability: tuple[str, ...] = ("CONSISTENT", "MOSTLY_CONSISTENT")
    allowed_oos_generalization: tuple[str, ...] = ("GENERALIZES", "PARTIALLY_GENERALIZES")
    drawdown_must_be_acceptable: bool = True
    loss_concentration_must_be_acceptable: bool = True
    tail_loss_must_be_acceptable: bool = True
    exposure_must_be_acceptable: bool = True
    block_benchmark_dominance: bool = True
    block_high_severity_oos_errors: bool = True
    block_confidence_underperformance: bool = True
    manual_review_required: bool = True
    auto_promotion: bool = False

    def __post_init__(self) -> None:
        if self.auto_promotion:
            raise ValueError("auto_promotion must remain disabled")
        if not self.manual_review_required:
            raise ValueError("manual_review_required must remain enabled")


@dataclass(frozen=True)
class PromotionEvidence:
    """Canonical, read-only evidence from Phases 4 through 13."""

    sample_size_status: SampleSizeStatus | None = None
    net_pnl: Decimal | None = None
    expectancy_net_pnl: Decimal | None = None
    profit_factor: Decimal | None = None
    max_drawdown: Decimal | None = None
    max_drawdown_pct: Decimal | None = None
    drawdown_acceptable: bool | None = None
    loss_concentration_acceptable: bool | None = None
    tail_loss_acceptable: bool | None = None
    exposure_acceptable: bool | None = None
    robustness_interval_status: str | None = None
    robustness_stability_status: str | None = None
    robustness_sign_consistency_status: str | None = None
    robustness_outlier_status: str | None = None
    walk_forward_valid_fold_count: int | None = None
    walk_forward_stability_status: str | None = None
    walk_forward_leakage_free: bool | None = None
    walk_forward_net_pnl: Decimal | None = None
    oos_sample_size_status: SampleSizeStatus | None = None
    oos_generalization_status: str | None = None
    oos_net_pnl: Decimal | None = None
    oos_expectancy: Decimal | None = None
    benchmark_dominated: bool | None = None
    unresolved_high_severity_oos_errors: bool | None = None
    high_confidence_underperforms: bool | None = None
    strategy_dependency_flags: tuple[str, ...] = ()
    regime_dependency_flags: tuple[str, ...] = ()
    direction_dependency_flags: tuple[str, ...] = ()
    time_volatility_dependency_flags: tuple[str, ...] = ()
    hard_blockers: tuple[str, ...] = ()
    nondeterministic_validation: bool = False
    source_mutated: bool = False
    paper_to_live_path: bool = False
    real_exchange_call: bool = False
    security_integrity_failure: bool = False


@dataclass(frozen=True)
class PromotionAssessment:
    promotion_state: PromotionState
    manual_review_required: bool
    hard_blockers: tuple[str, ...]
    soft_failures: tuple[str, ...]
    passed_criteria: tuple[str, ...]
    failed_criteria: tuple[str, ...]
    insufficient_criteria: tuple[str, ...]
    evidence_summary: tuple[str, ...]
    rationale_codes: tuple[str, ...]


def _add_missing(name: str, value: object, missing: list[str]) -> bool:
    if value is None:
        missing.append(name)
        return False
    return True


def assess_promotion(evidence: PromotionEvidence, config: PromotionCriteriaConfig = PromotionCriteriaConfig()) -> PromotionAssessment:
    """Return a deterministic state; this function has no side effects."""

    passed: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    soft: list[str] = []
    blockers = list(evidence.hard_blockers)
    if evidence.nondeterministic_validation:
        blockers.append("NONDETERMINISTIC_VALIDATION")
    if evidence.source_mutated:
        blockers.append("SOURCE_VALIDATION_RECORD_MUTATION")
    if evidence.paper_to_live_path:
        blockers.append("PAPER_TO_LIVE_EXECUTION_PATH")
    if evidence.real_exchange_call:
        blockers.append("REAL_EXCHANGE_CALL")
    if evidence.security_integrity_failure:
        blockers.append("SECURITY_OR_INTEGRITY_FAILURE")

    if _add_missing("sample_size", evidence.sample_size_status, missing):
        if _SAMPLE_ORDER[evidence.sample_size_status] < _SAMPLE_ORDER[config.minimum_sample_status]:
            failed.append("MINIMUM_SAMPLE")
        else:
            passed.append("MINIMUM_SAMPLE")

    for name, value, threshold in (
        ("net_pnl", evidence.net_pnl, config.minimum_net_pnl),
        ("expectancy_net_pnl", evidence.expectancy_net_pnl, config.minimum_expectancy),
    ):
        if _add_missing(name, value, missing):
            (passed if value > threshold else failed).append(name)

    if config.minimum_profit_factor is not None:
        if _add_missing("profit_factor", evidence.profit_factor, missing):
            (passed if evidence.profit_factor >= config.minimum_profit_factor else failed).append("profit_factor")

    for name, value in (
        ("drawdown_acceptable", evidence.drawdown_acceptable),
        ("loss_concentration_acceptable", evidence.loss_concentration_acceptable),
        ("tail_loss_acceptable", evidence.tail_loss_acceptable),
        ("exposure_acceptable", evidence.exposure_acceptable),
    ):
        required = getattr(config, name.replace("_acceptable", "_must_be_acceptable"), True)
        if required and _add_missing(name, value, missing):
            (passed if value else failed).append(name)

    if _add_missing("robustness_interval", evidence.robustness_interval_status, missing):
        (passed if evidence.robustness_interval_status == config.required_robustness_interval_status else failed).append("robustness_interval")
    if _add_missing("robustness_stability", evidence.robustness_stability_status, missing):
        (passed if evidence.robustness_stability_status in config.allowed_robustness_stability else failed).append("robustness_stability")
    if _add_missing("robustness_sign_consistency", evidence.robustness_sign_consistency_status, missing):
        (passed if evidence.robustness_sign_consistency_status in config.required_sign_consistency else failed).append("robustness_sign_consistency")
    if _add_missing("robustness_outliers", evidence.robustness_outlier_status, missing):
        (passed if evidence.robustness_outlier_status != "OUTLIER_SENSITIVE" else failed).append("robustness_outliers")

    if _add_missing("walk_forward_valid_folds", evidence.walk_forward_valid_fold_count, missing):
        (passed if evidence.walk_forward_valid_fold_count >= 2 else failed).append("walk_forward_valid_folds")
    if _add_missing("walk_forward_leakage_free", evidence.walk_forward_leakage_free, missing):
        (passed if evidence.walk_forward_leakage_free else blockers).append("walk_forward_leakage_free" if not evidence.walk_forward_leakage_free else "walk_forward_leakage_free")
    if _add_missing("walk_forward_stability", evidence.walk_forward_stability_status, missing):
        (passed if evidence.walk_forward_stability_status in config.allowed_walk_forward_stability else failed).append("walk_forward_stability")

    for name, value, threshold in (
        ("oos_net_pnl", evidence.oos_net_pnl, config.minimum_net_pnl),
        ("oos_expectancy", evidence.oos_expectancy, config.minimum_expectancy),
    ):
        if _add_missing(name, value, missing):
            (passed if value > threshold else failed).append(name)
    if _add_missing("oos_sample_size", evidence.oos_sample_size_status, missing):
        (passed if _SAMPLE_ORDER[evidence.oos_sample_size_status] >= _SAMPLE_ORDER[config.minimum_sample_status] else failed).append("oos_sample_size")
    if _add_missing("oos_generalization", evidence.oos_generalization_status, missing):
        (passed if evidence.oos_generalization_status in config.allowed_oos_generalization else failed).append("oos_generalization")

    if config.block_benchmark_dominance:
        if _add_missing("benchmark_dominance", evidence.benchmark_dominated, missing):
            (failed if evidence.benchmark_dominated else passed).append("benchmark_dominance")
    if config.block_high_severity_oos_errors:
        if _add_missing("high_severity_oos_errors", evidence.unresolved_high_severity_oos_errors, missing):
            (failed if evidence.unresolved_high_severity_oos_errors else passed).append("high_severity_oos_errors")
    if config.block_confidence_underperformance:
        if _add_missing("confidence_quality", evidence.high_confidence_underperforms, missing):
            (failed if evidence.high_confidence_underperforms else passed).append("confidence_quality")

    if blockers:
        state = PromotionState.REJECT
        rationale = ("HARD_BLOCKER",)
    elif failed:
        state = PromotionState.REJECT if any(item in {"net_pnl", "expectancy_net_pnl", "oos_net_pnl", "oos_expectancy", "oos_generalization", "benchmark_dominance", "high_severity_oos_errors"} for item in failed) else PromotionState.NEEDS_IMPROVEMENT
        rationale = ("FAILED_MANDATORY_CRITERIA",)
    elif missing:
        state = PromotionState.KEEP_SHADOW
        rationale = ("INSUFFICIENT_EVIDENCE",)
    else:
        state = PromotionState.CANDIDATE_FOR_NEXT_STAGE
        rationale = ("ALL_CRITERIA_PASS_MANUAL_REVIEW_ONLY",)
    return PromotionAssessment(state, config.manual_review_required, tuple(dict.fromkeys(blockers)), tuple(soft), tuple(passed), tuple(failed), tuple(missing), ("PHASE_4_TO_13_EVIDENCE_ONLY",), rationale)


__all__ = ["PromotionAssessment", "PromotionCriteriaConfig", "PromotionEvidence", "PromotionState", "assess_promotion"]
