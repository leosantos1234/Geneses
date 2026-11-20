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

def get_market_data_bybit(symbol, session, interval="60", limit=50, category="linear", retries=3, retry_delay=30):
    """Compatibilidade com módulos legados; encaminha para a exchange ativa."""
    return get_market_data(
        symbol=symbol,
        session=session,
        interval=interval,
        limit=limit,
        category=category,
        retries=retries,
        retry_delay=retry_delay,
    )


def get_market_data(symbol, session, interval="60", limit=50, category="linear", retries=3, retry_delay=30):
    """Obtém candles usando o adaptador da exchange ativa."""
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
            df = df.sort_values("time").reset_index(drop=True)
            if not df["time"].is_monotonic_increasing:
                logging.warning("Candles fora de ordem detectados. Corrigindo automaticamente.")
                df = df.sort_values("time").reset_index(drop=True)
            if df.empty or 'time' not in df.columns:
                logging.error("❌ DataFrame inválido ou vazio após processamento.")
                return pd.DataFrame()
            _log_temporal_order(df, "utils.utils_market.get_market_data")
            return df
        except Exception as e:
            logging.error(f"❌ Falha ao processar DataFrame dos dados Bybit: {e}", exc_info=True)
            return pd.DataFrame()

    logging.error("❌ Falha ao obter dados após múltiplas tentativas (rate limit Bybit).")
    return pd.DataFrame()


