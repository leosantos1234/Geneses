import pandas as pd
import numpy as np
import logging
import os

# Leitura dos parâmetros do .env (com valores padrão)
SMA_CURTA = int(os.getenv("SMA_CURTA", 10))
SMA_LONGA = int(os.getenv("SMA_LONGA", 50))
SMA_BB = int(os.getenv("SMA_BB", 20))
BB_STD = float(os.getenv("BB_STD", 2))
RSI_WINDOW = int(os.getenv("RSI_WINDOW", 14))
RSI_OVERBOUGHT = int(os.getenv("RSI_OVERBOUGHT", 70))
RSI_OVERSOLD = int(os.getenv("RSI_OVERSOLD", 30))
ATR_WINDOW = int(os.getenv("ATR_WINDOW", 14))
EMA_FAST = int(os.getenv("EMA_FAST", 12))
EMA_SLOW = int(os.getenv("EMA_SLOW", 26))
EMA_TREND = int(os.getenv("EMA_TREND", 200))
EMA_50 = int(os.getenv("EMA_50", 50))
MOMENTUM_PERIOD = int(os.getenv("MOMENTUM_PERIOD", 10))
VOLUME_MEDIA = int(os.getenv("VOLUME_MEDIA", 20))
MACD_SIGNAL = int(os.getenv("MACD_SIGNAL", 9))
RISK_PERCENTAGE = float(os.getenv("RISK_PERCENTAGE", 0.4))

logger = logging.getLogger(__name__)

def calculate_indicators(df):
    """
    Calcula Indicadores Técnicos (parametrizáveis via .env):
    - SMA, Bollinger Bands, RSI, ATR, EMA, MACD, Resistance, Support, etc.
    """
    try:
        required_columns = ['close', 'high', 'low']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise KeyError(f"🚨 ERRO: As colunas {missing_columns} estão ausentes no DataFrame!")

        df[required_columns] = df[required_columns].apply(pd.to_numeric, errors='coerce')
        df.dropna(subset=required_columns, inplace=True)
        if df.empty:
            raise ValueError("🚨 ERRO: Dados insuficientes para calcular indicadores técnicos.")

        # 🔹 SMA
        df[f'SMA_{SMA_CURTA}'] = df['close'].rolling(window=SMA_CURTA, min_periods=1).mean()
        df[f'SMA_{SMA_LONGA}'] = df['close'].rolling(window=SMA_LONGA, min_periods=1).mean()

        # 🔹 Bollinger Bands
        df[f'SMA_BB'] = df['close'].rolling(window=SMA_BB, min_periods=1).mean()
        df['std_dev'] = df['close'].rolling(window=SMA_BB, min_periods=SMA_BB).std()
        df['Bollinger_Upper'] = df['SMA_BB'] + (BB_STD * df['std_dev'])
        df['Bollinger_Lower'] = df['SMA_BB'] - (BB_STD * df['std_dev'])

        logger.info(f"DataFrame após calcular Bollinger Bands:\n{df.tail(5)}")

        # 🔹 RSI
        delta = df['close'].diff()
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = pd.Series(gain).rolling(window=RSI_WINDOW, min_periods=1).mean()
        avg_loss = pd.Series(loss).rolling(window=RSI_WINDOW, min_periods=1).mean()
        rs = np.where(avg_loss == 0, 0, avg_gain / avg_loss)
        df['RSI'] = 100 - (100 / (1 + rs))

        # 🔹 ATR
        df['TrueRange'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(np.abs(df['high'] - df['close'].shift()), np.abs(df['low'] - df['close'].shift()))
        )
        df['ATR'] = df['TrueRange'].rolling(window=ATR_WINDOW, min_periods=1).mean()
        df['ATR'] = df['ATR'].bfill().ffill()

        # 🔹 Resistance/Support (janela 3)
        df['Resistance'] = df['high'].rolling(window=3, min_periods=1).max()
        df['Support'] = df['low'].rolling(window=3, min_periods=1).min()

        # 🔹 EMA
        df[f'EMA_{EMA_FAST}'] = df['close'].ewm(span=EMA_FAST, adjust=False).mean()
        df[f'EMA_{EMA_SLOW}'] = df['close'].ewm(span=EMA_SLOW, adjust=False).mean()
        df[f'EMA_{EMA_TREND}'] = df['close'].ewm(span=EMA_TREND, adjust=False).mean()
        df[f'EMA_{EMA_50}'] = df['close'].ewm(span=EMA_50, adjust=False).mean()

        # 🔹 Sempre crie as EMAs padrão para compatibilidade
        df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # 🔹 Volume médio
        df['Volume_Media'] = df['volume'].rolling(window=VOLUME_MEDIA, min_periods=1).mean()

        # 🔹 MACD
        df['MACD'] = df[f'EMA_{EMA_FAST}'] - df[f'EMA_{EMA_SLOW}']
        df['Signal_Line'] = df['MACD'].ewm(span=MACD_SIGNAL, adjust=False).mean()
        df['MACD_Histogram'] = df['MACD'] - df['Signal_Line']

        # 🔹 Momentum
        df['momentum'] = df['close'] - df['close'].shift(MOMENTUM_PERIOD)

        # 🔥 IMPORTANTE: limpar valores inválidos após todos os indicadores
        cols_indicadores = [
            f'SMA_{SMA_CURTA}', f'SMA_{SMA_LONGA}', 'SMA_BB', 'std_dev',
            'Bollinger_Upper', 'Bollinger_Lower', 'RSI', 'TrueRange', 'ATR',
            'Resistance', 'Support', f'EMA_{EMA_FAST}', f'EMA_{EMA_SLOW}',
            f'EMA_{EMA_TREND}', f'EMA_{EMA_50}', 'EMA12', 'EMA26', 'EMA50',
            'Volume_Media', 'MACD', 'Signal_Line', 'MACD_Histogram', 'momentum'
        ]

        df.dropna(subset=cols_indicadores, inplace=True)

        print("Indicadores técnicos calculados com sucesso.")
        return df
    

    except Exception as e:
        logging.error(f"❌ Erro ao calcular os indicadores técnicos: {e}")
        return None
    
    




def detectar_tendencia(df, coluna='close', periodo=SMA_CURTA):
    df["sma_curta"] = df[coluna].rolling(window=periodo).mean()
    df["sma_longa"] = df[coluna].rolling(window=periodo * 2).mean()
    if df["sma_curta"].iloc[-1] > df["sma_longa"].iloc[-1]:
        return "buy"
    else:
        return "sell"

def calcular_lote(saldo, preco_atual, atr, risk_percentage=RISK_PERCENTAGE):
    if atr is None or atr <= 0:
        logging.warning("⚠️ ATR inválido. Não é possível calcular o tamanho do lote.")
        return None
    risco_por_trade = saldo * (risk_percentage / 100)
    stop_loss = atr
    tamanho_lote = risco_por_trade / stop_loss
    return max(tamanho_lote, 0.001)

def calcular_volatilidade(df):
    try:
        if df is None or df.empty:
            raise ValueError("DataFrame de entrada está vazio ou inválido.")
        if "close" not in df.columns:
            raise KeyError("Coluna 'close' não encontrada no DataFrame.")
        df["retorno"] = df["close"].pct_change()
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


def preparar_df(df):
    df = df.copy()

    # 1) Ordenação obrigatória
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)

    # 2) Limpeza mínima antes dos indicadores
    df.dropna(subset=["close", "high", "low"], inplace=True)

    # 3) Indicadores completos
    df = calculate_indicators(df)

    # 4) Limpa NaNs criados pelas janelas
    df.dropna(inplace=True)

    return df


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

    # ===============================
    # 🔧 PREPARAÇÃO CENTRALIZADA
    # ===============================
    df_1m = preparar_df(df_1m)
    df_5m = preparar_df(df_5m)
    df_15m = preparar_df(df_15m)

    # ===============================
    # 📊 SCORE TÉCNICO – 5m
    # ===============================
    last_close = df_5m["close"].iloc[-1]

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

    # ===============================
    # 📈 TENDÊNCIA – 15m
    # ===============================
    resultado["tendencia_15m"] = detectar_tendencia(df_15m, "close", 20)

    # ===============================
    # 🔥 ROMPIMENTO – 1m
    # ===============================
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
