from estrategia_market_making import criar_backtest_marketmaking
import pandas as pd

df = pd.read_csv("market_data.csv")
resultado = criar_backtest_marketmaking(df)

print(resultado)
