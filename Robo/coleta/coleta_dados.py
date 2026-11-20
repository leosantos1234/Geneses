import pandas as pd
import logging
from sklearn.preprocessing import MinMaxScaler
from imblearn.over_sampling import SMOTE
from sklearn.model_selection import train_test_split
from pybit.unified_trading import HTTP


# Inicializar a sessão da API Bybit
session = HTTP(
    api_key="zJprgOUpJUQeZZaRqO",
    api_secret="lGAHa337TPEsApB5tBPe7elhSZ1nnTjCORf8",
    testnet=True  # Defina como True para usar o ambiente de teste
)

# Função para obter dados de mercado da Bybit
def get_market_data_bybit(symbol, interval="60"):
    try:
        logging.info(f"Obtendo dados de mercado para {symbol} com intervalo {interval}...")
        response = session.get_kline(symbol=symbol, interval=interval, limit=200)
        if response.get("retCode") == 0:
            data = response["result"]["list"]
            if not data:
                logging.error("Nenhum dado de mercado retornado pela API.")
                return None

        # Chamada à API para obter os dados de mercado (kline/candlestick)
        response = session.get_kline(symbol=symbol, interval=interval, limit=200)
        if response.get("retCode") == 0:
            data = response["result"]["list"]
            if not data:
                print("Nenhum dado de mercado retornado pela API.")
                return None

            # Processamento dos dados
            columns = ["time", "open", "high", "low", "close", "volume", "turnover"]
            df = pd.DataFrame(data, columns=columns)

            # Converter tempo para datetime
            df["time"] = pd.to_datetime(pd.to_numeric(df["time"]), unit="ms")

            # Limpar e validar colunas numéricas
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")  # Converte inválidos para NaN
            df.dropna(subset=["open", "high", "low", "close", "volume", "turnover"], inplace=True)

            if df.empty:
                print("Erro: Nenhum dado válido disponível após a limpeza.")
                return None

            print(f"Dados processados e validados:\n{df.head()}")
            return df
        else:
            print(f"Erro na API Bybit: {response.get('retMsg')}")
    except Exception as e:
        print(f"Erro ao obter dados de mercado: {e}")
    return None


# Função para carregar dados históricos
def carregar_dados_historicos(filename):
    try:
        logging.info(f"Carregando dados históricos de {filename}...")
        return pd.read_csv(filename)
    except Exception as e:
        logging.error(f"Erro ao carregar dados históricos: {e}")
        return None

# Função para preparar os dados
def preparar_dados(df):
    try:
        logging.info("Limpando colunas numéricas...")
        df = clean_numeric_columns(df, ['close', 'volume', 'open', 'high', 'low'])

        logging.info("Calculando indicadores técnicos...")
        df['SMA_10'] = df['close'].rolling(window=10).mean()
        df['SMA_50'] = df['close'].rolling(window=50).mean()
        df.dropna(inplace=True)

        logging.info("Normalizando os dados...")
        scaler = MinMaxScaler()
        X = scaler.fit_transform(df[['close', 'volume', 'SMA_10', 'SMA_50']])
        y = (df['close'].shift(-1) > df['close']).astype(int)

        logging.info("Balanceando classes com SMOTE...")
        smote = SMOTE(random_state=42)
        X_bal, y_bal = smote.fit_resample(X, y)

        return X_bal, y_bal
    except Exception as e:
        logging.error(f"Erro ao preparar os dados: {e}")
        return None, None

# Função para limpar colunas numéricas
def clean_numeric_columns(df, columns):
    for column in columns:
        df[column] = pd.to_numeric(df[column], errors='coerce')
    df.dropna(subset=columns, inplace=True)
    return df

# Fluxo principal
if __name__ == "__main__":
    filename = "market_data.csv"
    df = carregar_dados_historicos(filename)
    if df is not None:
        X, y = preparar_dados(df)
        if X is not None and y is not None:
            logging.info(f"Dados prontos para treinamento. Amostras: {len(X)}")
