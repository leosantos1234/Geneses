from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
import numpy as np
import logging
import os

def prepare_lstm_data(df, window_size):
    """
    Prepara dados para o modelo LSTM.
    """
    data = []
    labels = []
    for i in range(len(df) - window_size):
        data.append(df.iloc[i:i + window_size][['close', 'volume']].values)
        labels.append(1 if df.iloc[i + window_size]['close'] > df.iloc[i + window_size - 1]['close'] else 0)
    return np.array(data), np.array(labels)

def create_lstm_model(input_shape):
    """
    Cria e retorna um modelo LSTM.
    """
    model = Sequential([
        LSTM(50, return_sequences=True, input_shape=input_shape),
        Dropout(0.2),
        LSTM(50),
        Dropout(0.2),
        Dense(1, activation='sigmoid')  # Saída binária: 'buy' ou 'sell'
    ])
    model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
    return model

# Carregar modelo apenas uma vez
logging.info("Carregando o modelo LSTM...")


# ======= Carregar modelo LSTM usando o .env =======
try:
    caminho_modelo = os.getenv("MODEL_PATH")
    assert caminho_modelo, "MODEL_PATH não definido no .env"
    modelo_lstm = load_model(caminho_modelo)
    logging.info(f"Modelo LSTM carregado de {caminho_modelo}.")
except Exception as e:
    logging.warning(f"Falha ao carregar modelo {caminho_modelo}: {e}. Criando dummy.")
    modelo_lstm = create_lstm_model((10, 4))  # Ajuste window_size se necessário



def execute_lstm(dados, config):
    """
    Executa a estratégia baseada no modelo LSTM treinado.

    :param dados: DataFrame com os dados de entrada.
    :param config: Dicionário com configurações do modelo (caminho do modelo, janela de tempo).
    :return: 'Buy', 'Sell' ou 'Hold'
    """

    try:
        window_size = config["window_size"]
        features = ["close", "high", "low", "volume"]
        X = np.array(dados[features].iloc[-window_size:].values)
        if X.shape[0] < window_size:
            logging.warning(f"Poucos dados ({X.shape[0]}), exigido {window_size}. Retornando Hold.")
            return "Hold"
        X = X.reshape(1, X.shape[0], X.shape[1])
        prediction = modelo_lstm.predict(X)
        logging.info(f"LSTM Pred: {prediction}")
        return "Buy" if prediction > 0.5 else "Sell"
    except Exception as e:
        logging.error(f"Erro no LSTM: {e}")
        return "Hold"
