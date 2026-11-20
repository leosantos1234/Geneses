# utils/utils_market.py
import logging
import time
import pandas as pd

def obter_preco_mercado(session, symbol):
    try:
        response = session.get_tickers(category="spot", symbol=symbol)
        if response and response.get("retCode") == 0:
            preco = float(response["result"]["list"][0]["lastPrice"])
            logging.info(f"Preço de mercado obtido: {preco}")
            return preco
        else:
            logging.error(f"Erro ao obter preço de mercado: {response.get('retMsg')}")
    except Exception as e:
        logging.error(f"Erro ao consultar preço de mercado: {e}")
    return None



def get_market_data_bybit(symbol, session, interval="60", limit=50, category="linear", retries=3, retry_delay=30):
    try:
        for attempt in range(retries):
            response = session.get_kline(
                category=category,
                symbol=symbol,
                interval=interval,
                limit=limit
            )

            ret_code = response.get("retCode", -1)
            if ret_code == 10006:
                logging.warning(f"⚠️ Limite de requisições API atingido. Tentativa {attempt+1} de {retries}. Aguardando {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            elif ret_code != 0:
                logging.error(f"Erro ao obter dados: {response.get('retMsg')}")
                return pd.DataFrame()
            
            data = response["result"]["list"]
            df = pd.DataFrame(data)
            expected_cols = ['time', 'open', 'high', 'low', 'close', 'volume', 'turnover']
            df.columns = expected_cols
            
            # Converte colunas numéricas
            for col in ['open', 'high', 'low', 'close', 'volume', 'turnover']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # Converte o timestamp para datetime (assumindo timestamp em ms)
            df['time'] = pd.to_numeric(df['time'], errors='coerce')  # Garante tipo numérico
            df['time'] = pd.to_datetime(df['time'], unit='ms')


            if df.empty or 'time' not in df.columns:
                logging.error("❌ DataFrame inválido ou vazio após processamento.")
                return pd.DataFrame()
            
            return df

        logging.error("❌ Falha ao obter dados após múltiplas tentativas por limite de API.")
        return pd.DataFrame()

    except Exception as e:
        logging.error(f"❌ Exceção ao obter dados de mercado: {e}", exc_info=True)
        return pd.DataFrame()

