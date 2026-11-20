# treinar_lstm.py

import pandas as pd
from estrategias.lstm_rnn import create_lstm_model, prepare_lstm_data
from tensorflow.keras.models import save_model

# Simula dados históricos reais
from backtesting_geral1 import carregar_dados_historicos, session

SYMBOL = "BTCUSDT"
INTERVAL = "1"  # 1 minuto
WINDOW_SIZE = 30

# Carregar dados
df = carregar_dados_historicos(session, SYMBOL, INTERVAL)
if df is None or df.empty:
    print("Erro ao carregar dados.")
    exit()

# Preparar dados para o modelo
X, y = prepare_lstm_data(df[["close", "volume"]], WINDOW_SIZE)
if X is None or y is None or len(X) == 0:
    print("Erro ao preparar dados para LSTM.")
    exit()

# Criar e treinar modelo
model = create_lstm_model((WINDOW_SIZE, 2))
model.fit(X, y, epochs=15, batch_size=8, verbose=1)

# Salvar modelo
model.save("estrategias/lstm_btc.h5")
print("✅ Modelo salvo em 'estrategias/lstm_btc.h5'")
