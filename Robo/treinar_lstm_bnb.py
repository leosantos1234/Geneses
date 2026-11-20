import pandas as pd
import numpy as np
import pickle
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")


# =============================================================================
# CONFIGURAÇÕES DO USUÁRIO
# =============================================================================
DATA_PATH = "market_data_BNBUSDT_5m.csv"

# Caminhos finais dos arquivos treinados
MODEL_PATH = "C:/Users/emgepron/Documents/robo/estrategias/lstm_bnb_next.keras"
SCALER_FEATURES_PATH = "C:/Users/emgepron/Documents/robo/estrategias/scaler_bnb_features.pkl"
SCALER_TARGET_PATH = "C:/Users/emgepron/Documents/robo/estrategias/scaler_bnb_target.pkl"


# =============================================================================
# 1. Carregar dados
# =============================================================================
print("Carregando dataset:", DATA_PATH)
df = pd.read_csv(DATA_PATH)

# Criar coluna target = próximo fechamento
df["target_next_close"] = df["close"].shift(-1)
df.dropna(inplace=True)

features = ["open", "high", "low", "close", "volume"]

# Normalizadores
scaler_features = MinMaxScaler()
scaler_target = MinMaxScaler()

X_scaled = scaler_features.fit_transform(df[features])
y_scaled = scaler_target.fit_transform(df[["target_next_close"]])


# =============================================================================
# 2. Criar janelas (10 candles)
# =============================================================================
def criar_janelas(data, target, janela=10):
    X, y = [], []
    for i in range(len(data) - janela):
        X.append(data[i:i+janela])
        y.append(target[i+janela])
    return np.array(X), np.array(y)


X, y = criar_janelas(X_scaled, y_scaled, janela=10)

print(f"Dataset criado: X={X.shape}, y={y.shape}")


# =============================================================================
# 3. Criar modelo LSTM
# =============================================================================
def criar_modelo(janela=10, features=5):
    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=(janela, features)),
        Dropout(0.2),

        LSTM(32),
        Dropout(0.2),

        Dense(16, activation="relu"),
        Dense(1, activation="linear"),
    ])

    model.compile(optimizer="adam", loss="mse")
    return model


print("Criando modelo LSTM...")
model = criar_modelo()


# =============================================================================
# 4. Treinar
# =============================================================================
print("Iniciando treinamento...")
history = model.fit(
    X, y,
    epochs=25,
    batch_size=32,
    validation_split=0.1,
    verbose=1
)


# =============================================================================
# 5. Salvar modelo e scalers
# =============================================================================
print("Salvando modelo e scalers...")

# Criar diretório se não existir
os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)

model.save(MODEL_PATH)

with open(SCALER_FEATURES_PATH, "wb") as f:
    pickle.dump(scaler_features, f)

with open(SCALER_TARGET_PATH, "wb") as f:
    pickle.dump(scaler_target, f)

print("\n===================================")
print("TREINAMENTO CONCLUIDO COM SUCESSO!")
print("Modelo salvo em:", MODEL_PATH)
print("Scaler Features salvo em:", SCALER_FEATURES_PATH)
print("Scaler Target salvo em:", SCALER_TARGET_PATH)
print("===================================\n")
