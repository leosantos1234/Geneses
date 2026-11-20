import numpy as np
import pandas as pd
import joblib
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from sklearn.preprocessing import MinMaxScaler

# ========= CONFIG =========
ARQUIVO = "xrp_5m.csv"
JANELA = 10
EPOCHS = 30
BATCH = 32

# Carrega dados
df = pd.read_csv(ARQUIVO)

# Features usadas no seu robô:
features = ["close", "high", "low", "volume"]
data = df[features].values

# Cria o scaler
scaler = MinMaxScaler()
scaled = scaler.fit_transform(data)

joblib.dump(scaler, "scaler_xrp.pkl")
print("📏 Scaler salvo: scaler_xrp.pkl")

# ======= Criar janelas =======
X = []
y = []

for i in range(JANELA, len(scaled)):
    X.append(scaled[i-JANELA:i])
    y.append(scaled[i][0])  # previsão do CLOSE

X = np.array(X)
y = np.array(y)

# ======= Criar modelo LSTM =======
model = Sequential([
    LSTM(64, return_sequences=True, input_shape=(JANELA, len(features))),
    Dropout(0.2),

    LSTM(64),
    Dropout(0.2),

    Dense(32, activation="relu"),
    Dense(1)
])

model.compile(optimizer="adam", loss="mse")
print(model.summary())

# ======= Treinar =======
print("🏋️‍♂️ Treinando modelo LSTM...")
history = model.fit(X, y, epochs=EPOCHS, batch_size=BATCH, validation_split=0.1)

# ======= Salvar modelo =======
model.save("lstm_xrp.keras", save_format="keras")
print("💾 Modelo salvo: lstm_xrp_4features.h5")

print("🎉 Treino finalizado!")
