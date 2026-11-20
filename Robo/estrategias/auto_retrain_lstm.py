"""Retreinamento seguro de um LSTM a partir dos candles atuais da OKX.

Este processo nunca grava diretamente nos artefatos em produção. Ele treina,
valida e publica um candidato versionado. O lstm_service.py decide quando
recarregar esse candidato.
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras import callbacks, layers, models
from tensorflow.keras import backend as K


FEATURES = ["open", "high", "low", "close", "volume"]
WINDOW = 10
ASSETS = {
    "btc": "BTC-USDT-SWAP",
    "eth": "ETH-USDT-SWAP",
    "sol": "SOL-USDT-SWAP",
    "bnb": "BNB-USDT-SWAP",
    "xrp": "XRP-USDT-SWAP",
}


def fetch_candles(inst_id: str, limit: int) -> pd.DataFrame:
    # O worker limpa os proxies antes de iniciar o robô, mas o serviço LSTM
    # pode ser iniciado antes dessa limpeza. Evite herdar proxy local inválido
    # (por exemplo 127.0.0.1:9) durante o retreinamento.
    session = requests.Session()
    session.trust_env = False
    response = session.get(
        "https://www.okx.com/api/v5/market/candles",
        params={"instId": inst_id, "bar": os.getenv("LSTM_RETRAIN_BAR", "5m"), "limit": str(limit)},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise RuntimeError(f"OKX retornou dados invalidos para {inst_id}: {payload}")
    rows = [
        {
            "time": pd.to_datetime(int(row[0]), unit="ms", utc=True),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in payload["data"]
    ]
    return pd.DataFrame(rows).sort_values("time").drop_duplicates("time").reset_index(drop=True)


def make_windows(features: np.ndarray, target: np.ndarray):
    x_values, y_values = [], []
    for index in range(len(features) - WINDOW):
        x_values.append(features[index : index + WINDOW])
        y_values.append(target[index + WINDOW])
    return np.asarray(x_values, dtype=np.float32), np.asarray(y_values, dtype=np.float32)


def build_model() -> models.Model:
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
    return model


def train(asset: str, output_dir: Path, limit: int, epochs: int) -> dict:
    asset = asset.lower()
    if asset not in ASSETS:
        raise ValueError(f"Ativo nao suportado: {asset}")
    df = fetch_candles(ASSETS[asset], limit)
    if len(df) < WINDOW + 60:
        raise RuntimeError(f"Dados insuficientes: {len(df)} candles")

    df["target_next_close"] = df["close"].shift(-1)
    df = df.dropna().reset_index(drop=True)
    split = max(WINDOW + 20, int(len(df) * 0.8))
    train_df = df.iloc[:split]
    # O modelo continua sendo validado em dados posteriores, mas os scalers
    # precisam cobrir o regime atual inteiro. Se forem ajustados apenas nos
    # 80% mais antigos, o preço atual fica artificialmente fora da faixa.
    scaler_features = MinMaxScaler().fit(df[FEATURES])
    scaler_target = MinMaxScaler().fit(df[["target_next_close"]])
    x_scaled = scaler_features.transform(df[FEATURES])
    y_scaled = scaler_target.transform(df[["target_next_close"]])
    x, y = make_windows(x_scaled, y_scaled)
    train_count = max(1, split - WINDOW)

    model = build_model()
    stop = callbacks.EarlyStopping(monitor="val_loss", patience=4, restore_best_weights=True)
    model.fit(
        x[:train_count],
        y[:train_count],
        epochs=epochs,
        batch_size=32,
        validation_split=0.2,
        shuffle=False,
        callbacks=[stop],
        verbose=0,
    )

    validation_x = x[train_count:]
    validation_y = y[train_count:]
    if len(validation_x) == 0:
        raise RuntimeError("Conjunto de validacao vazio")
    predicted = scaler_target.inverse_transform(model.predict(validation_x, verbose=0)).ravel()
    actual = scaler_target.inverse_transform(validation_y).ravel()
    mape = float(np.mean(np.abs((actual - predicted) / np.maximum(np.abs(actual), 1e-8))) * 100)
    if not np.isfinite(mape):
        raise RuntimeError("MAPE invalido")
    max_mape = float(os.getenv("LSTM_RETRAIN_MAX_MAPE_PCT", "5.0"))
    if mape > max_mape:
        raise RuntimeError(f"MAPE {mape:.3f}% acima do limite {max_mape:.3f}%")

    candidate = output_dir / asset
    candidate.mkdir(parents=True, exist_ok=True)
    model_path = candidate / f"lstm_{asset}_next.keras"
    features_path = candidate / f"scaler_{asset}_features.pkl"
    target_path = candidate / f"scaler_{asset}_target.pkl"
    model.save(model_path)
    with features_path.open("wb") as file:
        pickle.dump(scaler_features, file)
    with target_path.open("wb") as file:
        pickle.dump(scaler_target, file)
    manifest = {
        "asset": asset,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candles": len(df),
        "bar": os.getenv("LSTM_RETRAIN_BAR", "5m"),
        "mape_pct": mape,
        "model": str(model_path),
        "features_scaler": str(features_path),
        "target_scaler": str(target_path),
    }
    (candidate / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    K.clear_session()
    print(json.dumps(manifest))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=int(os.getenv("LSTM_RETRAIN_CANDLES", "2000")))
    parser.add_argument("--epochs", type=int, default=int(os.getenv("LSTM_RETRAIN_EPOCHS", "25")))
    args = parser.parse_args()
    try:
        train(args.asset, Path(args.output_dir), args.limit, args.epochs)
        return 0
    except Exception as exc:
        print(f"AUTO_RETRAIN_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
