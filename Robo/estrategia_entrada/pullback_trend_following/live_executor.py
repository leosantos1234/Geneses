"""Executor protegido do Pullback Trend Following."""

from __future__ import annotations

import logging
import os

from .strategy import avaliar_setup_entrada_1h, avaliar_setup_entrada_4h

logger = logging.getLogger(__name__)


def _enabled(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in {"1", "true", "yes", "on", "sim"}


def executar_pullback_okx(*, session, symbol: str, timeframes: dict):
    if not _enabled("PULLBACK_LIVE_EXECUTOR_ENABLED"):
        logger.info("[PULLBACK][OKX] Executor live desabilitado por configuração.")
        return None
    if not (_enabled("ENABLE_LIVE_TRADING") and _enabled("ALLOW_EXCHANGE_WRITE")):
        logger.warning("[PULLBACK][OKX] Execução bloqueada: modo live/write não autorizado.")
        return None
    one, four, daily = timeframes.get("1h"), timeframes.get("4h"), timeframes.get("1d")
    if one is None or four is None or daily is None:
        logger.warning("[PULLBACK][OKX] Timeframes ausentes; nenhuma ordem será enviada.")
        return None
    from estrategia_entrada.swing_trend_following.strategy import classificar_regime_swing
    regime = classificar_regime_swing(four, daily)
    # O backtest de referência usa entrada em 1h; 4h/diário são contexto.
    # Manter 1h como fallback evita perder a paridade se o .env não carregar.
    entry_timeframe = os.getenv("PULLBACK_ENTRY_TIMEFRAME", "1h").strip().lower()
    signal = avaliar_setup_entrada_4h(four, daily, regime=regime) if entry_timeframe == "4h" else avaliar_setup_entrada_1h(one, four, daily, regime=regime)
    if not signal.ok or signal.side not in {"buy", "sell"}:
        logger.info("[PULLBACK][OKX] Sem setup válido: %s", signal.reason)
        return None
    try:
        qty = float(os.getenv("PULLBACK_LIVE_QTY", "0"))
    except (TypeError, ValueError):
        qty = 0.0
    if qty <= 0:
        from estrategia_entrada.swing_trend_following.live_executor import _qty_from_balance_percent
        qty = _qty_from_balance_percent(session, symbol, float(signal.entry or 0.0), prefix="PULLBACK")
    if qty > 0:
        pass
    else:
        logger.error("[PULLBACK][OKX] Execução bloqueada: PULLBACK_LIVE_QTY inválido.")
        return None
    from estrategia_entrada.estrategia_breakout_filtros import executar_ordem_fluxo
    result = executar_ordem_fluxo(session, session, symbol, signal.side, float(signal.entry), qty,
                                  float(signal.stop_loss), float(signal.take_profit),
                                  float(signal.trailing_distance),
                                  entry_mode=os.getenv("PULLBACK_LIVE_ENTRY_MODE", "Market"),
                                  entry_score=float(signal.score or 0.0), indicadores_df=None)
    if not result:
        return None
    order_id = result if isinstance(result, str) else result.get("trade_id") or result.get("orderId")
    logger.info("[PULLBACK][OKX] Entrada confirmada | order_id=%s | side=%s", order_id, signal.side)
    return order_id, float(signal.entry), qty
