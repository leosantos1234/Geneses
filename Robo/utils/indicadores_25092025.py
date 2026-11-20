import pandas as pd
import numpy as np
import logging

# Configuração do logging
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
        logger = logging.getLogger(__name__)
        logger.info(f"DataFrame após calcular Bollinger Bands:\n{df.tail(5)}")

        # 🔹 RSI
        delta = df['close'].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(window=14, min_periods=1).mean()
        avg_loss = pd.Series(loss).rolling(window=14, min_periods=1).mean()
        rs = np.where(avg_loss == 0, 0, avg_gain / avg_loss)
        df['RSI'] = 100 - (100 / (1 + rs))

        # 🔹 ATR (Correção para evitar NaN nos primeiros períodos)
        # 🔹 ATR com backfill para robustez
        df['TrueRange'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(np.abs(df['high'] - df['close'].shift()), np.abs(df['low'] - df['close'].shift()))
        )
        df['ATR'] = df['TrueRange'].rolling(window=14, min_periods=1).mean()
        df['ATR'] = df['ATR'].bfill().ffill()


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

        print("Indicadores técnicos calculados com sucesso.")
        return df

    except Exception as e:
        logging.error(f"❌ Erro ao calcular os indicadores técnicos: {e}")
        return None  # 🔹 Retorna None para evitar falha no resto do código


#FUNÇÃO DE DETECTAR TENDENCIA
def detectar_tendencia(df, coluna='close', periodo=20):
    """
    Detecta tendência simples com base em média móvel.
    Retorna 'buy' se tendência de alta, 'sell' se tendência de baixa.
    """
    df["sma_curta"] = df[coluna].rolling(window=periodo).mean()
    df["sma_longa"] = df[coluna].rolling(window=periodo * 2).mean()

    if df["sma_curta"].iloc[-1] > df["sma_longa"].iloc[-1]:
        return "buy"
    else:
        return "sell"



# 🔹 Certifica-se de que o `try-except` está corretamente finalizado antes de começar a próxima função
def calcular_lote(saldo, preco_atual, atr, risk_percentage=1):
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


def calcular_volatilidade(df):
    try:
        if df is None or df.empty:
            raise ValueError("DataFrame de entrada está vazio ou inválido.")

        if "close" not in df.columns:
            raise KeyError("Coluna 'close' não encontrada no DataFrame.")

        df["retorno"] = df["close"].pct_change()

        # Remover NaNs gerados pelo pct_change()
        df = df.dropna(subset=["retorno"])

        if df.empty:
            raise ValueError("Sem dados suficientes para calcular a volatilidade.")

        vol = df["retorno"].std()

        if vol is None or pd.isna(vol) or vol == 0:
            raise ValueError(f"Volatilidade calculada inválida: {vol}")

        logging.info(f"Volatilidade calculada com sucesso: {vol:.6f}")
        return vol

    except Exception as e:
        logging.warning(f"Erro no cálculo de volatilidade: {e}")
        return None


def analisar_multiplos_timeframes(df_1m, df_5m, df_15m):
    """
    Analisa sinais combinando múltiplos timeframes:
    - Score técnico no 5m
    - Tendência no 15m
    - Rompimento no 1m
    """
    resultado = {
        "score_5m": 0,
        "rompimento_1m": False,
        "tendencia_15m": "neutra"
    }

    # ⚙️ Calcula os indicadores para o timeframe de 5m
    df_5m = calculate_indicators(df_5m)
    if df_5m is None:
        return resultado

    close = df_5m["close"]
    last_close = close.iloc[-1]

    # Critérios de pontuação técnica no 5m
    if df_5m["RSI"].iloc[-1] < 30 or df_5m["RSI"].iloc[-1] > 70:
        resultado["score_5m"] += 1

    if last_close > df_5m["Bollinger_Upper"].iloc[-1] or last_close < df_5m["Bollinger_Lower"].iloc[-1]:
        resultado["score_5m"] += 1

    if df_5m["volume"].iloc[-1] > df_5m["Volume_Media"].iloc[-1]:
        resultado["score_5m"] += 1

    if df_5m["momentum"].iloc[-1] > 0:
        resultado["score_5m"] += 1

    if df_5m["EMA12"].iloc[-1] > df_5m["EMA26"].iloc[-1]:
        resultado["score_5m"] += 1

    # 🔍 Detecta tendência no 15m
    resultado["tendencia_15m"] = detectar_tendencia(df_15m, coluna="close", periodo=20)

    # 🔥 Detecta rompimento no 1m
    df_1m = calculate_indicators(df_1m)
    if df_1m is not None:
        close_1m = df_1m["close"].iloc[-1]
        resistencia = df_1m["Resistance"].iloc[-2]
        suporte = df_1m["Support"].iloc[-2]

        if close_1m > resistencia or close_1m < suporte:
            resultado["rompimento_1m"] = True

    return resultado


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

    # 🔽 Limpeza de NaN logo após o cálculo dos indicadores
    if df is not None:
        nan_cols = df.columns[df.isna().any()].tolist()
        if nan_cols:
            logging.warning(f"⚠️ Antes da limpeza, colunas com NaN: {nan_cols}")

        df = df.dropna()

        nan_cols = df.columns[df.isna().any()].tolist()
        if nan_cols:
            logging.error(f"❌ Indicadores com valores NaN após limpeza: {nan_cols}")
        else:
            logging.info("✅ Nenhum indicador com NaN após limpeza.")

        if 'ATR' in df.columns and not df.empty:
            print(df.tail(5))  # 🔍 Exibe todas as colunas disponíveis no DataFrame

            saldo = 1000
            preco_atual = df['close'].iloc[-1]
            atr = df['ATR'].iloc[-1]
            tamanho_lote = calcular_lote(saldo, preco_atual, atr)

            print(f"Tamanho do Lote Calculado: {tamanho_lote}")
        else:
            logging.error("🚨 ERRO: DataFrame vazio ou sem coluna ATR após limpeza.")
    else:
        logging.error("🚨 ERRO: O cálculo dos indicadores falhou. Nenhuma saída será gerada.")
