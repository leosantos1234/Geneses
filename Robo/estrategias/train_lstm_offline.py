import pandas as pd
from estrategias.lstm_rnn import train_lstm
import joblib
import os

# 1. Caminho do arquivo histórico de candles (ajuste se necessário)
csv_path = "C:\\Users\\emgepron\\Documents\\robo\\btc_historico.csv"

# 2. Caminhos de saída dos arquivos treinados
model_path = "C:\\Users\\emgepron\\Documents\\robo\\estrategias\\lstm_btcusdt.h5"
scaler_path = "C:\\Users\\emgepron\\Documents\\robo\\estrategias\\lstm_scaler.pkl"

# 3. Carregue seu histórico de candles
df = pd.read_csv(csv_path)

# (Opcional) Verifique se as colunas batem
print("Colunas disponíveis:", df.columns)

# 4. Treine o modelo LSTM uma única vez
model, scaler = train_lstm(df)  # Função já existente no seu projeto

# 5. Salve o modelo treinado e o scaler
model.save(model_path)
joblib.dump(scaler, scaler_path)

print("✅ LSTM treinado e salvo com sucesso!")
print(f"Modelo salvo em: {model_path}")
print(f"Scaler salvo em: {scaler_path}")
