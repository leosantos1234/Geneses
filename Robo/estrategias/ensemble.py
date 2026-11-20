import logging
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from tensorflow.keras.models import load_model
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
import os
import tensorflow as tf

# Configurações para TensorFlow e logs
tf.get_logger().setLevel("ERROR")
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Variáveis globais para os modelos
lstm_model = None
rf_model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
svm_model = SVC(kernel="rbf", probability=True, random_state=42)

def carregar_modelos():
    """
    Carrega os modelos necessários para o ensemble.
    """
    global lstm_model, rf_model, svm_model
    try:
        lstm_model = load_model("estrategias/lstm_model.keras")
        logging.info("Modelos carregados com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao carregar os modelos: {e}")

def treinar_modelo(X, y):
    """
    Treina o modelo RandomForest e retorna o modelo treinado.
    """
    try:
        logging.info("Treinando modelo RandomForest...")
        modelo = RandomForestClassifier(random_state=42)
        modelo.fit(X, y)
        logging.info("Modelo RandomForest treinado com sucesso.")
        return modelo
    except Exception as e:
        logging.error(f"Erro ao treinar o modelo: {e}")
        return None




def prever_acoes(df):
    """
    Realiza previsões combinadas usando os modelos treinados.
    """
    try:
        X = df[["close", "volume", "SMA_10", "RSI", "ATR", "EMA_20"]].values

        # Previsões individuais
        rf_pred = rf_model.predict(X)
        svm_pred = svm_model.predict(X)

        # Combinação com pesos
        pesos = [0.6, 0.4]  # Ajuste conforme necessário
        previsoes = [
            int(pesos[0] * rf + pesos[1] * svm > 0.5)
            for rf, svm in zip(rf_pred, svm_pred)
        ]

        acuracia = accuracy_score(df["target"], previsoes)
        return previsoes, acuracia
    except Exception as e:
        logging.error(f"Erro ao realizar previsões: {e}")
        return None, None



