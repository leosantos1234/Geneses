import os
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
import joblib

DATA_DIR = "C:/Users/emgepron/Documents/robo"
OUTPUT_DIR = os.path.join(DATA_DIR, "estrategias")
WINDOW = 50
EPOCHS = 25
BATCH_SIZE = 32

def criar_sequencias(data, window=50):
    X, y = [], []
    for i in range(window, len(data)):
        X.append(data[i-window:i])
        y.append(data[i, 3])  # preço de fechamento
    return np.array(X), np.array(y)

def treinar_modelo(arquivo_csv):
    nome_arquivo = os.path.basename(arquivo_csv)
    ativo = nome_arquivo.split("_")[2].replace("USDT", "").lower()
    print(f"\n🚀 Treinando modelo para {ativo.upper()}...")

    df = pd.read_csv(arquivo_csv)
    df = df[['open', 'high', 'low', 'close']].astype(float)

    scaler = MinMaxScaler()
    scaled_data = scaler.fit_transform(df)

    X, y = criar_sequencias(scaled_data, WINDOW)
    split = int(0.8 * len(X))
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=(X_train.shape[1], X_train.shape[2])),
        Dropout(0.2),
        LSTM(64),
        Dropout(0.2),
        Dense(1)
    ])

    model.compile(optimizer="adam", loss="mean_squared_error")
    model.fit(X_train, y_train, epochs=EPOCHS, batch_size=BATCH_SIZE, validation_data=(X_test, y_test), verbose=1)

    model_path = os.path.join(OUTPUT_DIR, f"lstm_{ativo}_4features.h5")
    scaler_path = os.path.join(OUTPUT_DIR, f"scaler_{ativo}.pkl")
    model.save(model_path)
    joblib.dump(scaler, scaler_path)

    print(f"✅ {ativo.upper()} salvo em:")
    print(f"   📂 {model_path}")
    print(f"   📂 {scaler_path}")

# Treina todos os arquivos CSV na pasta principal
for file in os.listdir(DATA_DIR):
    if file.startswith("market_data_") and file.endswith(".csv"):
        treinar_modelo(os.path.join(DATA_DIR, file))

print("\n🎯 Todos os modelos LSTM foram treinados com sucesso!")
