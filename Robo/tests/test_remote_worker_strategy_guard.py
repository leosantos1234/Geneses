"""Regression tests for explicit remote worker strategy selection."""

from render_worker import _strategy_from_state


def test_missing_strategy_never_defaults_to_swing():
    assert _strategy_from_state("AVAX", {"running": True}) is None


def test_supported_strategy_is_normalized():
    assert _strategy_from_state("AVAX", {"running": True, "strategy": "Pullback Trend Following"}) == "PULLBACK_TREND_FOLLOWING"


def test_invalid_strategy_is_rejected():
    assert _strategy_from_state("AVAX", {"running": True, "strategy": "UNKNOWN"}) is None
