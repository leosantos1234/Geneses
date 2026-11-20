# analise/auto_retrain.py
import sqlite3
import pandas as pd
import numpy as np
import logging
from datetime import datetime
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping
from sklearn.preprocessing import MinMaxScaler
import joblib
import os

def auto_retrain_modelo(symbol="BTCUSDT", db_path="C:/Users/emgepron/Documents/robo/trades.db"):
    logging.basicConfig(filename=f"logs/retrain_{symbol}.log", level=logging.INFO, format="%(asctime)s - %(message)s")
    logging.info(f"🧬 Iniciando re-treino automático para {symbol}...")

    try:
        # === 1️⃣ Carregar dados reais de trades do banco ===
        conn = sqlite3.connect(db_path)
        query = f"""
            SELECT time, entry_price, exit_price, side, profit_pct, stop_loss, take_profit, rsi, atr, regime
            FROM trades
            WHERE symbol = '{symbol}'
            ORDER BY time ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            logging.warning("⚠️ Nenhum trade encontrado para esse ativo. Abortando retrain.")
            return {"status": "no_data"}

        # === 2️⃣ Pré-processamento ===
        df = df.dropna()
        df["target"] = (df["profit_pct"] > 0).astype(int)
        features = ["entry_price", "rsi", "atr", "stop_loss", "take_profit"]
        scaler = MinMaxScaler()
        X_scaled = scaler.fit_transform(df[features])
        y = df["target"]

        # === 3️⃣ Re-treinar modelo ===
        X_seq, y_seq = [], []
        window = 10
        for i in range(len(X_scaled) - window):
            X_seq.append(X_scaled[i:i+window])
            y_seq.append(y.iloc[i+window])
        X_seq, y_seq = np.array(X_seq), np.array(y_seq)

        model = Sequential([
            LSTM(64, return_sequences=True, input_shape=(window, X_seq.shape[2])),
            Dropout(0.2),
            LSTM(32),
            Dense(1, activation="sigmoid")
        ])

        model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
        es = EarlyStopping(monitor="val_loss", patience=5, restore_best_weights=True)
        model.fit(X_seq, y_seq, validation_split=0.2, epochs=40, batch_size=16, callbacks=[es], verbose=0)

        # === 4️⃣ Salvar novos arquivos de modelo e scaler ===
        base_name = symbol.lower().replace("usdt", "")
        model_dir = "C:/Users/emgepron/Documents/robo/estrategias"
        os.makedirs(model_dir, exist_ok=True)
        model_path = os.path.join(model_dir, f"lstm_{base_name}_4features_v2.h5")
        scaler_path = os.path.join(model_dir, f"scaler_{base_name}_v2.pkl")

        model.save(model_path)
        joblib.dump(scaler, scaler_path)

        logging.info(f"✅ Novo modelo salvo em {model_path}")
        logging.info(f"✅ Novo scaler salvo em {scaler_path}")
        return {"status": "success", "novo_modelo": model_path, "amostras": len(df)}

    except Exception as e:
        logging.error(f"❌ Erro no auto-retrain: {e}", exc_info=True)
        return {"status": "error", "mensagem": str(e)}
