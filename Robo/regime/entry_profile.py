"""Perfis centrais de entrada por regime de mercado."""

from copy import deepcopy


ENTRY_PROFILES = {
    1: {"breakout_enabled": True, "breakout_min_score": 1, "breakout_confirmations": 2, "description": "tendencia_forte"},
    2: {"breakout_enabled": True, "breakout_min_score": 2, "breakout_confirmations": 2, "description": "tendencia_moderada"},
    3: {"breakout_enabled": False, "breakout_min_score": 999, "breakout_confirmations": 0, "description": "lateralidade"},
    4: {"breakout_enabled": False, "breakout_min_score": 999, "breakout_confirmations": 0, "description": "caos"},
}

STRATEGY_REGIMES = {
    "BREAKOUT": {1, 2},
    "MEAN_REVERSION": {1, 2},
    "MARKET_MAKING_LATERAL": {3},
    "EARLY_TREND": {2},
    "SWING_TREND_FOLLOWING": {1, 2},
    "PULLBACK_TREND_FOLLOWING": {1, 2},
}


def get_entry_profile(regime: int) -> dict:
    """Retorna uma cópia segura do perfil correspondente ao regime."""
    return deepcopy(ENTRY_PROFILES.get(int(regime), ENTRY_PROFILES[4]))


def get_strategy_profile(strategy: str) -> dict:
    """Retorna o perfil de roteamento da estratégia."""
    name = str(strategy or "").strip().upper()
    regimes = STRATEGY_REGIMES.get(name, set())
    return {"strategy": name, "allowed_regimes": set(regimes), "enabled": bool(regimes)}


def strategy_allowed(strategy: str, regime: int) -> bool:
    """Indica se a estratégia pode ser considerada no regime informado."""
    return int(regime) in get_strategy_profile(strategy)["allowed_regimes"]


def breakout_only_enabled(value=None) -> bool:
    """Return whether runtime is restricted to Breakout."""
    import os

    raw = os.getenv("BREAKOUT_ONLY", "0") if value is None else value
    return str(raw).strip().lower() in {"1", "true", "yes"}


def runtime_strategy_for_regime(regime: int, breakout_only=None):
    """Resolve the eligible runtime strategy before strategy routing."""
    regime = int(regime)
    if breakout_only_enabled(breakout_only):
        return "BREAKOUT" if regime in {1, 2} else None
    if regime == 3:
        return "MARKET_MAKING_LATERAL"
    if regime == 4:
        return None
    return "BREAKOUT" if regime in {1, 2} else None
