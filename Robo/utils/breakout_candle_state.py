"""Per-symbol last-candle state for the Breakout worker."""
from __future__ import annotations

import os
import re


# Historical residue only. New runtime state never reads this shared file.
LEGACY_SHARED_CANDLE_PATH = "ultimo_candle_breakout.txt"


def canonical_breakout_symbol(symbol=None) -> str:
    raw = str(symbol or os.getenv("SYMBOL") or os.getenv("ATIVOS") or os.getenv("PAR") or "UNKNOWN")
    canonical = re.sub(r"[^A-Z0-9]", "", raw.upper())
    return canonical or "UNKNOWN"


def candle_state_path(symbol=None) -> str:
    return f"ultimo_candle_breakout_{canonical_breakout_symbol(symbol)}.txt"


def get_last_candle(symbol=None):
    try:
        with open(candle_state_path(symbol), "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return None


def set_last_candle(value, symbol=None):
    with open(candle_state_path(symbol), "w", encoding="utf-8") as handle:
        handle.write(str(value))
