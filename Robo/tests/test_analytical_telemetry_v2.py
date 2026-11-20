from datetime import datetime, timezone

import numpy as np
import pandas as pd

from utils.analytical_telemetry import normalize_db_value, normalize_json_value, record_canonical_candle
from utils.breakout_observability import BreakoutEvaluation


def test_natural_candle_telemetry_is_closed_and_production(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.analytical_telemetry._jobs", type("Q", (), {
        "put": lambda self, item: jobs.append(item), "unfinished_tasks": 0,
    })())
    monkeypatch.setattr("utils.analytical_telemetry._worker", object())
    record_canonical_candle(
        symbol="SOLUSDT", timeframe="5",
        candle={"candle_open_time": datetime(2026, 10, 11, 12, tzinfo=timezone.utc),
                "candle_close_time": datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
                "candle_closed": True, "open": 100, "high": 101, "low": 99,
                "close": 100.5, "volume": 10},
        diagnostics={"atr": 1, "atr_percent": 1, "volume_ratio": 1.2,
                     "body_fraction": .5, "winning_rule": "REGIME2_EXPLICIT_RULE"},
        regime=2,
    )
    assert jobs[0]["record_provenance"] == "PRODUCTION_RUNTIME"
    assert jobs[0]["candle_closed"] is True
    assert jobs[0]["feature_vector"]["atr_percent"] == 1


def test_breakout_unresolved_direction_is_explicit_unknown(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    evaluation = BreakoutEvaluation(
        symbol="SOLUSDT", timeframe="5", regime=2,
        record_provenance="PRODUCTION_RUNTIME", evaluation_context="PRODUCTION_RUNTIME",
        candle_closed=True,
    )
    evaluation.finish("EVALUATED")
    assert jobs[-1][1]["direction_candidate"] == "UNKNOWN"


def test_db_scalar_normalization_preserves_zero_and_nulls_nonfinite_values():
    assert normalize_db_value(np.float64(1.25)) == 1.25
    assert normalize_db_value(np.int64(7)) == 7
    assert normalize_db_value(np.bool_(True)) is True
    assert normalize_db_value(pd.Timestamp("2026-10-11 17:00:00+00:00")).tzinfo is not None
    assert normalize_db_value(pd.NaT) is None
    assert normalize_db_value(float("nan")) is None
    assert normalize_db_value(float("inf")) is None
    assert normalize_db_value(-float("inf")) is None
    assert normalize_db_value(0.0) == 0.0


def test_db_payload_normalization_is_recursive_and_does_not_invent_values():
    value = normalize_db_value({"open": np.float64(100), "atr": np.nan, "missing": None})
    assert value == {"open": 100.0, "atr": None, "missing": None}


def test_feature_vector_json_roundtrip_handles_nested_scalars():
    import json
    payload = {
        "direction": "UNKNOWN", "regime": 2, "score": 0.0, "enabled": True,
        "missing": None, "float": np.float64(1.25), "integer": np.int64(4),
        "boolean": np.bool_(False), "timestamp": pd.Timestamp("2026-10-11T17:00:00Z"),
        "nan": np.nan, "nested": {"values": [np.float64(0.0), np.inf, None]},
    }
    encoded = json.dumps(normalize_json_value(payload), separators=(",", ":"))
    decoded = json.loads(encoded)
    assert decoded["float"] == 1.25
    assert decoded["integer"] == 4
    assert decoded["boolean"] is False
    assert decoded["timestamp"] == "2026-10-11T17:00:00+00:00"
    assert decoded["nan"] is None
    assert decoded["nested"]["values"] == [0.0, None, None]
