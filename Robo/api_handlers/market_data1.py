import os
import logging
import pandas as pd
from dotenv import load_dotenv
from pybit.unified_trading import HTTP

def _log_temporal_order(df: pd.DataFrame, origem: str) -> None:
    """Log temporario para auditar ordem temporal dos candles."""
    try:
        if df is None or df.empty or "time" not in df.columns:
            logging.info(f"[TEMP-CANDLE-ORDER] origem={origem} sem_dados")
            return
        head_ts = [str(x) for x in df["time"].head(3).tolist()]
        tail_ts = [str(x) for x in df["time"].tail(3).tolist()]
        inc = bool(df["time"].is_monotonic_increasing)
        dec = bool(df["time"].is_monotonic_decreasing)
        logging.info(
            "[TEMP-CANDLE-ORDER] origem=%s head3=%s tail3=%s monotonic_inc=%s monotonic_dec=%s",
            origem,
            head_ts,
            tail_ts,
            inc,
            dec,
        )
    except Exception as e:
        logging.warning(f"[TEMP-CANDLE-ORDER] falha_origem={origem}: {e}")


def get_market_data_bybit(symbol, session, interval="60"):
    """
    Obtém dados históricos de mercado da Bybit e salva no arquivo CSV.
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
            df = df.sort_values("time").reset_index(drop=True)
            if not df["time"].is_monotonic_increasing:
                logging.warning("Candles fora de ordem detectados. Corrigindo automaticamente.")
                df = df.sort_values("time").reset_index(drop=True)
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df.dropna(inplace=True)
            _log_temporal_order(df, "api_handlers.market_data1.get_market_data_bybit")
            
            if df.empty:
                logging.error("Erro: Nenhum dado válido disponível após a limpeza.")
                return None
            
            df.to_csv("market_data.csv", index=False)
            logging.info("Dados salvos no arquivo 'market_data.csv'")
            return df
        else:
            logging.error(f"Erro na API Bybit: {response.get('retMsg')}")
            return None
    except Exception as e:
        logging.error(f"Erro ao obter dados de mercado: {e}")
        return None

# Teste do módulo
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    session = initialize_session()
    df = get_market_data_bybit("BTCUSDT", session)
    if df is not None:
        print(df.head())
