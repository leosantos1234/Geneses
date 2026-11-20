from utils.logger_config import configurar_logger_global
import logging
import os

logger = logging.getLogger(__name__)

import pandas as pd
import time
from utils.indicadores import calculate_indicators  # Importa o módulo de indicadores técnicos

# 📌 **Cálculo do ATR (Average True Range)**
def calcular_atr(df, period=14):
    df['TR'] = df[['high', 'low', 'close']].apply(lambda x: max(x['high'] - x['low'], 
                                                                abs(x['high'] - x['close']), 
                                                                abs(x['low'] - x['close'])), axis=1)
    df['ATR'] = df['TR'].rolling(window=period).mean()
    return df

# 📌 **Cálculo das Bandas de Bollinger**
def calcular_bollinger_bands(df, period=20):
    df['SMA'] = df['close'].rolling(window=period).mean()
    df['STD'] = df['close'].rolling(window=period).std()
    df['Bollinger_Upper'] = df['SMA'] + (df['STD'] * 2)
    df['Bollinger_Lower'] = df['SMA'] - (df['STD'] * 2)
    return df

# 📌 **Cálculo do Desvio Padrão da Volatilidade**
def calcular_desvio_padrao_volatilidade(df, period=20):
    df['Volatilidade'] = df['close'].rolling(window=period).std()
    return df


def calcular_volatilidade(df):
    """
    Volatilidade REAL baseada em ATR relativo (padrão profissional).
    Evita falso zero e calibra para cripto intraday.
    """
    try:
        if df is None or df.empty:
            raise ValueError("DataFrame vazio ou inválido.")

        # Requer ATR já calculado pelo calculate_indicators()
        if "ATR" not in df.columns or "close" not in df.columns:
            raise KeyError("ATR e close precisam estar no DataFrame antes do cálculo de volatilidade.")

        atr = df["ATR"].iloc[-1]
        preco = df["close"].iloc[-1]

        # Proteções totais
        if pd.isna(atr) or atr <= 0 or pd.isna(preco) or preco <= 0:
            logging.warning("⚠️ ATR inválido para volatilidade. Ajustando para mínimo seguro.")
            return 0.0001  # 0.01%

        # 🔥 Cálculo profissional
        volatilidade_relativa = atr / preco     # Ex.: 180 ATR / 88000 preço = 0.00204
        volatilidade_percentual = volatilidade_relativa * 100  # Ex.: 0.204%

        # Evitar retorno absurdo por bug
        if volatilidade_percentual <= 0:
            volatilidade_percentual = 0.0001

        logging.info(f"📊 Volatilidade real: {volatilidade_percentual:.4f}%")
        return volatilidade_percentual

    except Exception as e:
        logging.error(f"❌ Erro em calcular_volatilidade: {e}")
        return 0.0001  # fallback seguro



# 📌 **Avaliação do Mercado**
def avaliar_condicao_mercado(df):
    try:
        volatilidade = calcular_volatilidade(df)

        # Limiares profissionais para cripto (configuráveis por ativo via .env)
        limiar_min = float(os.getenv("VOLATILIDADE_MIN", 0.02))   # 0.02% — abaixo disso é "mercado morto"
        limiar_max = float(os.getenv("VOLATILIDADE_MAX", 5.0))    # 5% — acima disso é "mercado caótico"

        if limiar_min <= volatilidade <= limiar_max:
            logger.info(f"✅ Mercado propício! Volatilidade: {volatilidade:.3f}%")
            return True

        logger.warning(f"❌ Mercado desfavorável. Volatilidade: {volatilidade:.3f}%")
        return False

    except Exception as e:
        logger.error(f"❌ Erro ao avaliar mercado: {e}")
        return True  # fallback para não travar o robô


# 📌 **Monitorar Mercado e Aguardar se Necessário**
def monitorar_mercado(df, tempo_espera=30):
    while True:
        logger.info("🔍 Verificando mercado...")

        if 'Volatilidade' not in df.columns:
            df = calcular_desvio_padrao_volatilidade(df)

        if avaliar_condicao_mercado(df):
            logger.info("🚀 Condições favoráveis! Prosseguindo com as operações...")
            break
        else:
            logger.info(f"⏳ Aguardando {tempo_espera} segundos antes de verificar novamente...")
            time.sleep(tempo_espera)
