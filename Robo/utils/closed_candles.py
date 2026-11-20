"""Safe selection of completed exchange candles."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def select_closed_candles(
    df: pd.DataFrame,
    interval_minutes: int,
    *,
    now: datetime | None = None,
    timestamp_column: str = "time",
) -> tuple[pd.DataFrame, dict[str, Any]] | None:
    """Return rows whose open-time plus interval is not in the future.

    The active adapter normalizes Bybit/OKX candle timestamps to candle open
    time.  No exchange ``confirm`` flag is exposed by that adapter, so closure
    is proven from the open timestamp and the requested interval.  Ambiguous
    or insufficient data fails closed.
    """
    if df is None or df.empty or timestamp_column not in df.columns:
        return None
    try:
        interval = int(interval_minutes)
        if interval <= 0:
            return None
        work = df.copy()
        timestamps = pd.to_datetime(work[timestamp_column], errors="coerce", utc=True)
        if timestamps.isna().any():
            return None
        work[timestamp_column] = timestamps
        work = work.sort_values(timestamp_column).reset_index(drop=True)
        now_utc = _utc_naive(now or datetime.now(timezone.utc))
        close_times = work[timestamp_column].dt.tz_convert("UTC").dt.tz_localize(None) + timedelta(minutes=interval)
        closed = work.loc[close_times <= now_utc].copy()
        if closed.empty:
            return None
        last_open = closed[timestamp_column].iloc[-1].to_pydatetime()
        last_close = last_open + timedelta(minutes=interval)
        return closed, {
            "candle_open_time": last_open,
            "candle_close_time": last_close,
            "candle_closed": True,
            "timeframe_minutes": interval,
        }
    except (TypeError, ValueError, OverflowError):
        return None


__all__ = ["select_closed_candles"]
