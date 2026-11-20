import pandas as pd
import numpy as np
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from sklearn.preprocessing import MinMaxScaler
from estrategias.lstm_rnn import prepare_lstm_data, create_lstm_model

# ======================================================
# 1️⃣ Carregar ou criar seus dados históricos
# ======================================================
# Aqui você pode carregar de um CSV local ou via API Bybit
# Exemplo: arquivo com colunas ['time', 'open', 'high', 'low', 'close', 'volume']
df = pd.read_csv("market_data.csv")  # substitua pelo seu arquivo real

# Garantir que não haja NaN
df = df.dropna()
df = df.tail(10000)

# ======================================================
# 2️⃣ Normalizar os dados (ajuda o LSTM a aprender melhor)
# ======================================================
scaler = MinMaxScaler(feature_range=(0, 1))
df[['close', 'high', 'low', 'volume']] = scaler.fit_transform(df[['close', 'high', 'low', 'volume']])

# ======================================================
# 3️⃣ Preparar os dados para o modelo LSTM
# ======================================================
window_size = 30
X, y = prepare_lstm_data(df, window_size)
print(f"Shape de X: {X.shape} | Shape de y: {y.shape}")

# ======================================================
# 4️⃣ Criar e treinar o modelo LSTM com 4 features
# ======================================================
model = create_lstm_model((X.shape[1], X.shape[2]))

model.fit(
    X, y,
    epochs=10,           # pode aumentar para 50+ se quiser maior precisão
    batch_size=32,
    verbose=1
)

# ======================================================
# 5️⃣ Salvar o modelo treinado
# ======================================================
model.save("estrategias/lstm_btc_4features.h5")
print("✅ Modelo salvo com sucesso em: estrategias/lstm_btc_4features.h5")
