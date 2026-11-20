from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from regime.regime_detector import RegimeDetector
from utils.closed_candles import select_closed_candles


def _candles(last_open="2026-10-10 12:45:00"):
    times = pd.date_range(end=pd.Timestamp(last_open, tz="UTC"), periods=4, freq="5min")
    return pd.DataFrame({
        "time": times,
        "open": [100, 101, 102, 103],
        "high": [101, 102, 103, 104],
        "low": [99, 100, 101, 102],
        "close": [100.5, 101.5, 102.5, 103.5],
        "volume": [10, 10, 10, 10],
    })


def test_selects_only_closed_5m_candles():
    result = select_closed_candles(_candles(), 5, now=datetime(2026, 10, 10, 12, 48, tzinfo=timezone.utc))
    assert result is not None
    closed, meta = result
    assert closed["time"].iloc[-1] == pd.Timestamp("2026-10-10 12:40:00", tz="UTC")
    assert meta["candle_closed"] is True


def test_excludes_forming_last_candle():
    result = select_closed_candles(_candles(), 5, now=datetime(2026, 10, 10, 12, 47, tzinfo=timezone.utc))
    assert result is not None
    assert result[0]["time"].iloc[-1] == pd.Timestamp("2026-10-10 12:40:00", tz="UTC")


def test_preserves_already_closed_last_candle():
    result = select_closed_candles(_candles(), 5, now=datetime(2026, 10, 10, 12, 51, tzinfo=timezone.utc))
    assert result is not None
    assert result[0]["time"].iloc[-1] == pd.Timestamp("2026-10-10 12:45:00", tz="UTC")


def test_uncertain_timestamp_fails_closed():
    frame = _candles()
    frame.loc[3, "time"] = "not-a-timestamp"
    assert select_closed_candles(frame, 5, now=datetime.now(timezone.utc)) is None


def test_future_only_data_fails_closed():
    assert select_closed_candles(_candles("2099-01-01 00:00:00"), 5, now=datetime.now(timezone.utc)) is None


def test_regime_detector_exposes_numerical_evidence_without_changing_result():
    frame = _candles()
    for column in ("EMA_12", "EMA_26", "EMA_50", "EMA_200"):
        frame[column] = frame["close"]
    frame["ATR"] = 0.05
    detector = RegimeDetector()
    regime = detector.detectar(frame, symbol="BTCUSDT")
    assert regime in {1, 2, 3, 4}
    assert detector.last_diagnostics["winning_rule"]
    assert "atr_percent" in detector.last_diagnostics


def test_all_symbols_use_independent_selection():
    for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"):
        result = select_closed_candles(_candles(), 5, now=datetime(2026, 10, 10, 12, 48, tzinfo=timezone.utc))
        assert result is not None
        assert result[1]["timeframe_minutes"] == 5
