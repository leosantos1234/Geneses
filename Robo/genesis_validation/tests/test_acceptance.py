import sys

sys.path.insert(0, "Robo")

from genesis_validation.acceptance import run_phase15_acceptance


def test_phase15_integrated_acceptance_is_deterministic_and_green():
    report = run_phase15_acceptance()
    assert report.end_to_end_validation_chain == "PASS"
    assert report.acceptance_dataset_coverage == "PASS"
    assert report.dataset_acceptance == "PASS"
    assert report.labeling_acceptance == "PASS"
    assert report.confidence_calibration_acceptance == "PASS"
    assert report.strategy_performance_acceptance == "PASS"
    assert report.regime_analysis_acceptance == "PASS"
    assert report.long_short_acceptance == "PASS"
    assert report.time_volatility_acceptance == "PASS"
    assert report.statistical_robustness_acceptance == "PASS"
    assert report.walk_forward_acceptance == "PASS"
    assert report.oos_acceptance == "PASS"
    assert report.benchmark_acceptance == "PASS"
    assert report.error_analysis_acceptance == "PASS"
    assert report.promotion_acceptance == "PASS"
    assert report.deterministic_repeat_count == 0
