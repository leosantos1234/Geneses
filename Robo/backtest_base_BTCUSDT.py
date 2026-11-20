import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

# Carrega .env com sua API_KEY, API_SECRET, TESTNET etc
load_dotenv()
API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
TESTNET = os.getenv("TESTNET", "False").lower() == "true"

# Setup da sessão Bybit
session = HTTP(testnet=TESTNET, api_key=API_KEY, api_secret=API_SECRET)

symbol = "BTCUSDT"
interval = "5"  # ou "1" para 1-minuto, "15", etc
limit_per_call = 1000

# Data final: agora
end_time = int(time.time())
# Data inicial: 1 ano atrás
start_time = int((datetime.now() - timedelta(days=30)).timestamp())

df_full = []

while True:
    response = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        start=start_time * 1000,  # ms
        end=end_time * 1000,      # ms
        limit=limit_per_call
    )
    data = response['result']['list']
    if not data:
        break
    data = sorted(data, key=lambda x: x[0])  # Ordena por timestamp

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume", "turnover"
    ])
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="ms")
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col])

    df_full.append(df)
    # Atualiza o end_time para a data do primeiro candle menos 1 minuto
    end_time = int(df["time"].iloc[0].timestamp()) - 60
    if len(data) < limit_per_call:
        break

# Junta todos os pedaços
df_result = pd.concat(df_full).sort_values("time").reset_index(drop=True)
df_result.to_csv("market_data_BTCUSDT_5min_1mes.csv", index=False)
print("✅ CSV salvo: market_data_BTCUSDT_5dia_1mes.csv")
