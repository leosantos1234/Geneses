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
DATA_PATH = "market_data_ETHUSDT_5m.csv"

# Caminhos finais dos arquivos treinados
MODEL_PATH = "C:/Users/emgepron/Documents/robo/estrategias/lstm_eth_next.keras"
SCALER_FEATURES_PATH = "C:/Users/emgepron/Documents/robo/estrategias/scaler_eth_features.pkl"
SCALER_TARGET_PATH = "C:/Users/emgepron/Documents/robo/estrategias/scaler_eth_target.pkl"


# =============================================================================
# 1. Carregar dados
# =============================================================================
print("Carregando dataset:", DATA_PATH)
df = pd.read_csv(DATA_PATH)

features = ["open", "high", "low", "close", "volume"]

# Garantir ordem temporal correta antes de criar target
if "time" in df.columns:
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).sort_values("time").reset_index(drop=True)
    df = df.drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    print("Ordem temporal após sort:", "inc=", df["time"].is_monotonic_increasing, "dec=", df["time"].is_monotonic_decreasing)

# Higienização numérica das features
df[features] = df[features].apply(pd.to_numeric, errors="coerce")
df = df.dropna(subset=features).reset_index(drop=True)

# Criar coluna target = próximo fechamento (após ordenação temporal)
df["target_next_close"] = df["close"].shift(-1)
df = df.dropna(subset=["target_next_close"]).reset_index(drop=True)

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
    shuffle=False,
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
