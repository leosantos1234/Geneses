import logging
import os
import time
from datetime import datetime
from typing import Optional, Tuple

from regime.regime_detector import RegimeDetector
from utils.indicadores import calculate_indicators
from execution.execution_gate import executar_ordem_com_validacao

from .early_trend_core import detectar_entrada_early_trend, _get_bool_env, _get_float_env, _get_int_env
from utils.utils_telegram import send_telegram_message

logger = logging.getLogger(__name__)


def _notify_telegram(message: str) -> None:
    try:
        send_telegram_message(message)
    except Exception as e:
        logger.warning("[EARLYTREND] Falha ao enviar Telegram: %s", e)


def _round_step(valor: float, step: float) -> float:
    if step <= 0:
        return valor
    casas = len(str(step).split(".")[-1].rstrip("0"))
    return round(round(valor / step) * step, casas)


def _obter_precisao(api, symbol: str) -> tuple[float, float, float]:
    tick_size, qty_step, min_qty = 0.01, 0.001, 0.001
    try:
        info = api.get_instruments_info(category="linear", symbol=symbol)
        item = info.get("result", {}).get("list", [{}])[0]
        tick_size = float(item.get("priceFilter", {}).get("tickSize", tick_size))
        qty_step = float(item.get("lotSizeFilter", {}).get("qtyStep", qty_step))
        min_qty = float(item.get("lotSizeFilter", {}).get("minOrderQty", min_qty))
    except Exception as e:
        logger.warning(f"[EARLYTREND] N??o foi poss??vel obter precis??o de {symbol}: {e}")
    return tick_size, qty_step, min_qty



def _enviar_stop_limit_confirmacao(api, symbol: str, side: str, qty: float, preco_confirmacao: float, stop_loss: float, take_profit: Optional[float]) -> Optional[str]:
    tick_size, qty_step, min_qty = _obter_precisao(api, symbol)
    preco_ajustado = _round_step(preco_confirmacao, tick_size)
    qty_ajustada = _round_step(qty, qty_step)
    if qty_ajustada < min_qty:
        logger.warning("[EARLYTREND] Qty abaixo do minimo | qty=%.8f min=%.8f", qty_ajustada, min_qty)
        qty_ajustada = min_qty
        logger.info("[EARLYTREND] Precision | tick=%.6f qty_step=%.6f min_qty=%.6f", tick_size, qty_step, min_qty)


    if preco_ajustado <= 0:
        logger.warning(f"[EARLYTREND] Preco de confirmacao invalido: {preco_confirmacao}")
        return None

    side_norm = side.lower().strip()
    sl = _round_step(float(stop_loss), tick_size)
    tp = _round_step(float(take_profit), tick_size) if take_profit is not None else None

    if sl <= 0 or (tp is not None and tp <= 0):
        logger.warning(f"[EARLYTREND] SL/TP invalidos | SL={sl} TP={tp}")
        return None

    if side_norm == "buy":
        if sl >= preco_ajustado or (tp is not None and tp <= preco_ajustado):
            logger.warning(
                "[EARLYTREND] SL/TP fora da hierarquia | side=BUY | SL=%.6f TP=%.6f trigger=%.6f",
                sl,
                float(tp or 0.0),
                preco_ajustado,
            )
            return None
    else:
        if sl <= preco_ajustado or (tp is not None and tp >= preco_ajustado):
            logger.warning(
                "[EARLYTREND] SL/TP fora da hierarquia | side=SELL | SL=%.6f TP=%.6f trigger=%.6f",
                sl,
                float(tp or 0.0),
                preco_ajustado,
            )
            return None

    params = {
        "category": "linear",
        "symbol": symbol,
        "side": "Buy" if side.lower() == "buy" else "Sell",
        "orderType": "Limit",
        "qty": f"{qty_ajustada}",
        "price": f"{preco_ajustado}",
        "triggerPrice": f"{preco_ajustado}",
        "triggerDirection": 1 if side.lower() == "buy" else 2,
        "triggerBy": "LastPrice",
        "timeInForce": "GTC",
        "stopLoss": f"{sl:.6f}",
        "slTriggerBy": "LastPrice",
    }
    if tp is not None:
        params["takeProfit"] = f"{tp:.6f}"
        params["tpTriggerBy"] = "LastPrice"

    tp_msg = f"{float(tp):.4f}" if tp is not None else "None"
    logger.info(
        f"[EARLYTREND] ARMOU STOP_LIMIT | side={side.upper()} | trigger={preco_ajustado:.4f} | limit={preco_ajustado:.4f} | qty={qty_ajustada} | SL={sl:.4f} | TP={tp_msg}"
    )
    try:
        gate_result = executar_ordem_com_validacao(
            {
                "trade_id": f"early_trend:{symbol}:{side}:{preco_ajustado}",
                "symbol": symbol,
                "side": side,
                "entry_price": preco_ajustado,
                "qty": qty_ajustada,
                "stop_loss": sl,
                "take_profit": tp,
                "decision_timestamp": datetime.utcnow().isoformat(),
                "ttl_seconds": int(float(os.getenv("SIGNAL_TTL_SECONDS", "180"))),
                "order_params": params,
                "execution_fn": lambda payload: api.place_order(**payload),
            }
        )
        if gate_result.get("status") != "executado":
            logger.warning(f"[EARLYTREND] Execution gate bloqueou STOP_LIMIT: {gate_result.get('motivo')}")
            return None
        resposta = (gate_result.get("detalhes") or {}).get("resposta")
    except Exception as e:
        logger.warning(f"[EARLYTREND] Falha ao enviar STOP_LIMIT: {e}", exc_info=True)
        return None

    if not resposta or resposta.get("retCode") != 0:
        ret_code = resposta.get("retCode") if resposta else None
        ret_msg = resposta.get("retMsg") if resposta else None
        logger.warning(
            "[EARLYTREND] STOP_LIMIT rejeitada | retCode=%s retMsg=%s symbol=%s side=%s qty=%s price=%s trigger=%s",
            ret_code,
            ret_msg,
            symbol,
            params.get("side"),
            params.get("qty"),
            params.get("price"),
            params.get("triggerPrice"),
        )
        return None

    return (resposta.get("result") or {}).get("orderId") or resposta.get("orderId")



def _consultar_execucao(api, symbol: str, order_id: str) -> tuple[bool, Optional[float], Optional[float]]:
    try:
        resp = api.get_order_history(category="linear", symbol=symbol, orderId=order_id, limit=1)
    except Exception as e:
        logger.warning(f"[EARLYTREND] Falha ao consultar ordem {order_id}: {e}")
        return False, None, None

    if resp.get("retCode") != 0:
        logger.warning(f"[EARLYTREND] Consulta de ordem retornou erro: {resp.get('retMsg')}")
        return False, None, None

    lista = resp.get("result", {}).get("list", [])
    if not lista:
        return False, None, None

    ordem = lista[0]
    status = (ordem.get("orderStatus") or "").lower()
    qty_exec = float(ordem.get("cumExecQty") or 0)
    avg_price = float(ordem.get("avgPrice") or 0)

    if status == "filled" and qty_exec > 0 and avg_price > 0:
        return True, avg_price, qty_exec

    return False, None, None


def _cancelar_ordem(api, symbol: str, order_id: str) -> None:
    try:
        api.cancel_order(category="linear", symbol=symbol, orderId=order_id)
    except Exception as e:
        logger.warning(f"[EARLYTREND] Erro ao cancelar ordem {order_id}: {e}")


def executar_early_trend_realtime(*, session, symbol: str, df, interval: str, qty: float) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """
    Execução real Early Trend.
    Retorna (trade_id, entry_price, qty) quando entrar; caso contrário (None, None, None).
    """
    if not _get_bool_env("EARLY_TREND_ENABLED", False):
        logger.info("[EARLYTREND] Desativada (EARLY_TREND_ENABLED=false).")
        return None, None, None

    if str(os.getenv("BACKTEST_MODE", "false")).strip().lower() == "true":
        logger.info("[EARLYTREND] BACKTEST_MODE ativo — não envia ordens reais.")
        return None, None, None

    if df is None or getattr(df, "empty", False):
        return None, None, None

    try:
        df_ind = calculate_indicators(df.copy())
    except Exception:
        df_ind = df

    try:
        regime = RegimeDetector().detectar(df_ind, symbol=symbol)
    except Exception as e:
        logger.warning(f"[EARLYTREND] Falha ao detectar regime, usando fallback 2: {e}")
        regime = 2

    entrada = detectar_entrada_early_trend(df_ind, regime)
    if not entrada.ok or not entrada.side or entrada.preco_referencia is None or entrada.tr is None:
        logger.info(f"[EARLYTREND] INELEGIVEL/SEM_SINAL | motivo={entrada.motivo}")
        return None, None, None

    if entrada.stop_loss is None:
        logger.info("[EARLYTREND] SL indefinido - cancelando entrada.")
        return None, None, None

    stop_loss_val = float(entrada.stop_loss)
    take_profit_val = float(entrada.take_profit) if entrada.take_profit is not None else None

    # Preço de confirmação via TR (configurável)
    fator_tr = _get_float_env("EARLY_TREND_CONFIRM_TR_FACTOR", _get_float_env("FATOR_CONFIRMACAO_TR", 0.12))
    preco_confirmacao = (
        float(entrada.preco_referencia) + (float(entrada.tr) * fator_tr)
        if entrada.side == "buy"
        else float(entrada.preco_referencia) - (float(entrada.tr) * fator_tr)
    )

    use_fixed_tp = _get_bool_env("EARLY_TREND_USE_FIXED_TP", True)
    tp_r_mult = _get_float_env("EARLY_TREND_TP_R_MULT", 3.0)
    if tp_r_mult < 0:
        tp_r_mult = 0.0
    risk_dist = abs(float(preco_confirmacao) - float(stop_loss_val))
    if risk_dist <= 0 and entrada.tr:
        risk_dist = abs(float(entrada.tr))
    desired_tp = None
    if risk_dist > 0:
        if entrada.side == "buy":
            desired_tp = float(preco_confirmacao) + (risk_dist * tp_r_mult)
        else:
            desired_tp = float(preco_confirmacao) - (risk_dist * tp_r_mult)

    trailing_enabled = _get_bool_env("EARLY_TREND_TRAILING_ENABLED", False)
    if use_fixed_tp and desired_tp is not None:
        if take_profit_val is None:
            take_profit_val = float(desired_tp)
        elif entrada.side == "buy":
            take_profit_val = max(float(take_profit_val), float(desired_tp))
        else:
            take_profit_val = min(float(take_profit_val), float(desired_tp))
    elif not use_fixed_tp:
        if trailing_enabled:
            take_profit_val = None
        elif desired_tp is not None:
            take_profit_val = float(desired_tp)

    trailing_stop_val = None
    if trailing_enabled:
        trailing_atr_mult = _get_float_env("EARLY_TREND_TRAILING_ATR_MULT", 2.5)
        trailing_min_abs = _get_float_env("EARLY_TREND_TRAILING_MIN_ABS", 0.0)
        trailing_min_pct = _get_float_env("EARLY_TREND_TRAILING_MIN_PCT", 0.0)
        if trailing_atr_mult < 0:
            trailing_atr_mult = 0.0
        if trailing_min_abs < 0:
            trailing_min_abs = 0.0
        if trailing_min_pct < 0:
            trailing_min_pct = 0.0
        base_atr = float(entrada.atr or entrada.tr or 0.0)
        trailing_stop_val = max(
            base_atr * trailing_atr_mult,
            float(preco_confirmacao) * trailing_min_pct,
            trailing_min_abs,
        )
        if trailing_stop_val <= 0:
            trailing_stop_val = None
        else:
            max_offset = abs(float(preco_confirmacao) - float(stop_loss_val))
            if max_offset > 0:
                trailing_stop_val = min(float(trailing_stop_val), max_offset)

    timeout = _get_int_env("EARLY_TREND_ENTRY_TIMEOUT_S", 180)
    intervalo = _get_int_env("EARLY_TREND_ENTRY_CHECK_INTERVAL_S", 3)

    order_id = _enviar_stop_limit_confirmacao(
        session,
        symbol,
        entrada.side,
        float(qty),
        float(preco_confirmacao),
        float(stop_loss_val),
        take_profit_val,
    )
    if not order_id:
        logger.warning("[EARLYTREND] STOP_LIMIT nao enviada | side=%s qty=%.6f trigger=%.4f sl=%.4f tp=%.4f",
                       entrada.side.upper(), float(qty), float(preco_confirmacao), float(stop_loss_val), float(take_profit_val or 0.0))
        return None, None, None

    _notify_telegram(
        f"[EARLYTREND] Ordem armada | symbol={symbol} | side={entrada.side.upper()} | trigger={preco_confirmacao:.4f} | qty={float(qty):.6f} | SL={float(stop_loss_val):.4f} | TP={float(take_profit_val or 0.0):.4f} | order_id={order_id}"
    )

    inicio = time.time()
    while True:
        filled, entry_price, qty_exec = _consultar_execucao(session, symbol, order_id)
        if filled:
            logger.info(f"[EARLYTREND] FILL | order_id={order_id} | price={entry_price:.4f} | qty={qty_exec}")
            break

        if (time.time() - inicio) >= timeout:
            logger.info(f"[EARLYTREND] EXPIRADO | order_id={order_id} | timeout_s={timeout}")
            _notify_telegram(f"[EARLYTREND] Ordem cancelada | symbol={symbol} | motivo=timeout | order_id={order_id}")
            _cancelar_ordem(session, symbol, order_id)
            return None, None, None

        time.sleep(max(1, intervalo))

    # Aplica SL/TP (obrigatorio)
    try:
        payload = {
            "category": "linear",
            "symbol": symbol,
            "stopLoss": f"{float(stop_loss_val):.6f}",
            "slTriggerBy": "LastPrice",
        }
        if take_profit_val is not None:
            payload["takeProfit"] = f"{float(take_profit_val):.6f}"
            payload["tpTriggerBy"] = "LastPrice"
        if trailing_stop_val is not None and trailing_stop_val > 0:
            payload["trailingStop"] = f"{float(trailing_stop_val):.6f}"

        session.set_trading_stop(**payload)
        logger.info(
            f"[EARLYTREND] SL/TP aplicado | SL={payload.get('stopLoss')} | TP={payload.get('takeProfit')} | TS={payload.get('trailingStop')}"
        )
    except Exception as e:
        logger.warning(f"[EARLYTREND] Falha ao aplicar SL/TP (nao fatal): {e}", exc_info=True)

    _notify_telegram(
        f"[EARLYTREND] Ordem preenchida | symbol={symbol} | side={entrada.side.upper()} | order_id={order_id} | price={float(entry_price or 0.0):.4f} | qty={float(qty_exec or qty):.6f} | SL={float(stop_loss_val):.4f} | TP={float(take_profit_val or 0.0):.4f}"
    )

    return str(order_id), float(entry_price or 0.0), float(qty_exec or qty)
