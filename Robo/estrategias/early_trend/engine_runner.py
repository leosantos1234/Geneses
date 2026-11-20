from __future__ import annotations

import logging
from typing import Optional, Tuple

import pandas as pd

from .early_trend_engine import EarlyTrendEngine, EarlyTrendTickResult

logger = logging.getLogger(__name__)

_EARLY_TREND_ENGINES: dict[str, EarlyTrendEngine] = {}


def executar_early_trend_tick(
    *,
    session,
    symbol: str,
    df: pd.DataFrame,
    interval: str,
    qty: float,
    regime_override: int | None = None,
) -> EarlyTrendTickResult:
    key = (symbol or "").upper()
    engine = _EARLY_TREND_ENGINES.get(key)
    if engine is None:
        engine = EarlyTrendEngine(session=session, symbol=key)
        _EARLY_TREND_ENGINES[key] = engine
        logger.info(f"[EARLYTREND] Engine criado | symbol={key}")

    return engine.on_tick(df=df, interval=interval, qty=qty, regime_override=regime_override)


def executar_early_trend_tick_as_tuple(
    *,
    session,
    symbol: str,
    df: pd.DataFrame,
    interval: str,
    qty: float,
    regime_override: int | None = None,
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Adaptador para o contrato legado do trading_bot_geneses.py:
    retorna (trade_id, preco_entrada, tamanho) quando houver FILL.
    """
    res = executar_early_trend_tick(
        session=session,
        symbol=symbol,
        df=df,
        interval=interval,
        qty=qty,
        regime_override=regime_override,
    )
    if res.status != "FILLED":
        return None, None, None
    return res.order_id, res.fill_price, res.qty
