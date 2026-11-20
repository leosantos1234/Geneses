"""Versioned, asset-specific calibration profiles.

Security and exchange authorization are deliberately excluded.  This module
only resolves strategy calibration values and returns an immutable snapshot
for one worker run.
"""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Any

from genesis_runtime import connect_operational_database


CALIBRATION_FIELDS = (
    "modo_validacao", "min_score_breakout_agressivo",
    "min_score_breakout_moderado", "min_score_breakout_conservador",
    "filtro_breakout_min_confirmacoes", "breakout_min_volume_ratio",
    "ema_curta", "ema_media", "ema_longa", "ema_trend", "sr_lookback",
    "breakout_level_lookback", "breakout_impulse_reset_min_bars",
    "breakout_impulse_atr_mult", "breakout_impulse_body_min",
)

_ENV_MAP = {
    "modo_validacao": ("MODO_VALIDACAO", "agressivo", str),
    "min_score_breakout_agressivo": ("MIN_SCORE_BREAKOUT_AGRESSIVO", 1, int),
    "min_score_breakout_moderado": ("MIN_SCORE_BREAKOUT_MODERADO", 2, int),
    "min_score_breakout_conservador": ("MIN_SCORE_BREAKOUT_CONSERVADOR", 3, int),
    "filtro_breakout_min_confirmacoes": ("FILTRO_BREAKOUT_MIN_CONFIRMACOES", 2, int),
    "breakout_min_volume_ratio": ("BREAKOUT_MIN_VOLUME_RATIO", 1.0, float),
    "ema_curta": ("EMA_CURTA", 20, int),
    "ema_media": ("EMA_MEDIA", 50, int),
    "ema_longa": ("EMA_LONGA", 200, int),
    "ema_trend": ("EMA_TREND", 200, int),
    "sr_lookback": ("SR_LOOKBACK", 14, int),
    "breakout_level_lookback": ("BREAKOUT_LEVEL_LOOKBACK", 5, int),
    "breakout_impulse_reset_min_bars": ("BREAKOUT_IMPULSE_RESET_MIN_BARS", 0, int),
    "breakout_impulse_atr_mult": ("BREAKOUT_IMPULSE_ATR_MULT", 1.5, float),
    "breakout_impulse_body_min": ("BREAKOUT_IMPULSE_BODY_MIN", 0.55, float),
}


def _env_snapshot() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field, (env_name, default, caster) in _ENV_MAP.items():
        raw = os.getenv(env_name)
        try:
            values[field] = caster(default if raw in (None, "") else raw)
        except (TypeError, ValueError):
            values[field] = default
    return values


def _validate(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    if result["modo_validacao"] not in {"agressivo", "moderado", "conservador"}:
        raise ValueError("modo_validacao invalido")
    for field in ("min_score_breakout_agressivo", "min_score_breakout_moderado",
                  "min_score_breakout_conservador", "filtro_breakout_min_confirmacoes",
                  "ema_curta", "ema_media", "ema_longa", "ema_trend", "sr_lookback",
                  "breakout_level_lookback"):
        if int(result[field]) < 1:
            raise ValueError(f"{field} deve ser >= 1")
    if float(result["breakout_min_volume_ratio"]) < 0:
        raise ValueError("breakout_min_volume_ratio deve ser >= 0")
    if int(result["breakout_impulse_reset_min_bars"]) < 0:
        raise ValueError("breakout_impulse_reset_min_bars deve ser >= 0")
    if float(result["breakout_impulse_atr_mult"]) <= 0 or float(result["breakout_impulse_body_min"]) < 0:
        raise ValueError("parametros de impulso invalidos")
    return result


def _with_identity(values: dict[str, Any], *, source: str, version: str) -> dict[str, Any]:
    clean = _validate({field: values[field] for field in CALIBRATION_FIELDS})
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    clean.update({"source": source, "version": version,
                  "config_hash": hashlib.sha256(canonical.encode()).hexdigest()})
    return clean


@lru_cache(maxsize=32)
def _load_database_profile(symbol: str, timeframe: str, profile_name: str) -> dict[str, Any] | None:
    try:
        with connect_operational_database(read_only=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT version, parameters FROM strategy_calibration_profiles
                       WHERE symbol=%s AND timeframe=%s AND profile_name=%s
                         AND enabled=TRUE
                         AND (effective_from IS NULL OR effective_from <= NOW())
                         AND (effective_until IS NULL OR effective_until > NOW())
                       ORDER BY version DESC LIMIT 1""",
                    (symbol, timeframe, profile_name),
                )
                row = cur.fetchone()
        if not row:
            return None
        parameters = row[1] if isinstance(row[1], dict) else json.loads(row[1])
        return _with_identity(parameters, source="DATABASE", version=str(row[0]))
    except Exception:
        # Existing deployments remain operational until migration/profile data
        # is installed.  The fallback is explicitly identifiable in telemetry.
        return None


def resolve_strategy_profile(symbol: str | None, timeframe: str = "5m") -> dict[str, Any]:
    """Resolve one immutable calibration snapshot; never reads live flags."""
    normalized_symbol = str(symbol or os.getenv("SYMBOL", "UNKNOWN")).upper()
    profile_name = str(os.getenv("MODO_VALIDACAO", "agressivo")).strip().lower()
    db_profile = _load_database_profile(normalized_symbol, str(timeframe), profile_name)
    if db_profile is not None:
        return db_profile
    return _with_identity(_env_snapshot(), source="ENV_FALLBACK", version="env")


def attach_strategy_profile(df: Any, profile: dict[str, Any]) -> Any:
    """Attach a copy-free snapshot to a pandas DataFrame for downstream calls."""
    if hasattr(df, "attrs"):
        df.attrs["strategy_profile"] = dict(profile)
    return df
