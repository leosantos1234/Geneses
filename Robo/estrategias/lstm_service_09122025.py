# lstm_service.py
# ==========================================================
# Serviço LSTM Centralizado - compatível com Robô Fluxo
# ==========================================================

from fastapi import FastAPI, Request
from tensorflow.keras.models import load_model
import numpy as np
import uvicorn
import logging
import tensorflow as tf
import os
import sys
from dotenv import load_dotenv

# ==========================================================
# 🧩 Carregamento do .env dinâmico
# ==========================================================
try:
    dotenv_path = sys.argv[1] if len(sys.argv) > 1 else ".env"
    if not os.path.exists(dotenv_path):
        print(f"⚠️ .env não encontrado: {dotenv_path}")
    else:
        load_dotenv(dotenv_path=dotenv_path)
        print(f"🔗 Arquivo .env carregado: {dotenv_path}")
except Exception as e:
    print(f"⚠️ Falha ao carregar .env: {e}")

# ==========================================================
# 🧩 Configuração inicial
# ==========================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

tf.config.threading.set_intra_op_parallelism_threads(1)
tf.config.threading.set_inter_op_parallelism_threads(1)

app = FastAPI(title="Serviço Central LSTM - Robô Fluxo")

# ==========================================================
# 🧠 Carregamento do modelo LSTM
# ==========================================================
try:
    caminho_modelo = os.getenv("MODEL_PATH")
    assert caminho_modelo, "❌ MODEL_PATH não definido no .env"
    modelo = load_model(caminho_modelo)
    logger.info(f"✅ Modelo LSTM carregado de {caminho_modelo}")
except Exception as e:
    logger.error(f"❌ Falha ao carregar modelo LSTM: {e}")
    modelo = None

# ==========================================================
# 🔧 Normalização Min-Max adaptativa
# ==========================================================
def normalizar(janela):
    """
    Normaliza cada feature com min/max baseado na própria janela.
    """
    janela = np.array(janela, dtype=np.float32)

    min_vals = janela.min(axis=0)
    max_vals = janela.max(axis=0)

    janela_norm = (janela - min_vals) / (max_vals - min_vals + 1e-9)

    return janela_norm, min_vals, max_vals

# ==========================================================
# 🔄 Desnormalização para preço REAL
# ==========================================================
def desnormalizar_valor(pred_norm, min_vals, max_vals):
    """
    Converte a previsão normalizada para preço REAL do ativo.
    """
    return pred_norm * (max_vals[0] - min_vals[0]) + min_vals[0]


# ==========================================================
# 🚀 Endpoint principal de previsão
# ==========================================================
@app.post("/predict")
async def predict_lstm(request: Request):
    try:
        payload = await request.json()
        data = payload.get("data", None)

        # 🔒 1) Validação bruta do payload
        if data is None:
            logger.error("❌ Payload sem chave 'data'.")
            return {"erro": "Campo 'data' é obrigatório."}

        if not isinstance(data, list) or len(data) == 0:
            logger.error(f"❌ Payload 'data' vazio ou inválido: {type(data)} | {data}")
            return {"erro": "Campo 'data' deve ser uma lista de 10 linhas."}

        # Converte para array
        arr = np.array(data, dtype=np.float32)

        # 🔧 2) Caso venha achatado (vetor 1D) e tenha 40 valores, tentar reshapar
        if arr.ndim == 1:
            if arr.size == 40:
                logger.warning(f"⚠️ Recebido vetor 1D com 40 valores. Tentando reshapar para (10,4).")
                arr = arr.reshape(10, 4)
            else:
                logger.error(f"❌ Shape inválido 1D: {arr.shape} (tamanho {arr.size}, esperado 40 para reshape 10x4).")
                return {"erro": f"Shape inválido! Esperado (10,4), recebido {arr.shape}"}

        # 🔒 3) Checagem final de shape
        if arr.shape != (10, 4):
            logger.error(f"❌ Shape inválido: {arr.shape} (esperado 10,4)")
            return {"erro": f"Shape inválido! Esperado (10,4), recebido {arr.shape}"}

        # 🔒 4) NaNs
        if np.isnan(arr).any():
            logger.error("❌ Dados com NaN recebidos!")
            return {"erro": "Dados contém valores NaN."}

        if modelo is None:
            raise RuntimeError("Modelo LSTM não carregado.")

        # ==================================================
        # 🔧 Normalização
        # ==================================================
        janela_norm, min_vals, max_vals = normalizar(arr)
        X = janela_norm.reshape((1, 10, 4))

        # ==================================================
        # 🧠 Previsão normalizada
        # ==================================================
        y_pred_norm = float(modelo.predict(X)[0][0])

        # ==================================================
        # 💰 Previsão em preço REAL
        # ==================================================
        y_pred_real = float(desnormalizar_valor(y_pred_norm, min_vals, max_vals))

        logger.info(f"📈 Previsão normalizada: {y_pred_norm:.6f}")
        logger.info(f"💰 Preço previsto (REAL): {y_pred_real:.2f}")

        return {
            "prediction": y_pred_real,
            "raw": y_pred_norm,
        }

    except Exception as e:
        logger.error(f"❌ Erro durante a previsão LSTM: {e}")
        return {"erro": str(e)}



# ==========================================================
# ▶️ Execução direta
# ==========================================================
if __name__ == "__main__":
    port = int(os.getenv("LSTM_SERVICE_PORT", 8505))
    host = os.getenv("LSTM_SERVICE_HOST", "0.0.0.0")
    logger.info(f"🚀 Iniciando serviço LSTM na porta {port} ({host})")
    uvicorn.run(app, host=host, port=port)
