import os
import pickle
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras import callbacks, layers, models
from tensorflow.keras import backend as K


ROBO_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(ROBO_DIR, "estrategias")
BACKUP_DIR = os.path.join(
    ROBO_DIR, "backup_modelos_lstm_" + datetime.now().strftime("%Y%m%d_%H%M%S")
)
FEATURES = ["open", "high", "low", "close", "volume"]
WINDOW = 10
LIMIT = 300
EPOCHS = 25
BATCH_SIZE = 32
ASSETS = {
    "btc": "BTC-USDT-SWAP",
    "eth": "ETH-USDT-SWAP",
    "sol": "SOL-USDT-SWAP",
    "bnb": "BNB-USDT-SWAP",
    "xrp": "XRP-USDT-SWAP",
}


def buscar_candles(inst_id: str) -> pd.DataFrame:
    response = requests.get(
        "https://www.okx.com/api/v5/market/candles",
        params={"instId": inst_id, "bar": "5m", "limit": str(LIMIT)},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise RuntimeError(f"OKX retornou dados invalidos para {inst_id}: {payload}")

    rows = []
    for row in payload["data"]:
        rows.append(
            {
                "time": pd.to_datetime(int(row[0]), unit="ms"),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
        )
    return pd.DataFrame(rows).sort_values("time").drop_duplicates("time").reset_index(drop=True)


def criar_janelas(features, target):
    x_values, y_values = [], []
    for index in range(len(features) - WINDOW):
        x_values.append(features[index : index + WINDOW])
        y_values.append(target[index + WINDOW])
    return np.asarray(x_values, dtype=np.float32), np.asarray(y_values, dtype=np.float32)


def treinar_ativo(nome: str, inst_id: str):
    df = buscar_candles(inst_id)
    if len(df) < WINDOW + 50:
        raise RuntimeError(f"Dados insuficientes para {nome.upper()}: {len(df)} candles")

    df["target_next_close"] = df["close"].shift(-1)
    df = df.dropna().reset_index(drop=True)

    scaler_features = MinMaxScaler()
    scaler_target = MinMaxScaler()
    x_scaled = scaler_features.fit_transform(df[FEATURES])
    y_scaled = scaler_target.fit_transform(df[["target_next_close"]])
    x, y = criar_janelas(x_scaled, y_scaled)

    model = models.Sequential(
        [
            layers.Input(shape=(WINDOW, len(FEATURES))),
            layers.LSTM(64, return_sequences=True),
            layers.Dropout(0.2),
            layers.LSTM(32),
            layers.Dropout(0.2),
            layers.Dense(16, activation="relu"),
            layers.Dense(1),
        ]
    )
    model.compile(optimizer="adam", loss="mse")
    stop = callbacks.EarlyStopping(monitor="val_loss", patience=4, restore_best_weights=True)
    model.fit(
        x,
        y,
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        validation_split=0.2,
        shuffle=False,
        callbacks=[stop],
        verbose=1,
    )

    model_path = os.path.join(MODEL_DIR, f"lstm_{nome}_next.keras")
    features_path = os.path.join(MODEL_DIR, f"scaler_{nome}_features.pkl")
    target_path = os.path.join(MODEL_DIR, f"scaler_{nome}_target.pkl")
    for path in (model_path, features_path, target_path):
        if os.path.exists(path):
            os.makedirs(BACKUP_DIR, exist_ok=True)
            os.replace(path, os.path.join(BACKUP_DIR, os.path.basename(path)))

    model.save(model_path)
    with open(features_path, "wb") as file:
        pickle.dump(scaler_features, file)
    with open(target_path, "wb") as file:
        pickle.dump(scaler_target, file)

    print(
        f"OK {nome.upper()}: {len(df)} candles | "
        f"volume=[{df.volume.min():.4f}, {df.volume.max():.4f}] | "
        f"modelo={model_path}"
    )
    K.clear_session()


def main():
    os.makedirs(MODEL_DIR, exist_ok=True)
    for nome, inst_id in ASSETS.items():
        print(f"\nTreinando {nome.upper()} com {inst_id}...")
        treinar_ativo(nome, inst_id)
    print(f"\nTreinamento concluido. Backup dos modelos anteriores: {BACKUP_DIR}")


if __name__ == "__main__":
    main()
