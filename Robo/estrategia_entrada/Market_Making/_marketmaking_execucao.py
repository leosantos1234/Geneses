# ============================================================
#   MÓDULO 3 — EXECUÇÃO REALTIME MARKET MAKING PRO (PATCH)
# ============================================================

import logging
from monitoramento import get_lock_path
import time
from datetime import datetime

from execution.execution_gate import executar_ordem_com_validacao



def remover_lock(symbol):
    import os
    try:
        lock_path = get_lock_path(symbol)
        if os.path.exists(lock_path):
            os.remove(lock_path)
            logging.info(f"🔓 Lock removido: {lock_path}")
        else:
            logging.info(f"ℹ️ Nenhum lock para remover: {lock_path}")
    except Exception as e:
        logging.error(f"❌ Erro ao remover lock: {e}")
        

def aguardar_execucao_ou_cancelamento(session, symbol, order_id, timeout=120):
    """
    Aguarda a execução da ordem LIMIT do Market Making.
    Só libera o robô quando:
      - a ordem executa
      - a ordem é cancelada
      - a ordem expira
      - timeout é atingido
    """

    import time
    import logging
    inicio = time.time()

    while True:
        # Timeout
        if time.time() - inicio > timeout:
            logging.warning(f"⏳ Timeout atingido ({timeout}s). Cancelando ordem {order_id}...")
            try:
                session.cancel_order(category="linear", symbol=symbol, orderId=order_id)
            except Exception as e:
                logging.error(f"Erro ao cancelar após timeout: {e}")
            return "timeout"

        # Verifica ordens pendentes
        ordens = session.get_open_orders(category="linear", symbol=symbol)
        aberta = False
        if ordens and "result" in ordens:
            for o in ordens["result"]["list"]:
                if o.get("orderId") == order_id:
                    aberta = True
                    break

        if aberta:
            logging.info("⏳ MM: ordem ainda pendente no livro... aguardando execução.")
            time.sleep(2)
            continue

        # Se não está aberta, verifica posição
        pos = session.get_positions(category="linear", symbol=symbol)
        size = 0
        if pos and "result" in pos and "list" in pos["result"]:
            size = float(pos["result"]["list"][0]["size"])

        if size > 0:
            logging.info("🟢 Ordem executada! Entrou em posição no MM.")
            return "executada"

        # Se não está em posição e não está aberta → foi cancelada/expirada
        logging.info("⚠️ Ordem LIMIT não está mais aberta. Considerada CANCELADA ou EXPIRADA.")
        return "cancelada"



def executar_marketmaking_realtime(df, session, symbol, cfg):
    from monitoramento import monitorar_ordem_ate_encerrar
    

    # ============================================================
    # 1) CALCULAR SPREAD E PREÇOS
    # ============================================================
    # === PREÇO REAL DO MERCADO ===
    try:
        ticker = session.get_tickers(category="linear", symbol=symbol)
        preco_mid = float(ticker["result"]["list"][0]["lastPrice"])
    except Exception as e:
        logging.error(f"❌ Falha ao obter preço do ticker! Usando close do candle. Erro: {e}")
        preco_mid = float(df["close"].iloc[-1])  # fallback de segurança

    microvol = df["close"].pct_change().rolling(10).std().iloc[-1] * 100
    spread = max(cfg["spread_min"], microvol * cfg["spread_multiplier"])

    bid = round(preco_mid - spread / 2, 2)
    ask = round(preco_mid + spread / 2, 2)

    logging.info(f"[MM] MID={preco_mid} | BID={bid} | ASK={ask} | SPREAD={spread:.3f}")

    # ============================================================
    # 2) CANCELAR ORDENS ANTERIORES (POST-ONLY)
    # ============================================================
    if cfg.get("cancel_before_repost", True):
        try:
            session.cancel_all_orders(category="linear", symbol=symbol)
            logging.info("🗑️ Todas as ordens antigas canceladas (MM).")
        except Exception as e:
            logging.warning(f"⚠️ Falha ao cancelar ordens anteriores: {e}")

    # ============================================================
    # 3) DETERMINAR DIREÇÃO
    # ============================================================
    direcao = cfg.get("direcao", "SELL").upper()

    if direcao == "BUY":
        preco_ordem = bid
        side = "Buy"
    else:
        preco_ordem = ask
        side = "Sell"

    qty = cfg["order_qty"]

    logging.info(f"📌 MM enviando ordem LIMIT {side} @ {preco_ordem}")

    # ============================================================
    # 4) ENVIAR APENAS UMA ORDEM (NUNCA AS DUAS)
    # ============================================================
    try:
        gate_result = executar_ordem_com_validacao(
            {
                "trade_id": f"mm_exec:{symbol}:{side.lower()}:{datetime.utcnow().isoformat()}",
                "symbol": symbol,
                "side": side.lower(),
                "entry_price": preco_ordem,
                "qty": qty,
                "decision_timestamp": datetime.utcnow().isoformat(),
                "ttl_seconds": 120,
                "order_params": {
                    "category": "linear",
                    "symbol": symbol,
                    "side": side,
                    "order_type": "Limit",
                    "qty": qty,
                    "price": preco_ordem,
                    "timeInForce": "PostOnly"
                },
                "execution_fn": lambda payload: session.place_order(**payload),
            }
        )
        if gate_result.get("status") != "executado":
            logging.warning(f"ðŸš« Execution gate bloqueou ordem MM: {gate_result.get('motivo')}")
            return None
        ordem = (gate_result.get("detalhes") or {}).get("resposta")
        order_id = ordem["result"]["orderId"]
        logging.info(f"🟢 Ordem LIMIT enviada com sucesso (MM). OrderID={order_id}")
    except Exception as e:
        logging.error(f"❌ Erro ao enviar ordem MM: {e}")
        return None

    # ============================================================
    # 5) CRIAR LOCK FÍSICO (IMPEDIR SEGUNDA ENTRADA)
    # ============================================================
    try:
        with open(get_lock_path(symbol), "w") as f:
            f.write("1")
        logging.info(f"🔒 Lock físico criado → {get_lock_path(symbol)}")
    except Exception as e:
        logging.warning(f"⚠️ Falha ao criar lock físico: {e}")


    # ============================================================
    # 6) AGUARDAR EXECUÇÃO / CANCELAMENTO / TIMEOUT
    # ============================================================
    resultado = aguardar_execucao_ou_cancelamento(
        session=session,
        symbol=symbol,
        order_id=order_id,
        timeout=120  # ajuste como quiser
    )

    if resultado == "executada":
        logging.info("🟢 Ordem executada! Agora iniciando monitoramento completo (SL/TP/Trailing).")

    elif resultado == "cancelada":
        logging.warning("⚠️ Ordem foi cancelada/expirada. Encerrando ciclo do MM.")
        remover_lock(symbol)
        return None

    elif resultado == "timeout":
        logging.warning("⏳ Timeout atingido. Ordem cancelada e ciclo encerrado.")
        remover_lock(symbol)
        return None



    # ============================================================
    # 7) INICIAR MONITORAMENTO DA ORDEM
    # ============================================================
    logging.info("🔍 Entrando no monitoramento Market Making...")

    monitorar_ordem_ate_encerrar(
        trade_id=order_id,
        symbol=symbol,
        position_size=qty,
        entry_price=float(preco_ordem),
        side=side,
        api=session,
        risk_parameters={"tipo_stop": "atr"},
        indicators={}
    )

    logging.info("🏁 Execução Market Making finalizada — voltando ao robô.")
    
    # ============================================================
    # 8) REMOVER LOCK APÓS MONITORAMENTO
    # ============================================================
    remover_lock(symbol)    
    
    return True
