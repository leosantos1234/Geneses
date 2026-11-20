from tensorflow.keras.models import Sequential, load_model
from tensorflow.keras.layers import LSTM, Dense, Dropout
import numpy as np

def prepare_lstm_data(df, window_size):
    """
    Prepara dados para o modelo LSTM (4 features).
    """
    features = ['close', 'high', 'low', 'volume']
    data = []
    labels = []
    for i in range(len(df) - window_size):
        data.append(df.iloc[i:i + window_size][features].values)
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




def execute_lstm(df, config):
    """
    Executa a estratégia LSTM com proteção automática para dados incompletos.
    """
    import logging
    logging.basicConfig(level=logging.INFO)
    import numpy as np
    from tensorflow.keras.models import load_model

    # === 1️⃣ Garantir colunas esperadas ===
    expected_cols = ['close', 'high', 'low', 'volume']
    for col in expected_cols:
        if col not in df.columns:
            logging.warning(f"⚠️ Coluna ausente no DataFrame: '{col}'. Usando fallback com 'close'.")
            df[col] = df['close']

    # === 2️⃣ Padronizar ordem e remover valores ausentes ===
    df = df[expected_cols].copy()
    if df.isnull().any().any():
        logging.warning("⚠️ Dados com valores NaN detectados. Substituindo por último valor válido (ffill).")
        df.fillna(method='ffill', inplace=True)
        df.fillna(method='bfill', inplace=True)

    # === 3️⃣ Carregar modelo e preparar dados ===
    model = load_model(config['lstm_model_path'])
    window_size = config['window_size']

    if len(df) <= window_size:
        logging.error(f"❌ Dados insuficientes para LSTM (necessário ≥ {window_size} candles).")
        return "hold"

    X, _ = prepare_lstm_data(df, window_size)
    if X.size == 0:
        logging.error("❌ Erro ao preparar dados para o LSTM. Vetor X está vazio.")
        return "hold"

    # === 4️⃣ Fazer previsão ===
    prediction = model.predict(X[-1].reshape(1, -1, X.shape[2]), verbose=0)
    predicted_value = float(prediction[0][0])

    logging.info(f"🔮 Previsão LSTM: {predicted_value:.6f}")
    return "buy" if predicted_value > 0.5 else "sell"

