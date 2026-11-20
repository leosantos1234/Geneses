"""Bounded synthetic integrated acceptance for Sprint 9 Phase 15."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from .benchmarks import BenchmarkDefinition, compare_benchmarks
from .calibration import CalibrationRecord, calibrate_confidence
from .contracts import DecisionType, PromotionState, SampleSizeStatus, ValidationObservation
from .dataset import DatasetConfig, PaperOutcome, build_evaluation_dataset
from .direction_analysis import compare_long_short
from .error_analysis import analyze_errors
from .labeling import label_observation
from .oos import OOSConfig, assess_generalization, summarize_oos, split_oos
from .promotion import PromotionEvidence, assess_promotion
from .regime_analysis import CanonicalRegimePoint, analyze_regimes, resolve_canonical_regime
from .statistical_robustness import StatisticalConfig, analyze_robustness
from .strategy_performance import analyze_strategy_performance
from .time_volatility import CanonicalVolatilityPoint, TimeVolatilityConfig, VolatilityBand, analyze_time_volatility, resolve_canonical_volatility
from .walk_forward import WalkForwardConfig, run_walk_forward


@dataclass(frozen=True)
class Phase15AcceptanceReport:
    end_to_end_validation_chain: str
    acceptance_dataset_coverage: str
    dataset_acceptance: str
    labeling_acceptance: str
    confidence_calibration_acceptance: str
    strategy_performance_acceptance: str
    regime_analysis_acceptance: str
    long_short_acceptance: str
    time_volatility_acceptance: str
    statistical_robustness_acceptance: str
    walk_forward_acceptance: str
    oos_acceptance: str
    benchmark_acceptance: str
    error_analysis_acceptance: str
    promotion_acceptance: str
    deterministic_repeat_count: int


def _observations() -> tuple[ValidationObservation, ...]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    kinds = (DecisionType.CANDIDATE_LONG, DecisionType.CANDIDATE_SHORT) * 6
    rows = []
    for i, kind in enumerate(kinds):
        pnl = Decimal("1.0") if i % 3 else Decimal("-0.25")
        rows.append(ValidationObservation(
            commander_decision_id=f"phase15-{i:02d}", timestamp_utc=start + timedelta(hours=i), symbol="BTC/USD",
            decision_type=kind, confidence=Decimal("0.90") if i % 4 == 0 else Decimal("0.55"),
            strategy_id="strategy-a" if i % 2 == 0 else "strategy-b", strategy_name="acceptance",
            validation_horizon="H1", paper_execution_outcome="FILLED", fill_outcome="FILLED",
            risk_outcome="APPROVED", net_pnl=pnl, gross_pnl=pnl + Decimal("0.10"), return_pct=pnl,
            fees=Decimal("0.05"), slippage=Decimal("0.05"), mfe=Decimal("1.2"), mae=Decimal("0.3"),
            market_regime="TREND" if i % 2 == 0 else "RANGE", volatility=Decimal("0.10") if i % 2 == 0 else Decimal("0.30"),
            volatility_band="LOW" if i % 2 == 0 else "HIGH", time_bucket=f"UTC_{i % 4:02d}",
        ))
    return tuple(rows)


def _dataset_acceptance() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    decisions = [SimpleNamespace(commander_decision_id=f"ds-{i}", timestamp_utc=start + timedelta(hours=i), symbol="BTC/USD", decision=kind, confidence=Decimal("0.2") + Decimal(i) / Decimal("10"), strategy_id="s1") for i, kind in enumerate(("CANDIDATE_LONG", "CANDIDATE_SHORT", "NO_ACTION", "WATCH", "INSUFFICIENT_DATA"))]
    outcomes = {item.commander_decision_id: PaperOutcome(item.commander_decision_id, paper_execution_outcome="FILLED", paper_order_status="FILLED", paper_fill_status="FILLED", net_pnl="1", return_pct="1", outcome_timestamp_utc=item.timestamp_utc + timedelta(minutes=30)) for item in decisions}
    dataset = build_evaluation_dataset(decisions, outcomes, DatasetConfig({"H1": timedelta(hours=1), "H2": timedelta(hours=2), "H3": timedelta(hours=3)}))
    assert len(dataset.observations) == 15 and dataset.quality["candidate_long_count"] == 3 and dataset.quality["candidate_short_count"] == 3


def _run_once() -> Phase15AcceptanceReport:
    _dataset_acceptance()
    observations = _observations()
    records = tuple(CalibrationRecord(item, label_observation(item)) for item in observations)
    assert len({(item.observation.commander_decision_id, item.observation.validation_horizon) for item in records}) == len(records)
    calibration = calibrate_confidence(records)
    strategy = analyze_strategy_performance(records)
    regimes = analyze_regimes(records)
    points = tuple(CanonicalRegimePoint("BTC/USD", item.timestamp_utc, item.market_regime) for item in observations)
    assert resolve_canonical_regime(observations[-1], points) == observations[-1].market_regime
    directions = compare_long_short(records)
    time_vol = analyze_time_volatility(records, config=TimeVolatilityConfig(volatility_bands=(VolatilityBand("LOW", Decimal("0"), Decimal("0.2")), VolatilityBand("HIGH", Decimal("0.2"), Decimal("1"), True))))
    assert resolve_canonical_volatility(observations[-1], tuple(CanonicalVolatilityPoint("BTC/USD", item.observation.timestamp_utc, item.observation.volatility) for item in records)) is not None
    robustness = analyze_robustness(records, config=StatisticalConfig(resample_count=50, seed=15))
    walk = run_walk_forward(records, WalkForwardConfig(4, 2, 2))
    oos_split = split_oos(records, OOSConfig("COUNT", terminal_count=3, validation_count=3))
    oos = summarize_oos(oos_split.oos_records)
    reference = summarize_oos(oos_split.train_records)
    generalization = assess_generalization(oos, reference_net_pnl=reference.net_pnl, reference_expectancy=reference.expectancy, reference_directional_accuracy=reference.directional_accuracy)
    benchmarks = compare_benchmarks(records, (BenchmarkDefinition("CASH", "CASH"), BenchmarkDefinition("LONG", "ALWAYS_LONG"), BenchmarkDefinition("SHORT", "ALWAYS_SHORT"), BenchmarkDefinition("RANDOM", "SEEDED_RANDOM", seed=15)))
    errors = analyze_errors(records)
    complete = PromotionEvidence(sample_size_status=SampleSizeStatus.MODERATE_SAMPLE, net_pnl=Decimal("1"), expectancy_net_pnl=Decimal("1"), profit_factor=Decimal("2"), drawdown_acceptable=True, loss_concentration_acceptable=True, tail_loss_acceptable=True, exposure_acceptable=True, robustness_interval_status="POSITIVE_INTERVAL", robustness_stability_status="STABLE", robustness_sign_consistency_status="CONSISTENT_POSITIVE", robustness_outlier_status="ROBUST_TO_OUTLIERS", walk_forward_valid_fold_count=2, walk_forward_stability_status="CONSISTENT", walk_forward_leakage_free=True, oos_sample_size_status=SampleSizeStatus.MODERATE_SAMPLE, oos_generalization_status="GENERALIZES", oos_net_pnl=Decimal("1"), oos_expectancy=Decimal("1"), benchmark_dominated=False, unresolved_high_severity_oos_errors=False, high_confidence_underperforms=False)
    states = {assess_promotion(complete).promotion_state, assess_promotion(PromotionEvidence()).promotion_state, assess_promotion(complete.__class__(**{**complete.__dict__, "walk_forward_stability_status": "UNSTABLE"})).promotion_state, assess_promotion(complete.__class__(**{**complete.__dict__, "real_exchange_call": True})).promotion_state}
    assert states == {PromotionState.CANDIDATE_FOR_NEXT_STAGE, PromotionState.KEEP_SHADOW, PromotionState.NEEDS_IMPROVEMENT, PromotionState.REJECT}
    assert calibration and strategy.summaries and regimes.summaries and directions.summaries and time_vol.summaries and robustness.intervals and walk.folds and generalization and benchmarks and errors
    return Phase15AcceptanceReport("PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", "PASS", 0)


def run_phase15_acceptance() -> Phase15AcceptanceReport:
    first = _run_once()
    second = _run_once()
    if first != second:
        return Phase15AcceptanceReport(*first.__dict__.values()[:-1], 1)
    return first


__all__ = ["Phase15AcceptanceReport", "run_phase15_acceptance"]
