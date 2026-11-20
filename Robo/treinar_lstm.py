import sys
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from estrategias.lstm_rnn import prepare_lstm_data, create_lstm_model
import joblib

# ================================================
# 🚀 USO:
# python3 treinar_lstm.py ada
# python3 treinar_lstm.py sol
# python3 treinar_lstm.py link
# ================================================

if len(sys.argv) < 2:
    print("❌ Uso correto: python3 treinar_lstm.py <ativo>")
    sys.exit()

ativo = sys.argv[1].lower()
csv_name = f"market_data_{ativo.upper()}USDT_5m.csv"

print(f"📌 Treinando LSTM para o ativo: {ativo.upper()}")
print(f"📄 CSV: {csv_name}")

# ======================================================
# 1️⃣ Carregar os dados históricos
# ======================================================
df = pd.read_csv(csv_name)
df = df.dropna()
df = df.tail(8000)  # usa últimos candles

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
print(f"Shape X: {X.shape} | Shape y: {y.shape}")

# ======================================================
# 4️⃣ Criar e treinar o modelo
# ======================================================
model = create_lstm_model((X.shape[1], X.shape[2]))

model.fit(
    X, y,
    epochs=10,      # pode ajustar pra 30 ou 50 depois
    batch_size=32,
    verbose=1
)

# ======================================================
# 5️⃣ Salvar modelo e scaler
# ======================================================
modelo_path = f"estrategias/lstm_{ativo}_4features.h5"
scaler_path = f"estrategias/scaler_{ativo}.pkl"

model.save(modelo_path)
joblib.dump(scaler, scaler_path)

print("✅ Treinamento Concluído!")
print(f"📦 Modelo salvo em: {modelo_path}")
print(f"📦 Scaler salvo em: {scaler_path}")
