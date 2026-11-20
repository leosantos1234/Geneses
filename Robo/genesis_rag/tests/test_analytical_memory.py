from datetime import datetime, timezone

from genesis_rag.analytical_memory import (
    AnalyticalTrade,
    analytical_context,
    calculate_excursions,
    calculate_r_multiple,
    deterministic_setup_key,
    empirical_boundaries,
    forward_returns,
    rank_similar_cases,
    sample_quality,
    summarize,
    volatility_bucket,
)
from genesis_rag.rag_v1 import rank_analytical_context


def trade(**kwargs):
    defaults = {"symbol": "SOLUSDT", "strategy": "BREAKOUT", "side": "BUY", "regime": "2"}
    defaults.update(kwargs)
    return AnalyticalTrade(**defaults)


def test_r_long_and_short():
    assert calculate_r_multiple("BUY", 100, 95, 110) == 2.0
    assert calculate_r_multiple("SELL", 100, 105, 90) == 2.0


def test_invalid_stop_returns_null():
    assert calculate_r_multiple("BUY", 100, 100, 110) is None
    assert calculate_r_multiple("BUY", 100, None, 110) is None


def test_mae_mfe_long_and_short():
    candles = [{"high": 110, "low": 95}, {"high": 108, "low": 98}]
    assert calculate_excursions("BUY", 100, candles) == {"mae": 5, "mae_pct": 5, "mfe": 10, "mfe_pct": 10}
    assert calculate_excursions("SELL", 100, candles) == {"mae": 10, "mae_pct": 10, "mfe": 5, "mfe_pct": 5}


def test_forward_returns_use_only_supplied_closed_candles():
    rows = [{"close": 101}, {"close": 102}, {"close": 103}]
    result = forward_returns(rows, 100, "BUY")
    assert result["forward_return_1"] == 1
    assert result["forward_return_3"] == 3
    assert result["forward_return_6"] is None


def test_summary_and_sample_quality_include_sample_size():
    result = summarize([trade(pnl=1), trade(pnl=-0.5)])
    assert result["trade_count"] == 2
    assert result["wins"] == 1
    assert result["sample_quality"] == "INSUFFICIENT"
    assert sample_quality(20) == "LOW"
    assert sample_quality(50) == "MEDIUM"
    assert sample_quality(100) == "HIGH"


def test_setup_key_is_deterministic_and_bounded():
    row = trade(breakout_score=5, volatility_bucket="HIGH")
    assert deterministic_setup_key(row) == deterministic_setup_key(row)
    assert deterministic_setup_key(row).startswith("BREAKOUT|BUY|R2|")


def test_volatility_boundaries_are_empirical():
    boundaries = empirical_boundaries([1, 2, 3, 4, 5])
    assert boundaries == (1.8, 2.6, 3.4, 4.2)
    assert volatility_bucket(1.0, boundaries) == "VERY_LOW"
    assert volatility_bucket(5.0, boundaries) == "VERY_HIGH"


def test_similar_cases_rank_features_without_outcome_fields():
    candidate = trade(atr_pct=2, volume_ratio=1.2, breakout_score=5, impulse_reset_seen=True)
    close = trade(atr_pct=2, volume_ratio=1.2, breakout_score=5, impulse_reset_seen=True, r_multiple=-99, pnl=-999)
    far = trade(atr_pct=10, volume_ratio=10, breakout_score=1, impulse_reset_seen=False, r_multiple=99, pnl=999)
    result = rank_similar_cases(candidate, [far, close], limit=2)
    assert result[0]["r_multiple"] == -99
    assert "pnl" not in result[0]


def test_future_outcomes_are_excluded_by_as_of():
    cutoff = datetime(2026, 10, 11, tzinfo=timezone.utc)
    past = trade(entry_timestamp=datetime(2026, 10, 10, tzinfo=timezone.utc), pnl=1)
    future = trade(entry_timestamp=datetime(2026, 10, 12, tzinfo=timezone.utc), pnl=99)
    result = rank_similar_cases(trade(), [past, future], as_of=cutoff)
    assert len(result) == 1
    context = analytical_context([past, future], as_of=cutoff)
    assert context["sample_size"] == 1


def test_analytical_context_groups_regime_strategy_and_setup():
    result = analytical_context([trade(pnl=1), trade(regime="3", pnl=-1)])
    assert set(result["regime_performance"]) == {"2", "3"}
    assert result["strategy_performance"]["BREAKOUT"]["trade_count"] == 2


def test_analytical_rag_context_priority_precedes_recency():
    rows = [
        {"source_type": "decision_trace", "timestamp_utc": "2026-10-11T13:00:00+00:00", "source_id": "old"},
        {"source_type": "similar_setup_context", "timestamp_utc": "2026-10-10T13:00:00+00:00", "source_id": "similar"},
    ]
    assert rank_analytical_context(rows)[0]["source_type"] == "similar_setup_context"
