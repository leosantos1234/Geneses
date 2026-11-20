import os
from dotenv import load_dotenv  # <--- não esqueça de importar

import requests
import logging
from estrategias.lstm_predictor import carregar_modelo, prever_lstm

# Carrega variáveis do .env (procura no diretório corrente)
load_dotenv()

# ✅ Caminhos fixos do modelo e scaler (não dependem do .env)
MODEL_PATH = os.getenv("MODEL_PATH")
SCALER_PATH = os.getenv("SCALER_PATH")

# ✅ Verificações automáticas (para garantir que os arquivos existem)
assert os.path.isfile(MODEL_PATH), f"❌ Arquivo do modelo LSTM não encontrado: {MODEL_PATH}"
assert os.path.isfile(SCALER_PATH), f"❌ Arquivo do scaler não encontrado: {SCALER_PATH}"

print(f"🧠 Modelo LSTM encontrado: {MODEL_PATH}")
print(f"📏 Scaler encontrado: {SCALER_PATH}")

# Carrega o modelo normalmente
modelo, scaler = carregar_modelo(MODEL_PATH, SCALER_PATH)

# URL da IA remota
LSTM_API_URL = os.getenv("LSTM_API_URL", "http://localhost:8505")
print("DEBUG LSTM_API_URL =", LSTM_API_URL)


def obter_previsao_lstm(df):
    df = df.copy()

    if "time" in df.columns:
        df["time"] = df["time"].astype(str)

    cols_lstm = ["close", "high", "low", "volume"]
    ultimos = df[cols_lstm].tail(10).copy()

    # Dados para API
    dados = {"data": ultimos.values.tolist()}

    # === 1) API REMOTA ===
    try:
        url_predict = f"{LSTM_API_URL}/predict"
        logging.info(f"Chamando LSTM em {url_predict}")

        resp = requests.post(url_predict, json=dados, timeout=3)

        if resp.status_code == 200:
            data = resp.json()
            if "prediction" in data:
                previsao = float(data["prediction"])
                logging.info(f"📡 Previsão via API LSTM: {previsao}")
                return previsao
        else:
            logging.warning(f"⚠️ Erro HTTP {resp.status_code} ao consultar API LSTM.")
    except Exception as e:
        logging.warning(f"⚠️ LSTM API indisponível: {e}. Usando fallback local.")

    # === 2) FALLBACK LOCAL (CORRETO) ===
    try:
        df_local = ultimos.copy()
        previsao_local = prever_lstm(df_local, modelo, scaler)
        logging.info(f"🧠 Previsão local (fallback): {previsao_local}")
        return previsao_local
    except Exception as e:
        logging.error(f"❌ Falha crítica no fallback LSTM local: {e}")
        return None
