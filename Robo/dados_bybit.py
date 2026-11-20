import os
import logging
import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP


logger = logging.getLogger(__name__)

def initialize_session():
    """
    Inicializa a sessão HTTP da API Bybit.
    Retorna a sessão ativa.
    """
    load_dotenv()
    BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
    BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")
    
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        raise ValueError("As credenciais da API Bybit não foram carregadas corretamente.")
    
    session = HTTP(
        testnet=False,  # Defina como True se estiver testando no Testnet
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET
    )
    logging.info("Sessão da API Bybit inicializada com sucesso.")
    return session


def get_market_data_filename(symbol, interval="60"):
    return f"market_data_{symbol}_{interval}m.csv"

def get_market_data_bybit(symbol, session, interval="60", max_rows=10000):
    """
    Obtém dados históricos de mercado da Bybit e salva no arquivo CSV SEPARADO por ativo.
    """
    logging.info(f"Obtendo dados de mercado para {symbol} com intervalo {interval}...")
    try:
        response = session.get_kline(symbol=symbol, interval=interval, limit=200)
        logging.info(f"Resposta da API: {response}")

        if response.get("retCode") == 0:
            data = response.get("result", {}).get("list", [])
            if not data:
                logging.error("Nenhum dado de mercado retornado pela API.")
                return None

            columns = ["time", "open", "high", "low", "close", "volume", "turnover"]
            df = pd.DataFrame(data, columns=columns)
            df["time"] = pd.to_datetime(pd.to_numeric(df["time"], errors='coerce'), unit="ms")
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df.dropna(inplace=True)

            if df.empty:
                logging.error("Erro: Nenhum dado válido disponível após a limpeza.")
                return None

            # Caminho dinâmico, com máximo de linhas salvas
            filename = get_market_data_filename(symbol, interval)
            # Se já existir, concatena os novos dados e mantém só os últimos max_rows
            if os.path.exists(filename):
                df_old = pd.read_csv(filename)
                df = pd.concat([df_old, df]).drop_duplicates(subset="time").sort_values("time")
                df = df.tail(max_rows)
            else:
                df = df.tail(max_rows)
            df.to_csv(filename, index=False)
            logging.info(f"Dados salvos no arquivo '{filename}'")
            return df
        else:
            logging.error(f"Erro na API Bybit: {response.get('retMsg')}")
            return None
    except Exception as e:
        logging.error(f"Erro ao obter dados de mercado: {e}")
        return None



# Teste do módulo
if __name__ == "__main__":
    session = initialize_session()
    from utils.parse_env import get_ativos_env
    ativos = get_ativos_env()  # Exemplo: ['BTCUSDT', 'BNBUSDT']

    for symbol in ativos:
        df = get_market_data_bybit(symbol, session)
        if df is not None:
            print(f"{symbol}: {df.shape}")

