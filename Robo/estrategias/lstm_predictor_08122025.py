import os
import joblib
import numpy as np
from tensorflow.keras.models import load_model

def carregar_modelo(model_path, scaler_path):
    modelo = load_model(model_path)
    scaler = joblib.load(scaler_path)
    return modelo, scaler

def prever_lstm_label(df, modelo, scaler):
    resultado = prever_lstm(df, modelo, scaler)
    return "buy" if resultado > 0.5 else "sell"

def prever_lstm(df, modelo, scaler):
    """
    df: DataFrame Pandas OU array numpy (últimos candles, colunas idênticas ao treino)
    modelo: modelo LSTM carregado
    scaler: scaler carregado
    """
    window_size = 10
    features = ["close", "high", "low", "volume"]  # Ou ["close", "volume"] se só treinou assim!
    
    # Aceita tanto DataFrame quanto np.array
    if isinstance(df, np.ndarray):
        df_np = df[-window_size:]
    else:
        df_pre = df[features].tail(window_size)
        if df_pre.shape[0] < window_size:
            raise ValueError(f"Precisa de pelo menos {window_size} candles para prever com o LSTM.")
        df_np = df_pre.values

    X = scaler.transform(df_np)
    X = X.reshape((1, X.shape[0], X.shape[1]))
    pred = modelo.predict(X)
    return float(pred[0][0])  # retorna valor entre 0 e 1 (probabilidade de alta)
