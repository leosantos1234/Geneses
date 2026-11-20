import pandas as pd
from datetime import datetime, timedelta
import numpy as np

# Simula 10 candles de 5 minutos para BTCUSDT
now = datetime.now()
data = []
base_price = 68000

for i in range(10):
    t = now - timedelta(minutes=5 * (9 - i))
    open_ = base_price + np.random.uniform(-1000, 1000)
    high = open_ + np.random.uniform(0, 500)
    low = open_ - np.random.uniform(0, 500)
    close = np.random.uniform(low, high)
    volume = np.random.uniform(1, 100)
    turnover = close * volume
    data.append([
        int(t.timestamp() * 1000),  # time em ms
        round(open_, 2),
        round(high, 2),
        round(low, 2),
        round(close, 2),
        round(volume, 4),
        round(turnover, 2)
    ])

df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume", "turnover"])
csv_path = "/mnt/data/market_data_BTCUSDT_5m.csv"
df.to_csv(csv_path, index=False)

csv_path
