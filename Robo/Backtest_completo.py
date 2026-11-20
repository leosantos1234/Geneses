import pandas as pd
from backtesting_geral import executar_backtest_completo

# Carregar histórico salvo
df = pd.read_csv("market_data_BTCUSDT_5min_1ano.csv")
df["time"] = pd.to_datetime(df["time"])

# Rodar o backtest completo
melhor, relatorio = executar_backtest_completo(df, capital_inicial=1000)

print("📊 Melhor estratégia encontrada:")
print(melhor)
print("\n📑 Relatório completo:")
for r in relatorio:
    print(r)
