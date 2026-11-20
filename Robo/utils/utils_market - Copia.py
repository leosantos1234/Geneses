# utils/utils_market.py
import logging
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


def get_market_data_bybit(symbol, session, interval="60", limit=50):
    try:
        response = session.get_kline(
            category="spot",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if response.get("retCode") == 0:
            data = response["result"]["list"]
            df = pd.DataFrame(data)
            df.columns = ['time', 'open', 'high', 'low', 'close', 'volume', 'turnover']
            df = df.sort_values('time')
            df['close'] = pd.to_numeric(df['close'], errors='coerce')
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
            return df
        else:
            logging.error(f"Erro ao obter dados: {response.get('retMsg')}")
            return pd.DataFrame()
    except Exception as e:
        logging.error(f"Erro ao obter dados de mercado: {e}")
        return pd.DataFrame()
