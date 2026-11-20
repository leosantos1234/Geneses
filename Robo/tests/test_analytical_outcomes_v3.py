from datetime import datetime, timedelta, timezone

from utils.analytical_outcomes import (
    build_outcome, calculate_horizon_metrics, classify_outcome,
    rag_outcome_context, segment_outcomes,
)


def candles(n=12, start=100.0):
    base = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    return [{"candle_open_time": base + timedelta(minutes=5 * (i + 1)),
             "candle_close_time": base + timedelta(minutes=5 * (i + 2)),
             "close": start + i, "high": start + i + 2, "low": start + i - 1}
            for i in range(n)]


def event(direction="BUY"):
    return {"event_type": "BREAKOUT", "event_id": "e1", "symbol": "SOLUSDT", "timeframe": "5",
            "event_candle_open_time": datetime(2026, 10, 11, 11, 55, tzinfo=timezone.utc),
            "event_candle_close_time": datetime(2026, 10, 11, 12, tzinfo=timezone.utc),
            "event_close": 100, "direction": direction, "regime": 2,
            "blocker": "PRICE_NOT_BROKEN", "feature_vector": {"atr_pct": 0.2}}


def test_buy_forward_return_and_excursions():
    result = calculate_horizon_metrics(100, "BUY", candles())
    assert result["forward_return_1"] == 0
    assert result["forward_return_3"] == 2
    assert result["mfe_pct_1"] == 2
    assert result["mae_pct_1"] == -1


def test_sell_forward_return_and_excursions():
    result = calculate_horizon_metrics(100, "SELL", candles())
    assert result["forward_return_1"] == 0
    assert result["forward_return_3"] == -2
    assert result["mfe_pct_1"] == 1
    assert result["mae_pct_1"] == -2


def test_unknown_direction_is_never_inferred():
    result = calculate_horizon_metrics(100, "UNKNOWN", candles())
    assert all(result[f"forward_return_{n}"] is None for n in (1, 3, 6, 12))
    assert classify_outcome(result, "UNKNOWN") == "INSUFFICIENT_DATA"


def test_horizon_availability_and_no_future_candle():
    result = calculate_horizon_metrics(100, "BUY", candles(3))
    assert result["forward_return_1"] is not None
    assert result["forward_return_3"] is not None
    assert result["forward_return_6"] is None
    assert calculate_horizon_metrics(100, "BUY", [])["forward_return_1"] is None


def test_no_post_horizon_leakage():
    result = calculate_horizon_metrics(100, "BUY", candles(12))
    assert result["forward_return_1"] == 0
    assert result["mfe_pct_1"] == 2
    assert result["forward_return_12"] == 11


def test_outcome_payload_is_temporally_explicit():
    payload = build_outcome(event(), candles(3))
    assert payload["forward_return_3"] is not None
    assert payload["forward_return_6"] is None
    assert payload["available_at"] == payload["forward_return_3_available_at"]


def test_classification_is_descriptive_only():
    assert classify_outcome({"forward_return_1": 1}, "BUY") == "MISSED_OPPORTUNITY"
    assert classify_outcome({"forward_return_1": -1}, "BUY") == "PROTECTED_CAPITAL"
    assert classify_outcome({"forward_return_1": 0}, "BUY") == "NEUTRAL"


def test_segments_have_quality_and_coverage():
    rows = [{"symbol": "SOLUSDT", "regime": 2, "direction": "BUY", "blocker": "PRICE_NOT_BROKEN",
             "forward_return_3": 1}]
    value = segment_outcomes(rows)["SOLUSDT|2|BUY|PRICE_NOT_BROKEN"]
    assert value["data_quality"] == "INSUFFICIENT"
    assert value["coverage_pct"] == 100


def test_rag_context_is_descriptive_for_small_samples():
    contexts = rag_outcome_context([{"symbol": "SOLUSDT", "regime": 2, "direction": "BUY",
                                    "blocker": "IMPULSE_RESET_WAIT", "forward_return_3": None}])
    assert contexts[0]["context_type"] == "blocker_performance"
    assert contexts[0]["descriptive_only"] is True


def test_duplicate_identity_is_stable():
    first = build_outcome(event(), candles(1))
    second = build_outcome(event(), candles(1))
    assert first["id"] == second["id"] == "BREAKOUT:e1"


def test_similarity_contract_excludes_outcomes():
    from genesis_rag.analytical_memory import similarity_vector
    from dataclasses import replace
    from genesis_rag.analytical_memory import AnalyticalTrade
    a = AnalyticalTrade(symbol="SOLUSDT", atr_pct=1, volume_ratio=2, breakout_score=3, impulse_reset_seen=True, r_multiple=99)
    b = replace(a, r_multiple=-99)
    assert similarity_vector(a) == similarity_vector(b)


def test_commander_authority_is_not_part_of_outcome_model():
    payload = build_outcome(event(), candles(1))
    assert "commander_execution_authority" not in payload


def test_outcome_updater_requires_explicit_prospective_watermark():
    from utils.analytical_outcomes import update_natural_outcomes
    try:
        update_natural_outcomes()  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("historical backfill must not be implicit")


def test_outcome_failure_is_non_blocking(monkeypatch):
    from utils import analytical_telemetry

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, *args): pass

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def cursor(self): return Cursor()
        def commit(self): pass

    monkeypatch.setattr(analytical_telemetry, "connect_operational_database", lambda: Connection())
    monkeypatch.setattr(
        "utils.analytical_outcomes.update_natural_outcomes_from_activation",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("analytics unavailable")),
    )
    analytical_telemetry._write({
        "symbol": "SOLUSDT", "timeframe": "5", "candle_open_time": datetime.now(timezone.utc),
        "candle_close_time": datetime.now(timezone.utc), "open": 1, "high": 1,
        "low": 1, "close": 1, "volume": 1, "atr": 1, "atr_percent": 1,
        "volume_ratio": 1, "body_fraction": 0, "regime": 2,
        "winning_rule": "TEST", "candle_closed": True,
        "record_provenance": "PRODUCTION_RUNTIME", "feature_vector": {},
        "created_at": datetime.now(timezone.utc),
    })
