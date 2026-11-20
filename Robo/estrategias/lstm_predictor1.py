import os
import joblib
import numpy as np
from tensorflow.keras.models import load_model


modelo_path = "C:/Users/emgepron/Documents/robo/estrategias/lstm_btc_4features.h5"
scaler_path = "C:/Users/emgepron/Documents/robo/estrategias/scaler_btc.pkl"

assert os.path.isfile(modelo_path), f"❌ MODELO_LSTM_PATH não encontrado: {modelo_path}"
assert os.path.isfile(scaler_path), f"❌ SCALER_PATH não encontrado: {scaler_path}"

def carregar_modelo(model_path, scaler_path):
    try:
        modelo = load_model(model_path)
    except Exception as e:
        print(f"Erro ao carregar modelo: {e}")
        modelo = None

    try:
        import joblib
        scaler = joblib.load(scaler_path)
    except Exception as e:
        print(f"Erro ao carregar scaler: {e}")
        scaler = None

    assert modelo is not None, "❌ Modelo não carregado!"
    assert scaler is not None, "❌ Scaler não carregado!"
    return modelo, scaler

modelo, scaler = carregar_modelo(modelo_path, scaler_path)







def prever_lstm_label(df, modelo, scaler):
    resultado = prever_lstm(df, modelo, scaler)
    return "buy" if resultado > 0.5 else "sell"

def prever_lstm(df, modelo, scaler):
    window_size = 10
    features = ["close", "high", "low", "volume"]  # Ajuste se precisar

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
    return float(pred[0][0])
