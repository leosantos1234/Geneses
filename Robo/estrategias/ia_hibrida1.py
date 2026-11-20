import logging
import os
from dotenv import load_dotenv
from estrategias.lstm_predictor import carregar_modelo, prever_lstm

load_dotenv()

MODEL_PATH = os.getenv("MODEL_PATH")
SCALER_PATH = os.getenv("SCALER_PATH")

# Carrega o modelo LSTM local uma vez
modelo, scaler = carregar_modelo(MODEL_PATH, SCALER_PATH)


def obter_previsao_lstm(df):
    """
    Função padronizada:
    - Sempre usa o modelo local.
    - Nunca consulta API remota.
    - Sempre retorna preço real.
    - Janela fixa (10 candles).
    """

    try:
        janela = df[["close", "high", "low", "volume"]].tail(10)

        if janela.shape != (10, 4):
            logging.error(f"❌ Janela LSTM inválida: {janela.shape}")
            return float("nan")

        # 🔥 Sempre usar o predictor correto
        previsao = prever_lstm(janela, modelo, scaler)

        if previsao != previsao:  # Checa NaN
            logging.error("❌ Previsão local retornou NaN")
            return float("nan")

        logging.info(f"🧠 Previsão LSTM (LOCAL): {previsao}")
        return float(previsao)

    except Exception as e:
        logging.error(f"❌ Erro crítico no LSTM híbrido: {e}")
        return float("nan")
