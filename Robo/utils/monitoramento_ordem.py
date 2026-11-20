# utils/monitoramento_ordem.py

import time
import logging


def _get_last_price(api, symbol):
    """Tenta obter o último preço negociado de forma resiliente usando vários endpoints."""
    try:
        # pybit unified possivelmente expõe get_ticker/get_tickers/get_mark_price
        for method in ("get_symbol_ticker", "get_ticker", "get_mark_price", "get_tickers", "get_public_trading_records"):
            fn = getattr(api, method, None)
            if not fn:
                continue
            try:
                # tenta chamadas comuns
                if method == "get_public_trading_records":
                    resp = fn(symbol=symbol, limit=1)
                    if isinstance(resp, dict) and resp.get("retCode") == 0:
                        lst = resp.get("result", [])
                        if lst:
                            return float(lst[0].get("price") or lst[0].get("p") or 0)
                else:
                    resp = fn(symbol=symbol)
                    if isinstance(resp, dict) and resp.get("retCode") == 0:
                        # vários formatos: result.list[0].lastPrice ou result.lastPrice
                        res = resp.get("result", {})
                        if isinstance(res, dict):
                            if res.get("lastPrice"):
                                return float(res.get("lastPrice"))
                            if res.get("price"):
                                return float(res.get("price"))
                        if isinstance(res, list) and res:
                            first = res[0]
                            if first.get("lastPrice"):
                                return float(first.get("lastPrice"))
                            if first.get("price"):
                                return float(first.get("price"))
            except Exception:
                continue
    except Exception:
        pass
    return None


def esperar_ordem_executada(api, symbol, order_id, timeout=30, extend_if_price_within=0.002, max_total_timeout=300, allow_market_fallback=False, validate_breakout=None, estrategia="default"):
    """
    Aguarda até a ordem ser executada (Filled) ou até atingir tempo máximo.

    Comportamento adaptativo:
    - Espera `timeout` segundos inicialmente.
    - Se não executada, verifica o preço de mercado.
      Se o preço estiver a menos de `extend_if_price_within` (fração) do preço da ordem,
      estende a espera em blocos curtos até `max_total_timeout`.
    - Se allow_market_fallback=True e validate_breakout() retornar True, converte para MARKET.

    Retorna True se executada, False caso contrário.
    """
    tempo_inicio = time.time()
    total_allowed = max_total_timeout
    initial_timeout = max(1, int(timeout))

    # primeira janela
    elapsed = 0
    checked_extensions = 0

    while time.time() - tempo_inicio < total_allowed:
        try:
            status = api.get_order_history(category="linear", symbol=symbol, orderId=order_id)
        except Exception:
            status = {}

        if status.get("retCode", 1) == 0:
            orders = status["result"].get("list", [])
            if orders:
                order = orders[0]
                cum = float(order.get("cumExecQty", 0) or 0)
                state = order.get("orderStatus", "")
                if cum > 0 and state.lower() == "filled":
                    logging.info(f"✅ Ordem {order_id} EXECUTADA!")
                    return True
                # se parcialmente preenchida também consideramos como executada dependendo da política
                if cum > 0 and state.lower() in ("partiallyfilled", "partially_filled"):
                    logging.info(f"ℹ️ Ordem {order_id} parcialmente executada (qty={cum}).")
                    return True

                # obter preço da ordem (tenta diversos campos)
                order_price = None
                for k in ("price", "orderPrice", "px", "p"):
                    try:
                        v = order.get(k)
                        if v not in (None, "", 0, "0"):
                            order_price = float(v)
                            break
                    except Exception:
                        continue

                # se já passou a janela inicial sem execução
                elapsed = time.time() - tempo_inicio
                if elapsed >= initial_timeout:
                    # tenta verificar preço de mercado para decidir estender
                    if order_price:
                        last_price = _get_last_price(api, symbol)
                        if last_price:
                            try:
                                prox = abs(last_price - order_price) / (order_price or 1)
                            except Exception:
                                prox = 999
                            logging.info(f"⚖️ Ordem não preenchida, proximidade do mercado: {prox:.4f} (limite {extend_if_price_within}) | last={last_price} order={order_price}")
                            if prox <= extend_if_price_within and (time.time() - tempo_inicio) < total_allowed:
                                # estende mais um bloco de espera curto (20s)
                                extra = min(20, total_allowed - (time.time() - tempo_inicio))
                                if extra <= 0:
                                    break
                                logging.info(f"⏳ Preço próximo. Estendendo espera por {extra}s (extensão #{checked_extensions+1}).")
                                time.sleep(extra)
                                checked_extensions += 1
                                continue

                    # 🔒 Fallback para MARKET (desabilitado para BREAKOUT)
                    is_breakout = estrategia == "breakout"
                    # Para BREAKOUT: NÃO fazer fallback
                    # Para outras estratégias: fallback se permitido e rompimento validado
                    if allow_market_fallback and not is_breakout and validate_breakout and validate_breakout():
                        logging.info(f"🎯 Rompimento validado! Convertendo ordem {order_id} para MARKET.")
                        try:
                            # Cancelar LIMIT e enviar MARKET
                            api.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                            # Obter qty da ordem original
                            qty = None
                            if orders:
                                qty = orders[0].get("qty")
                            if qty:
                                market_resp = api.place_order(
                                    category="linear",
                                    symbol=symbol,
                                    side=orders[0].get("side"),
                                    orderType="Market",
                                    qty=qty,
                                    timeInForce="IOC",
                                    reduceOnly=True,
                                )
                                if market_resp and market_resp.get("retCode") == 0:
                                    logging.info(f"✅ Ordem MARKET executada: {market_resp['result']['orderId']}")
                                    return True
                                else:
                                    logging.error(f"❌ Falha ao enviar MARKET: {market_resp}")
                        except Exception as e:
                            logging.error(f"❌ Erro ao converter para MARKET: {e}")

                    # Para BREAKOUT: em caso de timeout total, cancelar e abortar
                    if is_breakout:
                        logging.info(f"⏰ BREAKOUT expirado: Ordem LIMIT {order_id} não executada. Cancelando e abortando trade.")
                        try:

                            api.cancel_order(category="linear", symbol=symbol, orderId=order_id)
                            logging.info(f"✅ Ordem {order_id} cancelada com sucesso.")
                        except Exception as e:
                            logging.error(f"❌ Erro ao cancelar ordem {order_id}: {e}")
                        return False

                    # caso não seja próximo ou não tenha preço, não estende indefinidamente
                    logging.warning(f"⏳ Ordem {order_id} não executada após {initial_timeout}s e sem proximidade suficiente. Timeout.")
                    return False

        # se nenhum dado válido, espera um pouco e re-testa
        time.sleep(1)

    logging.warning(f"⏳ Timeout máximo atingido ({total_allowed}s): Ordem {order_id} não executada.")
    return False
