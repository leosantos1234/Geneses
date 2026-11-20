import sys
import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# ==========================================
# USO:
# python baixar_historico.py ADAUSDT
# python baixar_historico.py AVAXUSDT
# ==========================================

if len(sys.argv) < 2:
    print("❌ Uso correto: python baixar_historico.py ADAUSDT")
    sys.exit()

symbol = sys.argv[1].upper()
print(f"📥 Baixando histórico para {symbol}...")

ENDPOINT = "https://api.bybit.com/v5/market/kline"
INTERVAL = "5"  # 5 minutos
LIMIT = 1000     # máximo permitido por chamada

# Baixar ~200 dias = 200 * 24 * 12 candles = 57.600 candles
TOTAL_CANDLES = 60000

def get_timestamp_ms(dt):
    return int(dt.timestamp() * 1000)

# Começar baixando agora
end_time = datetime.utcnow()
start_time = end_time - timedelta(days=200)

data = []

while True:
    start_ts = get_timestamp_ms(start_time)
    end_ts = get_timestamp_ms(end_time)

    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": INTERVAL,
        "start": start_ts,
        "end": end_ts,
        "limit": LIMIT
    }

    r = requests.get(ENDPOINT, params=params)
    json_data = r.json()

    if "result" not in json_data or "list" not in json_data["result"]:
        print("⚠️ Sem dados recebidos. Encerrando.")
        break

    candles = json_data["result"]["list"]

    if not candles:
        break

    data.extend(candles)

    # Atualiza fim para pegar próximo bloco
    newest = int(candles[-1][0])  # timestamp do último candle
    end_time = datetime.utcfromtimestamp(newest / 1000)

    print(f"⏳ Baixados {len(data)} candles até agora...")

    if len(candles) < LIMIT or len(data) >= TOTAL_CANDLES:
        break

    time.sleep(0.2)

# Organizar dataframe
df = pd.DataFrame(data, columns=[
    "time", "open", "high", "low", "close", "volume", "turnover"
])

df = df.sort_values("time")
df["time"] = pd.to_datetime(df["time"], unit="ms")

arquivo = f"market_data_{symbol}_5m.csv"
df.to_csv(arquivo, index=False)

print("✅ Histórico baixado com sucesso!")
print(f"📄 Arquivo salvo: {arquivo}")
print(f"📊 Total de candles: {len(df)}")
