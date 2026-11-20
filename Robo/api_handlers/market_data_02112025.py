import os
import logging
import pandas as pd
from pybit.unified_trading import HTTP

logger = logging.getLogger(__name__)

# 🔹 Configuração manual das chaves da Bybit (substitua pelos seus valores)
BYBIT_API_KEY = "ZDMew2QP3b3BESqUp1"
BYBIT_API_SECRET = "6dFxZYSDsCM1CBSRtaJ3wVwOGnUM5EWsl3gy"
BYBIT_TESTNET = True  # Defina como False para produção

# 🔹 Inicializa sessão com a Bybit
def initialize_session():
    return HTTP(
        testnet=BYBIT_TESTNET,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET
    )

# 🔹 Função para obter dados de mercado da Bybit
def get_market_data_bybit(symbol, session, interval="60", category="linear", limit=200, save_csv=True):
    logging.info(f"📊 Obtendo dados de mercado para {symbol} | Intervalo: {interval} | Categoria: {category}")

    try:
        response = session.get_kline(
            category=category,
            symbol=symbol,
            interval=interval,
            limit=limit
        )

        # --- Proteção contra Rate Limit Bybit ---
        if response.get("retCode") == 10006:
            # Excedeu o limite de requisições!
            logging.warning("⚠️ Rate limit excedido na Bybit (ErrCode 10006).")
            # Tenta usar o cabeçalho, se existir:
            try:
                # Acessa de forma segura os headers do último request feito pelo session
                # O objeto real depende da lib, adapte se necessário
                last_response = getattr(session, "_last_response", None)
                if last_response:
                    reset_header = last_response.headers.get("X-Bapi-Limit-Reset-Timestamp")
                    if reset_header:
                        import time
                        reset_seconds = max(1, int(reset_header) // 1000 - int(time.time()))
                        logging.warning(f"⏳ Dormindo {reset_seconds}s até reset do rate limit.")
                        time.sleep(reset_seconds)
                    else:
                        logging.warning("⏳ Header de reset não encontrado. Dormindo 10s por segurança.")
                        time.sleep(10)
                else:
                    logging.warning("⏳ Não foi possível acessar o último response. Dormindo 10s por padrão.")
                    time.sleep(10)
            except Exception as e:
                logging.warning(f"⏳ Falha ao checar header do rate limit: {e}. Dormindo 10s.")
                import time
                time.sleep(10)
            return None

        if response.get("retCode") != 0:
            logging.error(f"❌ Erro na API Bybit: {response.get('retMsg')}")
            return None

        data = response.get("result", {}).get("list", [])
        if not data:
            logging.warning("⚠️ Nenhum dado de mercado retornado.")
            return None

        columns = ["time", "open", "high", "low", "close", "volume", "turnover"]
        df = pd.DataFrame(data, columns=columns)

        df["time"] = pd.to_datetime(pd.to_numeric(df["time"], errors='coerce'), unit="ms")

        for col in ["open", "high", "low", "close", "volume", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df.dropna(inplace=True)

        if df.empty:
            logging.error("❌ DataFrame final está vazio após limpeza.")
            return None

        if save_csv:
            df.to_csv("market_data.csv", mode="a", index=False, header=not os.path.exists("market_data.csv"))
            logging.info("✅ Dados salvos em 'market_data.csv'")

        return df

    except Exception as e:
        logging.error(f"❌ Erro ao obter dados de mercado: {e}")
        return None


# 🔹 Teste do módulo (executa apenas se este arquivo for rodado diretamente)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # 🔹 Criar sessão com a API Bybit
    session = initialize_session()

    # 🔹 Obter dados do mercado para BTCUSDT
    df = get_market_data_bybit("BTCUSDT", session)
    
    # 🔹 Exibir os primeiros dados carregados
    if df is not None:
        print(df.head())
