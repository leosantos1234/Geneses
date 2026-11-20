import os
import time
import pandas as pd
from pybit.unified_trading import HTTP
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Carrega variáveis do .env
load_dotenv()

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BYBIT_TESTNET = os.getenv("BYBIT_TESTNET", "False").lower() in ["1", "true", "yes"]

symbol = "BTCUSDT"
interval = "5"      # 5 minutos
filename = f"market_data_{symbol}_{interval}min_1mes.csv"

session = HTTP(api_key=API_KEY, api_secret=API_SECRET, testnet=BYBIT_TESTNET)

# Define intervalo de datas (últimos 31 dias)
dt_end = datetime.utcnow()
dt_start = dt_end - timedelta(days=365)

candles = []
limit = 1000  # Máximo permitido por requisição

print(f"🔎 Baixando candles {symbol} de {dt_start} até {dt_end} (intervalo: {interval}min)...")
t0 = time.time()

current = int(dt_start.timestamp())

while current < int(dt_end.timestamp()):
    try:
        resp = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            start=current * 1000,
            limit=limit
        )
        klines = resp.get("result", {}).get("list", [])
        if not klines:
            print("⚠️ Nenhum dado retornado. Parando...")
            break

        # Ordena e acumula candles
        klines = sorted(klines, key=lambda x: int(x[0]))
        for k in klines:
            candles.append([
                int(k[0]),
                float(k[1]), float(k[2]), float(k[3]),
                float(k[4]), float(k[5]), float(k[6])
            ])

        ultimo_ts = int(klines[-1][0]) // 1000
        print(f"⬇️  {len(candles)} candles baixados até {datetime.utcfromtimestamp(ultimo_ts)}")

        # Avança para o próximo bloco de 1000 candles
        current = ultimo_ts + int(interval) * 60
        time.sleep(0.2)  # Pausa leve anti-rate-limit
    except Exception as e:
        print(f"❌ Erro: {e}")
        time.sleep(2)
        continue

# Salva no CSV
df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
df["time"] = pd.to_datetime(df["time"], unit="ms")
df.to_csv(filename, index=False)

print(f"\n✅ Download concluído! Salvo como {filename} ({len(df)} candles)")
print(f"⏱️ Tempo total: {round(time.time() - t0, 1)}s")
