import time
import logging
from pybit.unified_trading import HTTP

# Configuração da API da Bybit
session = HTTP(
    testnet=False,  
    api_key="zJprgOUpJUQeZZaRqO",
    api_secret="lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8"
)

def monitorar_operacao(order_id, symbol, estrategia="padrao"):
    """
    Monitora a execução da ordem até que seja finalizada.

    :param order_id: ID da ordem a ser monitorada.
    :param symbol: Par de negociação (ex.: "BTCUSDT").
    :param estrategia: Nome da estratégia em execução.
    """
    logging.info(f"🔍 Iniciando monitoramento da operação para {symbol} - Ordem {order_id}...")

    # Se for Market Making, o monitoramento será feito pelo próprio módulo
    if estrategia.lower() == "market_making":
        logging.info("🟡 Estratégia Market Making detectada. Monitoramento já acontece internamente. Pulando...")
        return

    while True:
        try:
            order_status = session.get_order(category="spot", orderId=order_id)
            
            if order_status["retCode"] != 0:
                logging.error(f"⚠️ Erro ao buscar status da ordem: {order_status['retMsg']}")
                break

            status = order_status["result"]["orderStatus"]
            logging.info(f"📊 Status atual da ordem {order_id}: {status}")

            if status == "Filled":
                logging.info(f"✅ Ordem {order_id} totalmente executada! Finalizando monitoramento.")
                break
            elif status == "Cancelled":
                logging.warning(f"⚠️ Ordem {order_id} foi cancelada! Finalizando monitoramento.")
                break
            else:
                logging.info(f"⌛ Ordem {order_id} ainda em aberto... aguardando execução.")
            
            time.sleep(5)

        except Exception as e:
            logging.error(f"❌ Erro ao monitorar operação: {e}")
            time.sleep(5)
