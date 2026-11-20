import uvicorn
from fastapi import FastAPI
import numpy as np
import tensorflow as tf
import joblib
import logging
import os

# =============================================
# CONFIGURAÇÕES
# =============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELO_PATH = os.path.join(BASE_DIR, "lstm_btc.keras")
SCALER_PATH = os.path.join(BASE_DIR, "scaler_btc.pkl")

# =============================================
# LOG
# =============================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============================================
# CARREGA MODELO E SCALER — EXATAMENTE COMO O LOCAL
# =============================================
logger.info(f"📦 Carregando modelo LSTM: {MODELO_PATH}")
model = tf.keras.models.load_model(MODELO_PATH)

logger.info(f"📦 Carregando scaler: {SCALER_PATH}")
scaler = joblib.load(SCALER_PATH)

# =============================================
# FASTAPI
# =============================================
app = FastAPI()

# =============================================
# FUNÇÃO DE PREVISÃO — IDÊNTICA AO MÓDULO LOCAL
# =============================================
def fazer_previsao_lstm(lista_precos):
    """
    lista_precos → lista de valores [close] normalizados
    Esse padrão já é usado no módulo local.
    """
    arr = np.array(lista_precos).reshape(-1, 1)

    # Normaliza como no arquivo local
    arr_scaled = scaler.transform(arr)

    # Prepara input (1, timesteps, 1)
    X = arr_scaled.reshape(1, arr_scaled.shape[0], 1)

    # Faz a previsão normalizada
    pred_scaled = model.predict(X, verbose=0)[0][0]

    # Inverte a normalização para preço REAL
    preco_real = scaler.inverse_transform([[pred_scaled]])[0][0]

    return float(preco_real)

# =============================================
# ENDPOINT
# =============================================
@app.post("/predict")
def predict(data: dict):
    try:
        lista_precos = data.get("prices", [])

        if not lista_precos:
            return {"erro": "lista 'prices' vazia"}

        resultado = fazer_previsao_lstm(lista_precos)

        return {
            "previsao": resultado
        }

    except Exception as e:
        logger.error(f"Erro na previsão: {e}")
        return {"erro": str(e)}

# =============================================
# EXECUÇÃO
# =============================================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8505)
