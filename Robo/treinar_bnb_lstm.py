import pandas as pd
import numpy as np
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from sklearn.preprocessing import MinMaxScaler
from estrategias.lstm_rnn import prepare_lstm_data, create_lstm_model
import joblib  # pra salvar o scaler

# ======================================================
# 🔧 Escolha o ativo que quer treinar
# ======================================================
ativo = "bnb"  # 👈 troque aqui o nome do ativo: btc, eth, bnb, sol, xrp, ltc etc.

# ======================================================
# 1️⃣ Carregar os dados históricos
# ======================================================
arquivo_csv = f"market_data_{ativo.upper()}USDT_5m.csv"
df = pd.read_csv(arquivo_csv)
df = df.dropna()
df = df.tail(8000)  # usa últimos 8.000 candles pra evitar MemoryError

# ======================================================
# 2️⃣ Normalizar os dados (close, high, low, volume)
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
# 4️⃣ Criar e treinar o modelo
# ======================================================
model = create_lstm_model((X.shape[1], X.shape[2]))

model.fit(
    X, y,
    epochs=10,           # aumente pra 50 se quiser maior precisão
    batch_size=32,
    verbose=1
)

# ======================================================
# 5️⃣ Salvar o modelo e o scaler
# ======================================================
modelo_path = f"estrategias/lstm_{ativo}_4features.h5"
scaler_path = f"estrategias/scaler_{ativo}.pkl"

model.save(modelo_path)
joblib.dump(scaler, scaler_path)

print(f"✅ Modelo salvo em: {modelo_path}")
print(f"✅ Scaler salvo em: {scaler_path}")
