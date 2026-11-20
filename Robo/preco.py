import logging
import time
from compra1 import obter_preco_mercado, create_order, initialize_session

# 🔹 Inicializa a sessão da Bybit
session = initialize_session()

def monitorar_ordem(session, symbol, order_id, stop_loss, take_profit, qty=0.01):
    """Monitora a ordem e verifica Stop-Loss e Take-Profit."""
    logging.info(f"🔍 Iniciando monitoramento da ordem {order_id} para {symbol}")

    while True:
        preco_atual = obter_preco_mercado(session, symbol)

        if preco_atual is None:
            logging.error("❌ ERRO: `obter_preco_mercado()` retornou None. Abortando monitoramento.")
            break

        logging.info(f"📊 Preço atual: {preco_atual} | TP: {take_profit} | SL: {stop_loss}")
        print(f"🟢 Monitorando... Preço atual: {preco_atual}")

        if preco_atual >= take_profit:
            logging.info(f"🎯 Take-Profit atingido ({take_profit}). Criando ordem de venda para encerrar posição.")
            create_order(session, symbol, "sell", qty, "Market")
            break
        elif preco_atual <= stop_loss:
            logging.info(f"🚨 Stop-Loss atingido ({stop_loss}). Criando ordem de venda para encerrar posição.")
            create_order(session, symbol, "sell", qty, "Market")
            break

        time.sleep(5)

    logging.info(f"✅ Ordem {order_id} finalizada. Monitoramento encerrado.")

# **TESTE A FUNÇÃO**
monitorar_ordem(session, "BTCUSDT", "123456", stop_loss=83500, take_profit=84000, qty=0.01)
