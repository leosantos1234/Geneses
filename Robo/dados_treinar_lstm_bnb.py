import requests
import pandas as pd
from datetime import datetime, timedelta
import time

# ===============================
# CONFIGURAÇÕES
# ===============================
SYMBOL = "BNBUSDT"
INTERVAL = 5  # minutos
OUTFILE = f"market_data_{SYMBOL}_{INTERVAL}m.csv"

# Bybit endpoint
URL = "https://api.bybit.com/v5/market/kline"

# Quantos dias você quer baixar?
DIAS = 200  # pode aumentar se quiser


# ===============================
# FUNÇÃO PARA BAIXAR CANDLES
# ===============================
def baixar_candles(symbol, interval, dias):
    print(f"📥 Baixando dados do {symbol} nos últimos {dias} dias...")
    
    fim = int(time.time() * 1000)
    inicio = int((datetime.utcnow() - timedelta(days=dias)).timestamp() * 1000)

    todos = []

    while True:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": str(interval),
            "start": inicio,
            "end": fim,
            "limit": 1000
        }

        r = requests.get(URL, params=params)
        data = r.json()

        if data.get("retCode") != 0:
            print("❌ Erro da API:", data)
            break

        candles = data["result"]["list"]
        if not candles:
            break

        todos.extend(candles)

        print(f"✔ +{len(candles)} candles baixados... (total: {len(todos)})")

        # Atualiza janela do próximo batch
        ultimo_ts = int(candles[-1][0])
        fim = ultimo_ts - 1

        # segurança para não sobrecarregar
        time.sleep(0.1)

    return todos


# ===============================
# PROCESSAMENTO E SALVAMENTO
# ===============================
def salvar_csv(candles):
    print("🔧 Convertendo dados...")

    df = pd.DataFrame(candles, columns=[
        "time", "open", "high", "low", "close", "volume", "turnover"
    ])

    # Convertendo tipos numéricos
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Ordena por tempo ascendente
    df["time"] = pd.to_datetime(df["time"], unit="ms")
    df = df.sort_values("time").reset_index(drop=True)

    # Remove duplicados
    df = df.drop_duplicates(subset=["time"])

    df.to_csv(OUTFILE, index=False)
    print(f"💾 Arquivo salvo: {OUTFILE}")
    print(df.tail())



# ===============================
# EXECUÇÃO
# ===============================
if __name__ == "__main__":
    candles = baixar_candles(SYMBOL, INTERVAL, DIAS)
    salvar_csv(candles)
    print("🎉 Download completo!")
