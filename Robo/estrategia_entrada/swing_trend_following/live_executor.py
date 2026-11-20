"""Executor live do swing para a OKX, sempre protegido por fail-closed.

O módulo só pode enviar ordem quando o worker estiver em produção, o executor
estiver explicitamente habilitado e a quantidade tiver sido configurada. A
camada ``GuardedExchangeProxy`` continua sendo a autoridade final de escrita.
"""

from __future__ import annotations

import logging
import os

from .strategy import (
    avaliar_setup_entrada_1h,
    avaliar_setup,
    classificar_regime_swing,
)

logger = logging.getLogger(__name__)


def _enabled(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on", "sim"}


def _float_env(name: str, default: float = 0.0) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _qty_from_balance_percent(session, symbol: str, entry_price: float, prefix: str = "SWING") -> float:
    prefix = str(prefix).strip().upper()
    percent = _float_env(f"{prefix}_LIVE_BALANCE_PCT", 0.0)
    if percent <= 0:
        return 0.0
    if percent > 100:
        logger.error("[SWING][OKX] Percentual de saldo inválido: %.4f", percent)
        return 0.0
    balance = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
    accounts = balance.get("result", {}).get("list", []) if isinstance(balance, dict) else []
    available = 0.0
    for account in accounts:
        for coin in account.get("coin", []) or []:
            if str(coin.get("coin") or "").upper() == "USDT":
                available = float(coin.get("totalAvailableBalance") or coin.get("availableToWithdraw") or 0.0)
                break
        if available > 0:
            break
    if available <= 0:
        logger.error("[SWING][OKX] Saldo USDT disponível não encontrado.")
        return 0.0

    max_notional = _float_env(f"{prefix}_LIVE_MAX_NOTIONAL_USDT", 0.0)
    notional = available * (percent / 100.0)
    if max_notional > 0:
        notional = min(notional, max_notional)
    if entry_price <= 0 or notional <= 0:
        return 0.0

    info = session.get_instruments_info(category="linear", symbol=symbol)
    rows = info.get("result", {}).get("list", []) if isinstance(info, dict) else []
    item = rows[0] if rows else {}
    contract_size = float(item.get("contractSize") or 0.0)
    lot_size = float(item.get("lotSizeFilter", {}).get("qtyStep") or 1.0)
    min_size = float(item.get("lotSizeFilter", {}).get("minOrderQty") or lot_size)
    if contract_size <= 0 or lot_size <= 0:
        logger.error("[SWING][OKX] Metadados de contrato inválidos para %s.", symbol)
        return 0.0
    contracts = notional / (entry_price * contract_size)
    contracts = (contracts // lot_size) * lot_size
    if contracts < min_size:
        minimum_notional = min_size * entry_price * contract_size
        minimum_percent = (minimum_notional / available) * 100.0 if available > 0 else 0.0
        complete_to_minimum = _enabled(f"{prefix}_LIVE_COMPLETE_TO_MINIMUM", "1")
        if complete_to_minimum and available >= minimum_notional and (
            max_notional <= 0 or minimum_notional <= max_notional
        ):
            logger.warning(
                "[SWING][OKX] Percentual abaixo do minimo; completando para o minimo da corretora | "
                "saldo=%.2f pct=%.4f notional_calculado=%.2f min_qty=%.8f "
                "min_notional=%.2f percentual_minimo=%.4f%%",
                available,
                percent,
                notional,
                min_size,
                minimum_notional,
                minimum_percent,
            )
            return float(min_size)
        logger.warning(
            "[SWING][OKX] Percentual abaixo do mínimo | saldo=%.2f pct=%.4f notional=%.2f "
            "min_qty=%.8f percentual_minimo=%.4f%%",
            available,
            percent,
            notional,
            min_size,
            minimum_percent,
        )
        return 0.0
    return float(contracts)


def executar_swing_okx(*, session, symbol: str, timeframes: dict, entry_timeframe: str = "1h"):
    """Avalia o setup atual e envia uma ordem somente sob autorização explícita.

    Retorna ``(order_id, entry_price, qty)`` quando a entrada é confirmada;
    retorna ``None`` em qualquer bloqueio ou ausência de setup.
    """
    if not _enabled("SWING_LIVE_EXECUTOR_ENABLED"):
        logger.info("[SWING][OKX] Executor live desabilitado por configuração.")
        return None
    if not _enabled("ENABLE_LIVE_TRADING") or not _enabled("ALLOW_EXCHANGE_WRITE"):
        logger.warning("[SWING][OKX] Execução bloqueada: modo live/write não autorizado.")
        return None

    one = timeframes.get("1h")
    four = timeframes.get("4h")
    daily = timeframes.get("1d")
    if one is None or four is None or daily is None:
        logger.warning("[SWING][OKX] Timeframes ausentes; nenhuma ordem será enviada.")
        return None

    regime = classificar_regime_swing(four, daily)
    if regime not in {1, 2}:
        logger.info("[SWING][OKX] Entrada bloqueada pelo regime: %s", regime)
        return None

    if str(entry_timeframe).strip().lower() == "1h":
        signal = avaliar_setup_entrada_1h(one, four, daily, regime=regime)
    else:
        signal = avaliar_setup(four, daily, regime=regime)
    if not signal.ok or signal.side not in {"buy", "sell"}:
        logger.info("[SWING][OKX] Sem setup válido: %s", signal.reason)
        return None

    qty = _float_env("SWING_LIVE_QTY", 0.0)
    if qty <= 0:
        # O percentual é convertido para contratos inteiros conforme o
        # instrumento OKX; nunca envia uma quantidade fracionária implícita.
        qty = _qty_from_balance_percent(session, symbol, float(signal.entry or 0.0))
    if qty <= 0:
        logger.error("[SWING][OKX] Execução bloqueada: quantidade fixa/percentual inválida.")
        return None

    max_notional = _float_env("SWING_LIVE_MAX_NOTIONAL_USDT", 0.0)
    notional = qty * float(signal.entry or 0.0)
    if max_notional > 0 and notional > max_notional:
        logger.error(
            "[SWING][OKX] Quantidade bloqueada pelo limite notional | notional=%.8f limite=%.8f",
            notional,
            max_notional,
        )
        return None

    from estrategia_entrada.estrategia_breakout_filtros import executar_ordem_fluxo

    result = executar_ordem_fluxo(
        session,
        session,
        symbol,
        signal.side,
        float(signal.entry),
        qty,
        float(signal.stop_loss),
        float(signal.take_profit),
        float(signal.trailing_distance),
        entry_mode=os.getenv("SWING_LIVE_ENTRY_MODE", "Market"),
        entry_score=float(signal.score or 0.0),
        indicadores_df=None,
    )
    if not result:
        logger.error("[SWING][OKX] Executor não confirmou a ordem.")
        return None
    order_id = result if isinstance(result, str) else result.get("trade_id") or result.get("orderId")
    logger.info(
        "[SWING][OKX] Entrada confirmada | order_id=%s | side=%s | entry=%.8f | qty=%.8f | SL=%.8f | TP=%.8f | trail=%.8f",
        order_id,
        signal.side,
        signal.entry,
        qty,
        signal.stop_loss,
        signal.take_profit,
        signal.trailing_distance,
    )
    return order_id, float(signal.entry), qty
