import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)



def calculate_indicators(df):
    """
    Calcula Indicadores Técnicos:
    - SMA, Bollinger Bands, RSI, ATR, EMA, MACD, Resistance e Support.
    """
    try:
        # ✅ Validação de colunas essenciais
        required_columns = ['close', 'high', 'low']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise KeyError(f"🚨 ERRO: As colunas {missing_columns} estão ausentes no DataFrame!")

        # ✅ Convertendo colunas para numérico
        df[required_columns] = df[required_columns].apply(pd.to_numeric, errors='coerce')
        df.dropna(subset=required_columns, inplace=True)

        if df.empty:
            raise ValueError("🚨 ERRO: Dados insuficientes para calcular indicadores técnicos.")

        # 🔹 SMA (Médias móveis simples)
        df['SMA_10'] = df['close'].rolling(window=10, min_periods=1).mean()
        df['SMA_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # 🔹 Bollinger Bands
        df['SMA_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['std_dev'] = df['close'].rolling(window=20, min_periods=1).std()
        df['Bollinger_Upper'] = df['SMA_20'] + (2 * df['std_dev'])
        df['Bollinger_Lower'] = df['SMA_20'] - (2 * df['std_dev'])

        # Log para verificar as últimas 5 linhas do DataFrame após o cálculo das Bollinger Bands
        logging.info(f"DataFrame após calcular Bollinger Bands:\n{df.tail(5)}")


        # 🔹 RSI
        delta = df['close'].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(window=14, min_periods=1).mean()
        avg_loss = pd.Series(loss).rolling(window=14, min_periods=1).mean()
        rs = np.where(avg_loss == 0, 0, avg_gain / avg_loss)
        df['RSI'] = 100 - (100 / (1 + rs))

        # 🔹 ATR (Correção para evitar NaN nos primeiros períodos)
        df['TrueRange'] = np.maximum(df['high'] - df['low'], 
                                     np.maximum(np.abs(df['high'] - df['close'].shift()), 
                                                np.abs(df['low'] - df['close'].shift())))
        df['ATR'] = df['TrueRange'].rolling(window=14, min_periods=1).mean()

        # 🔹 Resistance e Support (janela de 3 períodos)
        df['Resistance'] = df['high'].rolling(window=3, min_periods=1).max()
        df['Support'] = df['low'].rolling(window=3, min_periods=1).min()

        # 🔹 EMA
        df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()

        # 🔹 EMA 200 para tendência principal
        df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()

	# 🔹 EMA 50 (ausente atualmente)
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()

        # 🔹 Volume médio dos últimos 20 candles
        df['Volume_Media'] = df['volume'].rolling(window=20, min_periods=1).mean()     

        # 🔹 MACD
        df['MACD'] = df['EMA12'] - df['EMA26']
        df['Signal_Line'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

        # 🔹 Momentum (diferença do fechamento atual com N períodos atrás)
        df['momentum'] = df['close'] - df['close'].shift(10)

        # 🔹 Verificação final de colunas esperadas
        expected_columns = ['SMA_10', 'SMA_50', 'SMA_20', 'Bollinger_Upper', 'Bollinger_Lower',
                            'RSI', 'ATR', 'EMA12', 'EMA26', 'EMA_20', 'EMA50', 'MACD', 'Signal_Line',
                            'MACD_Histogram', 'Resistance', 'Support', 'momentum']

        missing_columns = [col for col in expected_columns if col not in df.columns]
        if missing_columns:
            logging.error(f"🚨 ERRO: As colunas calculadas {missing_columns} não foram geradas corretamente!")
            print(df.tail(5))  # Debug para ver as colunas existentes
            return None

        logging.info("✅ Indicadores técnicos calculados com sucesso.")
        df.dropna(subset=[
            "ATR", "RSI", "Bollinger_Upper", "Bollinger_Lower",
            "SMA_10", "SMA_50", "SMA_20", "EMA12", "EMA26",
            "MACD", "Signal_Line", "MACD_Histogram", "momentum"
        ], inplace=True)        



        # Mantém apenas a última linha válida para análise atual
        df = df.tail(1).copy()

        return df

    except Exception as e:
        logging.error(f"❌ Erro ao calcular os indicadores técnicos: {e}")
        return None  # 🔹 Retorna None para evitar falha no resto do código

# 🔹 Certifica-se de que o `try-except` está corretamente finalizado antes de começar a próxima função
def calcular_lote(saldo, preco_atual, atr, risk_percentage=0.4):
    """
    Calcula o tamanho do lote com base no saldo, no preço atual e no ATR.
    """
    if atr is None or atr <= 0:
        logging.warning("⚠️ ATR inválido. Não é possível calcular o tamanho do lote.")
        return None

    risco_por_trade = saldo * (risk_percentage / 100)
    stop_loss = atr  # Usando ATR como distância do stop
    tamanho_lote = risco_por_trade / stop_loss  # Tamanho do lote com base no risco
    return max(tamanho_lote, 0.001)  # Garante que o lote nunca seja menor que 0.001

# ✅ Teste do módulo
if __name__ == "__main__":
    # Simulação de dados históricos
    np.random.seed(42)
    data = {
        "time": pd.date_range(start="2023-01-01", periods=100, freq="D"),
        "close": np.random.uniform(low=100, high=200, size=100),
        "high": np.random.uniform(low=150, high=250, size=100),
        "low": np.random.uniform(low=50, high=100, size=100),
        "volume": np.random.uniform(low=1000, high=10000, size=100),
    }
    df = pd.DataFrame(data)

    # ✅ Calculando indicadores técnicos
    df = calculate_indicators(df)

    if df is not None and 'ATR' in df.columns:
        print(df.tail(5))  # 🔍 Exibe todas as colunas disponíveis no DataFrame

        saldo = 1000
        preco_atual = df['close'].iloc[-1]
        atr = df['ATR'].iloc[-1]
        tamanho_lote = calcular_lote(saldo, preco_atual, atr)

        print(f"Tamanho do Lote Calculado: {tamanho_lote}")
    else:
        logging.error("🚨 ERRO: O cálculo dos indicadores falhou. Nenhuma saída será gerada.")
