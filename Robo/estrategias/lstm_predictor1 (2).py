import numpy as np
import logging
from tensorflow.keras.models import load_model
import joblib

ultima_previsao_lstm = None


def carregar_modelo(model_path, scaler_path):
    """
    Carrega o modelo LSTM e o scaler, exatamente como o sistema antigo usava.
    """
    modelo = load_model(model_path)
    scaler = joblib.load(scaler_path)
    return modelo, scaler


def prever_lstm(df, modelo, scaler):
    """
    Previsão LSTM estável:
    - Sempre retorna um PREÇO REAL.
    - Nunca retorna probabilidade (0–1).
    - Nunca retorna None.
    - Nunca gera erro silencioso.
    """

    try:
        # 🔍 Garantir que existem ao menos 10 linhas (janela)
        if df.shape[0] < 10:
            logging.error(f"❌ Janela insuficiente para LSTM: {df.shape}")
            return float("nan")

        # 🔍 Usar apenas as features corretas (como no treino original)
        features = ["close", "high", "low", "volume"]

        if not all(f in df.columns for f in features):
            logging.error(f"❌ Features ausentes no DF para LSTM: {df.columns}")
            return float("nan")

        janela = df[features].tail(10).values

        # Aplicar normalização
        janela_scaled = scaler.transform(janela)

        # Criar batch (1,10,4)
        X = np.expand_dims(janela_scaled, axis=0)

        # Previsão normalizada
        pred_scaled = modelo.predict(X, verbose=0)[0][0]

        # Reverter normalização para PREÇO REAL
        # Criamos vetor-completo apenas com valor previsto
        dummy = np.zeros((1, len(features)))
        dummy[0][0] = pred_scaled  # posição do "close"
        pred_real = scaler.inverse_transform(dummy)[0][0]


        global ultima_previsao_lstm
        ultima_previsao_lstm = float(pred_real)


        return float(pred_real)

    except Exception as e:
        logging.error(f"❌ Erro ao prever LSTM: {e}")
        return float("nan")
