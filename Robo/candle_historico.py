
import logging
import pandas as pd
from pybit.unified_trading import HTTP
from datetime import datetime

# Configura logger
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")

# 🚀 Função de obtenção dos candles
def obter_candles_historicos(symbol, interval="1", start=None, end=None, limit=200, testnet=False):
    import requests
    import time

    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    url = f"{base_url}/v5/market/kline"

    if not start:
        # Pega os últimos candles, até agora
        end = int(time.time() * 1000)
        start = end - limit * int(interval) * 60 * 1000  # ex: 200 candles de 1min

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": str(interval),
        "start": start,
        "end": end,
        "limit": limit
    }

    try:
        response = requests.get(url, params=params)
        data = response.json()

        if data.get("retCode") != 0:
            print(f"❌ Erro na API: {data.get('retMsg')}")
            return None

        candles = data.get("result", {}).get("list", [])
        if not candles:
            print("⚠️ Nenhum candle retornado.")
            return None

        # Converte para floats e ints
        candles_fmt = [
            {
                "timestamp": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "turnover": float(c[6])
            }
            for c in candles
        ]
        return candles_fmt

    except Exception as e:
        print(f"❌ Erro ao obter candles: {e}")
        return None


# 🔐 Sessão de PRODUÇÃO
API_KEY = "qYrP9I7OQCVRoohgbl"
API_SECRET = "or0XeTv8WPEnqQeMJBJZDzJyJ6gpBQVgFPsF"

def testar_obter_candles():
    logging.info("🔑 Criando sessão com a Bybit...")
    session = HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET)
    logging.info("✅ Sessão criada com sucesso. Testando endpoint de candles...")

    df = obter_candles_historicos(session, symbol="BTCUSDT", interval="5m", limit=5)

    if df.empty:
        logging.error("❌ DataFrame retornado está vazio.")
    else:
        logging.info(f"✅ DataFrame carregado com sucesso! Última candle: {df.iloc[-1].to_dict()}")
        print(df.tail())

if __name__ == "__main__":
    testar_obter_candles()
