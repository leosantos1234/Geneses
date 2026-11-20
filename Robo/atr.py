import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def calculate_atr(df):
    """
    Função para calcular o ATR (Average True Range)
    """
    logger.info("Calculando ATR...")
    high_low = df['high'] - df['low']
    high_close = np.abs(df['high'] - df['close'].shift())
    low_close = np.abs(df['low'] - df['close'].shift())
    true_range = np.maximum(high_low, np.maximum(high_close, low_close))
    df['ATR'] = true_range.rolling(window=14).mean()
    return df

# Simulando dados históricos
data = {
    "time": pd.date_range(start="2023-01-01", periods=100, freq="D"),
    "close": np.random.uniform(low=100, high=200, size=100),
    "high": np.random.uniform(low=150, high=250, size=100),
    "low": np.random.uniform(low=50, high=100, size=100),
}

df = pd.DataFrame(data)

# Calculando o ATR
df = calculate_atr(df)

# Exibindo os últimos valores calculados
logging.info(f"Últimos valores de ATR:\n{df[['time', 'close', 'ATR']].tail()}")
