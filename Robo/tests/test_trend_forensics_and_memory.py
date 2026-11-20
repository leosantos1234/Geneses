from datetime import datetime, timedelta, timezone

import pandas as pd

from utils.trend_forensics import build_trend_forensics
from utils.analytical_telemetry import record_closed_1m_candle
from utils.breakout_market_outcomes import _metrics


def frame(short=10.0, long=11.0):
    return pd.DataFrame({"close": [10.0], "EMA50": [short], "EMA200": [long]})


def test_effective_sell_trend_relation_and_failure():
    result = build_trend_forensics(frame(12, 11), "SELL", state="FAIL", failure_reason="TREND_NOT_CONFIRMED")
    assert result["trend_ema_short_period"] == 50
    assert result["trend_ema_long_period"] == 200
    assert result["trend_ema_short_value"] == 12
    assert result["trend_ema_long_value"] == 11
    assert result["trend_condition_final"] is False
    assert result["trend_failure_reason"] == "TREND_NOT_CONFIRMED"


def test_effective_buy_trend_relation():
    result = build_trend_forensics(frame(12, 11), "BUY", state="PASS")
    assert result["trend_condition_final"] is True


def test_unavailable_trend_values_remain_null():
    result = build_trend_forensics(pd.DataFrame({"close": [10.0]}), "SELL", state="NOT_REACHED")
    assert result["trend_ema_short_value"] == 10.0
    assert result["trend_ema_long_value"] == 10.0
    assert result["trend_condition_final"] is None
    assert result["trend_ema_short_slope"] is None


def test_forming_1m_candle_is_rejected(monkeypatch):
    import utils.analytical_telemetry as telemetry
    queued = []
    monkeypatch.setattr(telemetry, "_jobs", type("Q", (), {"put": queued.append})())
    opened = datetime.now(timezone.utc)
    candle = {"candle_open_time": opened, "candle_close_time": opened + timedelta(minutes=1), "candle_closed": False}
    assert record_closed_1m_candle(symbol="SOLUSDT", candle=candle, evaluation_time=opened) is False
    assert queued == []


def test_direction_aware_mfe_mae():
    candles = [
        {"minutes_from_event": 5, "close": 101, "high": 103, "low": 99},
        {"minutes_from_event": 10, "close": 102, "high": 104, "low": 98},
    ]
    buy = _metrics(100, "BUY", candles)
    sell = _metrics(100, "SELL", candles)
    assert buy["mfe_5m"] == 3
    assert sell["mfe_5m"] == 1
    assert buy["forward_return_15m"] is None
