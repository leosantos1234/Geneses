from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from utils.breakout_observability import BreakoutEvaluation
from utils.breakout_runtime_context import build_breakout_evaluation_kwargs
from utils.closed_candles import select_closed_candles


def _one_minute_frame():
    return pd.DataFrame(
        {
            "time": pd.to_datetime(
                ["2026-10-11 12:00:00+00:00", "2026-10-11 12:01:00+00:00"]
            ),
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.0, 100.0],
            "close": [100.5, 101.5],
            "volume": [10.0, 20.0],
        }
    )


def test_production_breakout_input_excludes_forming_1m_candle():
    selected = select_closed_candles(
        _one_minute_frame(),
        1,
        now=datetime(2026, 10, 11, 12, 1, 30, tzinfo=timezone.utc),
    )
    assert selected is not None
    closed, context = selected
    assert list(closed["time"]) == [pd.Timestamp("2026-10-11 12:00:00+00:00")]
    assert context["candle_open_time"] == datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    assert context["candle_close_time"] == datetime(2026, 10, 11, 12, 1, tzinfo=timezone.utc)
    assert context["candle_closed"] is True


def test_closed_1m_boundary_is_included_and_ohlcv_is_preserved():
    selected = select_closed_candles(
        _one_minute_frame(),
        1,
        now=datetime(2026, 10, 11, 12, 2, tzinfo=timezone.utc),
    )
    assert selected is not None
    closed, _ = selected
    row = closed.iloc[-1]
    assert row["time"] == pd.Timestamp("2026-10-11 12:01:00+00:00")
    assert row["open"] == 101.0
    assert row["high"] == 102.0
    assert row["low"] == 100.0
    assert row["close"] == 101.5
    assert row["volume"] == 20.0


def test_natural_lineage_keeps_breakout_1m_separate_from_canonical_regime_5m():
    kwargs = build_breakout_evaluation_kwargs(
        symbol="SOLUSDT",
        timeframe="5",
        regime=2,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME",
        run_id="worker:SOLUSDT:1",
        breakout_evaluation_id="evaluation-1",
        persist=False,
        natural_context=True,
        evaluation_candle={
            "symbol": "SOLUSDT",
            "timeframe": "5",
            "regime": 2,
            "record_provenance": "PRODUCTION_RUNTIME",
            "evaluation_context": "PRODUCTION_RUNTIME",
            "run_id": "worker:SOLUSDT:1",
            "candle_open_time": datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc),
            "candle_close_time": datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
            "candle_closed": True,
            "analysis_key": "SOLUSDT|5|2026-10-11T12:00:00+00:00|PRODUCTION_RUNTIME",
        },
        breakout_lineage={
            "breakout_timeframe": "1",
            "breakout_candle_open_time": datetime(2026, 10, 11, 12, 4, tzinfo=timezone.utc),
            "breakout_candle_close_time": datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
            "breakout_candle_closed": True,
            "canonical_regime_timeframe": "5",
            "canonical_regime_candle_open_time": datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc),
            "canonical_regime_candle_close_time": datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
        },
    )
    evaluation = BreakoutEvaluation(**kwargs)
    assert evaluation.timeframe == "5"
    assert evaluation.breakout_timeframe == "1"
    assert evaluation.breakout_candle_closed is True
    assert evaluation.canonical_regime_timeframe == "5"
    assert evaluation.breakout_candle_open_time != evaluation.candle_open_time


def test_natural_lineage_is_carried_into_persisted_observability_payload(monkeypatch):
    jobs = []
    monkeypatch.setattr("utils.breakout_observability._enqueue", lambda kind, data: jobs.append((kind, data)))
    evaluation = BreakoutEvaluation(
        symbol="SOLUSDT",
        timeframe="5",
        regime=2,
        record_provenance="PRODUCTION_RUNTIME",
        evaluation_context="PRODUCTION_RUNTIME",
        breakout_timeframe="1",
        breakout_candle_open_time=datetime(2026, 10, 11, 12, 4, tzinfo=timezone.utc),
        breakout_candle_close_time=datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
        breakout_candle_closed=True,
        canonical_regime_timeframe="5",
        canonical_regime_candle_open_time=datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc),
        canonical_regime_candle_close_time=datetime(2026, 10, 11, 12, 5, tzinfo=timezone.utc),
    )
    evaluation.finish("EVALUATED")
    payload = jobs[-1][1]
    assert payload["breakout_timeframe"] == "1"
    assert payload["breakout_candle_closed"] is True
    assert payload["canonical_regime_timeframe"] == "5"


def test_backtest_path_does_not_apply_production_closed_filter():
    source = Path(__file__).parents[1].joinpath(
        "estrategia_entrada", "estrategia_breakout_filtros.py"
    ).read_text(encoding="utf-8")
    natural_guard = source[source.index("if natural_runtime:"):source.index("backtest_run_id", source.index("if natural_runtime:"))]
    assert "select_closed_candles(df_original, 1)" in natural_guard
    assert "record_provenance" in natural_guard
