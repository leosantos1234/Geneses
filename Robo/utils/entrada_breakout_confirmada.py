"""
Entrada profissional de breakout com confirmacao por TR/ATR.

Fluxo:
- Calcula preco de confirmacao alem do ponto de rompimento.
- Envia ordem STOP LIMIT (condicional) sem SL/TP/trailing.
- Aguarda execucao com timeout e cancela se nao preencher.
- Retorna None se nao confirmou; dict com entry_price real se confirmou.
"""

import logging
import os
import time
from typing import Dict, Optional, Tuple

from utils.utils_telegram import send_telegram_message
from execution.execution_gate import executar_ordem_com_validacao
logger = logging.getLogger(__name__)


# =============================================================================
# Configurações (.env)
# =============================================================================
FATOR_CONFIRMACAO_TR = float(os.getenv("FATOR_CONFIRMACAO_TR", os.getenv("OFFSET_CONFIRMACAO_ATR", 0.12)))
TIMEOUT_ENTRADA_SEGUNDOS = int(os.getenv("TIMEOUT_ENTRADA_SEGUNDOS", 180))
INTERVALO_VERIFICACAO_ENTRADA = int(os.getenv("INTERVALO_VERIFICACAO_ENTRADA", 3))
# Folga controlada entre STOP e LIMIT para reduzir "disparou mas não preencheu".
# Ex.: 0.0003 = 0.03% | BUY: limit > stop | SELL: limit < stop
BREAKOUT_STOP_LIMIT_SLIPPAGE_PCT = float(os.getenv("BREAKOUT_STOP_LIMIT_SLIPPAGE_PCT", 0.0))
# Gap minimo entre preco atual e trigger do STOP LIMIT para evitar rejeicao
# (ErrCode 110092 "expect Rising/Falling"). Ex.: 0.0002 = 0.02%
BREAKOUT_STOP_LIMIT_MIN_GAP_PCT = float(os.getenv("BREAKOUT_STOP_LIMIT_MIN_GAP_PCT", 0.0002))


# =============================================================================
# Helpers
# =============================================================================
def calcular_preco_confirmacao(preco_breakout: float, side: str, tr: float, fator_confirmacao: float = FATOR_CONFIRMACAO_TR) -> float:
    """Calcula o preço de confirmação do breakout."""
    offset = tr * fator_confirmacao
    side_norm = side.lower()
    preco_base = preco_breakout + offset if side_norm == "buy" else preco_breakout - offset

    raw_entry_offset = os.getenv("BREAKOUT_ENTRY_OFFSET", "0.0")
    try:
        breakout_entry_offset = float(raw_entry_offset or 0.0)
    except ValueError:
        logger.error("[BREAKOUT] Valor inválido em BREAKOUT_ENTRY_OFFSET (%s). Usando 0.0.", raw_entry_offset)
        breakout_entry_offset = 0.0

    if breakout_entry_offset == 0:
        return preco_base

    preco_ajustado = (
        preco_base * (1 + breakout_entry_offset)
        if side_norm == "buy"
        else preco_base * (1 - breakout_entry_offset)
    )

    logger.info(
        "[BREAKOUT] Offset aplicado | estrategia=breakout | side=%s | preco_base=%.4f | offset=%.6f | preco_final=%.4f",
        side.upper(),
        preco_base,
        breakout_entry_offset,
        preco_ajustado,
    )

    if preco_ajustado <= 0:
        logger.error(
            "[BREAKOUT] Preço de entrada inválido após offset | estrategia=breakout | side=%s | preco_base=%.8f | offset_raw=%s | preco_final=%.8f",
            side.upper(),
            preco_base,
            raw_entry_offset,
            preco_ajustado,
        )

    return preco_ajustado


def _round_step(valor: float, step: float) -> float:
    if step <= 0:
        return valor
    casas = len(str(step).split(".")[-1].rstrip("0"))
    return round(round(valor / step) * step, casas)


def _obter_precisao(api, symbol: str) -> Tuple[float, float]:
    tick_size, qty_step = 0.01, 0.001
    try:
        info = api.get_instruments_info(category="linear", symbol=symbol)
        item = info.get("result", {}).get("list", [{}])[0]
        tick_size = float(item.get("priceFilter", {}).get("tickSize", tick_size))
        qty_step = float(item.get("lotSizeFilter", {}).get("qtyStep", qty_step))
    except Exception as e:
        logger.warning(f"Não foi possível obter precisão de {symbol}: {e}")
    return tick_size, qty_step


def _notificar_cancelamento(symbol: str, side: str, preco_esperado: float, motivo: str) -> None:
    """Envia aviso padronizado de cancelamento da entrada confirmada."""
    msg = (
        "❌ BREAKOUT CANCELADO\n"
        f"Ativo: {symbol}\n"
        f"Lado: {side.upper()}\n"
        "Tipo: STOP LIMIT\n"
        f"Preço esperado: {preco_esperado:.4f}\n"
        f"Motivo: {motivo}"
    )
    logging.warning(msg)
    try:
        send_telegram_message(msg)
    except Exception as e:
        logging.warning(f"Falha ao enviar Telegram de cancelamento: {e}")


def _enviar_ordem_stop_limit(
    api,
    symbol: str,
    side: str,
    qty: float,
    preco_confirmacao: float,
    trade_id: Optional[str] = None,
    decision_timestamp: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict]:
    tick_size, qty_step = _obter_precisao(api, symbol)
    side_norm = side.lower().strip()
    trigger_price = _round_step(preco_confirmacao, tick_size)

    slippage_pct = float(BREAKOUT_STOP_LIMIT_SLIPPAGE_PCT or 0.0)
    if slippage_pct < 0:
        slippage_pct = 0.0

    min_gap_pct = float(BREAKOUT_STOP_LIMIT_MIN_GAP_PCT or 0.0)
    if min_gap_pct < 0:
        min_gap_pct = 0.0

    # Se o preco ja passou do trigger, a Bybit rejeita (ErrCode 110092).
    # Ajusta trigger para ficar do lado correto do preco atual.
    try:
        ticker = api.get_tickers(category="linear", symbol=symbol)
        last_price = float(ticker["result"]["list"][0]["lastPrice"])

        tick_gap_pct = float(tick_size) / max(float(last_price), 1.0)
        gap_pct = max(min_gap_pct, tick_gap_pct)

        if side_norm == "buy" and float(trigger_price) <= float(last_price):
            trigger_price = _round_step(float(last_price) * (1.0 + gap_pct), tick_size)
            if float(trigger_price) <= float(last_price):
                trigger_price = _round_step(float(last_price) + float(tick_size), tick_size)

        elif side_norm == "sell" and float(trigger_price) >= float(last_price):
            trigger_price = _round_step(float(last_price) * (1.0 - gap_pct), tick_size)
            if float(trigger_price) >= float(last_price):
                trigger_price = _round_step(float(last_price) - float(tick_size), tick_size)

    except Exception as e:
        logger.debug(f"[BREAKOUT] Falha ao obter lastPrice para validar trigger: {e}")

    raw_limit = trigger_price
    if slippage_pct > 0:
        if side_norm == "buy":
            raw_limit = trigger_price * (1.0 + slippage_pct)
        else:
            raw_limit = trigger_price * (1.0 - slippage_pct)

    limit_price = _round_step(raw_limit, tick_size)
    qty_ajustada = _round_step(qty, qty_step)

    if trigger_price <= 0 or limit_price <= 0:
        logger.error(
            "[BREAKOUT] trigger/limit invalidos | estrategia=breakout | side=%s | triggerPrice=%.8f | limitPrice=%.8f",
            side.upper(),
            float(trigger_price),
            float(limit_price),
        )
        _notificar_cancelamento(symbol, side, preco_confirmacao, "preço inválido")
        return None

    if side_norm == "buy" and limit_price < trigger_price:
        limit_price = trigger_price
    if side_norm == "sell" and limit_price > trigger_price:
        limit_price = trigger_price

    params = {
        "category": "linear",
        "symbol": symbol,
        "side": "Buy" if side.lower() == "buy" else "Sell",
        "orderType": "Limit",
        "qty": f"{qty_ajustada:.{len(str(qty_step).split('.')[-1].rstrip('0'))}f}",
        "price": f"{limit_price:.{len(str(tick_size).split('.')[-1].rstrip('0'))}f}",
        "triggerPrice": f"{trigger_price:.{len(str(tick_size).split('.')[-1].rstrip('0'))}f}",
        "triggerDirection": 1 if side.lower() == "buy" else 2,
        "triggerBy": "LastPrice",
        "timeInForce": "GTC",
    }

    logger.info(
        "[BREAKOUT] Enviando STOP LIMIT | estrategia=breakout | side=%s | trigger_price=%.4f | limit_price=%.4f | slippage_pct=%.5f",
        side.upper(),
        float(trigger_price),
        float(limit_price),
        float(slippage_pct),
    )
    try:
        # A OKX nao interpreta triggerPrice no endpoint de ordem normal.
        # Use o endpoint nativo de ordem-algo quando o adaptador o oferece.
        if hasattr(api, "place_algo_order"):
            gate_payload = {
                "trigger_price": trigger_price,
                "order_price": limit_price,
                "trigger_by": "last",
                "order_link_id": trade_id,
            }
            execution_fn = lambda payload: api.place_algo_order(
                category="linear", symbol=symbol,
                side="buy" if side_norm == "buy" else "sell",
                qty=qty_ajustada, **payload,
            )
        else:
            gate_payload = params
            execution_fn = lambda payload: api.place_order(**payload)
        gate_result = executar_ordem_com_validacao(
            {
                "trade_id": trade_id,
                "symbol": symbol,
                "side": side,
                "entry_price": trigger_price,
                "qty": qty_ajustada,
                "decision_timestamp": decision_timestamp,
                "ttl_seconds": ttl_seconds or int(float(os.getenv("SIGNAL_TTL_SECONDS", "180"))),
                "order_params": gate_payload,
                "execution_fn": execution_fn,
            }
        )
        if gate_result.get("status") != "executado":
            _notificar_cancelamento(
                symbol,
                side,
                preco_confirmacao,
                f"bloqueada pelo gate: {gate_result.get('motivo')}",
            )
            return None

        resposta = (gate_result.get("detalhes") or {}).get("resposta")
    except Exception as e:
        logger.error(f"Erro ao enviar ordem STOP LIMIT de confirmação: {e}", exc_info=True)
        _notificar_cancelamento(symbol, side, preco_confirmacao, "falha ao enviar ordem")
        return None

    if not resposta or resposta.get("retCode") != 0:
        logger.error(f"Falha ao enviar ordem STOP LIMIT: {resposta}")
        _notificar_cancelamento(symbol, side, preco_confirmacao, "falha na resposta da exchange")
        return None

    result = resposta.get("result") or {}
    rows = (result.get("list") or []) if isinstance(result, dict) else []
    first = rows[0] if rows else {}
    result_order_id = result.get("orderId") if isinstance(result, dict) else None
    order_id = (first.get("orderId") or first.get("algoId") or result_order_id
                or resposta.get("orderId") or resposta.get("algoId"))
    return {
        "orderId": order_id,
        "price": trigger_price,
        "limit_price": limit_price,
        "qty": qty_ajustada,
    }


def _consultar_execucao(api, symbol: str, order_id: str) -> Tuple[bool, Optional[float], Optional[float]]:
    try:
        if hasattr(api, "get_algo_order"):
            resp = api.get_algo_order(category="linear", symbol=symbol, algoId=order_id)
        else:
            resp = api.get_order_history(category="linear", symbol=symbol, orderId=order_id, limit=1)
    except Exception as e:
        logger.warning(f"Falha ao consultar ordem {order_id}: {e}")
        return False, None, None

    if resp.get("retCode") != 0:
        logger.warning(f"Consulta de ordem retornou erro: {resp.get('retMsg')}")
        return False, None, None

    lista = resp.get("result", {}).get("list", [])
    if not lista:
        return False, None, None

    ordem = lista[0]
    status = (ordem.get("orderStatus") or ordem.get("state") or "").lower()
    qty_exec = float(ordem.get("cumExecQty") or ordem.get("actualSz") or ordem.get("fillSz") or 0)
    avg_price = float(ordem.get("avgPrice") or ordem.get("actualPx") or ordem.get("avgPx") or 0)

    if status == "filled" and qty_exec > 0 and avg_price > 0:
        return True, avg_price, qty_exec

    return False, None, None


def _cancelar_ordem(api, symbol: str, order_id: str) -> None:
    try:
        if hasattr(api, "cancel_algo_order") and hasattr(api, "get_algo_order"):
            api.cancel_algo_order(category="linear", symbol=symbol, algoId=order_id)
        else:
            api.cancel_order(category="linear", symbol=symbol, orderId=order_id)
    except Exception as e:
        logger.warning(f"Erro ao cancelar ordem {order_id}: {e}")


def _aguardar_execucao(api, symbol: str, order_id: str, timeout: int, intervalo: int) -> Tuple[bool, Optional[float], Optional[float]]:
    inicio = time.time()
    ultima_mensagem = -1

    while True:
        executada, preco, qty = _consultar_execucao(api, symbol, order_id)
        if executada:
            logger.info("✅ Breakout confirmado — entrada executada em %.4f", preco)
            return True, preco, qty

        decorrido = int(time.time() - inicio)
        if decorrido >= timeout:
            logger.warning("🚫 Breakout não confirmado — ordem cancelada sem custo")
            return False, None, None

        if decorrido // intervalo != ultima_mensagem // intervalo:
            logger.info("⏳ Aguardando execução (%ss / %ss)", decorrido, timeout)
            ultima_mensagem = decorrido

        time.sleep(max(1, intervalo))


# =============================================================================
# API principal
# =============================================================================
def executar_entrada_breakout_confirmada(
    api,
    symbol: str,
    side: str,
    preco_breakout: float,
    tr: float,
    qty: float,
    fator_confirmacao: float = FATOR_CONFIRMACAO_TR,
    timeout: int = TIMEOUT_ENTRADA_SEGUNDOS,
    intervalo: int = INTERVALO_VERIFICACAO_ENTRADA,
    trade_id: Optional[str] = None,
    decision_timestamp: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Optional[Dict]:
    """
    Executa a entrada de breakout via ordem STOP LIMIT confirmada.

    Retorna:
        None -> não confirmou
        Dict -> {"orderId", "entry_price", "qty"}
    """
    logger.info("🎯 Breakout detectado — aguardando confirmação por TR")

    preco_confirmacao = calcular_preco_confirmacao(preco_breakout, side, tr, fator_confirmacao)
    if preco_confirmacao is None or preco_confirmacao <= 0:
        logger.error(
            "[BREAKOUT] Preço de confirmação inválido | estrategia=breakout | side=%s | preco=%.8f",
            side.upper(),
            preco_confirmacao or 0.0,
        )
        _notificar_cancelamento(symbol, side, preco_confirmacao or 0.0, "preço inválido")
        return None

    ordem = _enviar_ordem_stop_limit(
        api,
        symbol,
        side,
        qty,
        preco_confirmacao,
        trade_id=trade_id,
        decision_timestamp=decision_timestamp,
        ttl_seconds=ttl_seconds,
    )
    if not ordem or not ordem.get("orderId"):
        logger.error("Não foi possível enviar ordem STOP LIMIT de confirmação.")
        # _enviar_ordem_stop_limit já notificou os casos de erro; evita duplicar
        return None

    sucesso, preco_exec, qty_exec = _aguardar_execucao(api, symbol, ordem["orderId"], timeout, intervalo)
    if not sucesso:
        _cancelar_ordem(api, symbol, ordem["orderId"])
        _notificar_cancelamento(symbol, side, preco_confirmacao, "não confirmou dentro do tempo limite")
        return None

    return {
        "orderId": ordem["orderId"],
        "entry_price": preco_exec,
        "qty": qty_exec,
        "price_planned": preco_confirmacao,
    }
