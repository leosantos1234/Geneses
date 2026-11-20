import json
import os
import threading
from datetime import datetime, timezone


_LOCK = threading.Lock()
_STATE_FILE = os.getenv("ENTRY_IDEMPOTENCY_FILE", "entry_idempotency_state.json")


def _ts_to_key(candle_ts) -> str:
    if isinstance(candle_ts, datetime):
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)
        return candle_ts.isoformat()
    return str(candle_ts)


def _load_state() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {}
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(data: dict) -> None:
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_exec_key(symbol: str, timeframe: str, candle_ts, side: str) -> str:
    return f"{symbol.upper()}|{timeframe}|{_ts_to_key(candle_ts)}|{side.upper()}"


def is_registered(symbol: str, timeframe: str, candle_ts, side: str) -> tuple[bool, str]:
    """
    Returns (already_registered, key) without mutating state.
    """
    key = make_exec_key(symbol, timeframe, candle_ts, side)
    with _LOCK:
        state = _load_state()
        return key in state, key


def mark_registered(symbol: str, timeframe: str, candle_ts, side: str) -> tuple[bool, str]:
    """
    Returns (is_new, key).
    is_new=False means key was already present.
    """
    key = make_exec_key(symbol, timeframe, candle_ts, side)
    with _LOCK:
        state = _load_state()
        if key in state:
            return False, key
        state[key] = {"created_at": datetime.now(timezone.utc).isoformat()}
        _save_state(state)
    return True, key


def register_once(symbol: str, timeframe: str, candle_ts, side: str) -> tuple[bool, str]:
    """
    Returns (is_new, key).
    is_new=False means this candle/side/timeframe was already executed.
    """
    return mark_registered(symbol, timeframe, candle_ts, side)
