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



import time
import logging
import pandas as pd

def get_market_data_bybit(symbol, session, interval="60", limit=50, category="linear", retries=3, retry_delay=30):
    for attempt in range(retries):
        try:
            response = session.get_kline(
                category=category,
                symbol=symbol,
                interval=interval,
                limit=limit
            )
        except Exception as e:
            erro_str = str(e).lower()
            if "rate limit" in erro_str or "too many visits" in erro_str:
                logging.warning(f"⚠️ Limite de requisições (rate limit) - tentativa {attempt+1}/{retries}. Aguardando {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            else:
                logging.error(f"❌ Erro inesperado ao acessar a Bybit: {e}")
                return pd.DataFrame()

        # Análise da resposta
        ret_code = response.get("retCode", -1)
        if ret_code == 10006:  # Rate limit bybit
            logging.warning(f"⚠️ Rate limit (ErrCode 10006) — tentativa {attempt+1}/{retries}. Aguardando {retry_delay}s...")
            time.sleep(retry_delay)
            continue
        elif ret_code != 0:
            logging.error(f"❌ Erro Bybit: {response.get('retMsg')} (retCode={ret_code})")
            return pd.DataFrame()

        # Processo normal
        try:
            data = response["result"]["list"]
            expected_cols = ['time', 'open', 'high', 'low', 'close', 'volume', 'turnover']
            df = pd.DataFrame(data, columns=expected_cols)
            # Converte colunas numéricas
            for col in ['open', 'high', 'low', 'close', 'volume', 'turnover']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            # Timestamp ms → datetime
            df['time'] = pd.to_numeric(df['time'], errors='coerce')
            df['time'] = pd.to_datetime(df['time'], unit='ms')
            if df.empty or 'time' not in df.columns:
                logging.error("❌ DataFrame inválido ou vazio após processamento.")
                return pd.DataFrame()
            return df
        except Exception as e:
            logging.error(f"❌ Falha ao processar DataFrame dos dados Bybit: {e}", exc_info=True)
            return pd.DataFrame()

    logging.error("❌ Falha ao obter dados após múltiplas tentativas (rate limit Bybit).")
    return pd.DataFrame()


