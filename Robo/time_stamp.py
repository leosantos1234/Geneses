import requests
import time
import logging
logger = logging.getLogger(__name__)

def obter_diferenca_timestamp():
    try:
        response = requests.get("https://api.bybit.com/v5/market/time", timeout=5)
        response.raise_for_status()
        server_time = int(response.json()["time"])  # ✅ Corrigido aqui
        local_time = int(time.time() * 1000)
        diferenca = server_time - local_time

        logging.info(f"🕰️ Timestamp do servidor Bybit: {server_time}")
        logging.info(f"🕒 Timestamp local           : {local_time}")
        logging.info(f"⏳ Diferença calculada      : {diferenca} ms")

        if abs(diferenca) > 1000:
            logging.warning("⚠️ Diferença acima de 1 segundo! Pode causar rejeição de ordens assinadas.")

        return diferenca
    except Exception as e:
        logging.error(f"❌ Erro ao consultar timestamp da Bybit: {e}")
        return 0

# ✅ Executa ao rodar o script diretamente
if __name__ == "__main__":
    delta = obter_diferenca_timestamp()
    print(f"Diferença de timestamp: {delta} ms")
