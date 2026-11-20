import pandas as pd
from pybit.unified_trading import HTTP

client = HTTP(testnet=False)

def baixar_btc_5m():
    print("⏳ Baixando dados do SOLUSDT (5m)...")

    dados = client.get_kline(
        category="linear",
        symbol="SOLUSDT",
        interval="5",
        limit=2000
    )["result"]["list"]

    df = pd.DataFrame(dados)
    df.columns = ["start","open","high","low","close","volume","turnover"]
    df = df.astype(float)
    df["time"] = pd.to_datetime(df["start"], unit="ms")

    df = df.sort_values("time")
    df.to_csv("sol_5m.csv", index=False)

    print("✅ Arquivo salvo: sol_5m.csv")
    return df

if __name__ == "__main__":
    baixar_btc_5m()
