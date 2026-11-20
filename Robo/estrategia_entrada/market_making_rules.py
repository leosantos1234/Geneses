"""Regras econômicas canônicas do Market Making.

Backtest e executor usam este módulo para manter spread, quantidade,
inventário e skew parametrizados de forma idêntica. Proteções de infraestrutura
continuam no executor.
"""

import os
from typing import Any, Dict, Iterable


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def canonical_config() -> Dict[str, float]:
    return {
        "order_qty": max(_float("MM_BACKTEST_ORDER_QTY", 0.001), 1e-12),
        "max_inventory_qty": max(_float("MM_BACKTEST_MAX_INVENTORY_QTY", 0.02), 1e-12),
        "stop_inventory_qty": max(_float("MM_BACKTEST_STOP_INVENTORY_QTY", 0.018), 1e-12),
        "min_spread_pct": max(_float("MM_BACKTEST_MIN_SPREAD_PCT", 0.0004), 0.0),
        "max_spread_pct": max(_float("MM_BACKTEST_MAX_SPREAD_PCT", 0.01), 0.0004),
        "spread_vol_mult": max(_float("MM_BACKTEST_SPREAD_VOL_MULT", 2.0), 0.0),
        "skew_strength": max(_float("MM_BACKTEST_SKEW_STRENGTH", 0.50), 0.0),
    }


def quote_from_closes(
    closes: Iterable[Any],
    midpoint: float,
    inventory: float,
    max_inventory_qty: float,
    cfg: Dict[str, float] | None = None,
) -> Dict[str, float]:
    """Calcula volatilidade, spread, skew, bid e ask de forma determinística."""
    import pandas as pd

    cfg = cfg or canonical_config()
    series = pd.to_numeric(pd.Series(closes), errors="coerce").dropna()
    returns = series.pct_change().iloc[-20:].dropna()
    volatility = float(returns.std()) if not returns.empty else 0.0
    spread_pct = max(
        cfg["min_spread_pct"],
        min(cfg["max_spread_pct"], volatility * cfg["spread_vol_mult"]),
    )
    max_inventory_qty = max(float(max_inventory_qty), 1e-12)
    skew = (float(inventory) / max_inventory_qty) * cfg["skew_strength"] * spread_pct
    midpoint = float(midpoint)
    return {
        "volatility": volatility,
        "spread_pct": spread_pct,
        "skew": skew,
        "bid": midpoint * (1.0 - spread_pct / 2.0 - skew),
        "ask": midpoint * (1.0 + spread_pct / 2.0 - skew),
    }
