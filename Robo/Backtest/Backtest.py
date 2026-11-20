import os
import pandas as pd
from dotenv import load_dotenv

# Carregar variáveis do seu .env (inclui rigor, modo, chave API, etc)
load_dotenv(dotenv_path=".env", override=True)

from backtesting_geral import executar_backtest_completo

# === 1. Carregue seus candles de 1 ano (recomendado: 2024 inteiro) ===
df = pd.read_csv("market_data_1ano.csv")  # Troque pelo seu arquivo real!
df["time"] = pd.to_datetime(df["time"])

# === 2. Separar por mês ===
df["year_month"] = df["time"].dt.strftime("%Y-%m")

resultados = []
capital_inicial = 10000

for ym, df_mes in df.groupby("year_month"):
    print(f"\n\n🔎 Backtestando mês: {ym}  ---  candles: {len(df_mes)}")
    melhor, relatorio = executar_backtest_completo(df_mes, capital_inicial=capital_inicial)
    if melhor and isinstance(melhor, dict):
        resultado = {
            "mes": ym,
            "lucro": melhor.get("lucro"),
            "retorno_pct": melhor.get("retorno_pct"),
            "win_rate": melhor.get("win_rate"),
            "max_drawdown": melhor.get("max_drawdown"),
            "num_trades": melhor.get("num_trades"),
        }
    else:
        resultado = {"mes": ym, "lucro": None, "retorno_pct": None}
    resultados.append(resultado)

# === 3. Consolidado Anual ===
df_resultados = pd.DataFrame(resultados)
print("\n\n==== RESULTADOS MENSAIS ====")
print(df_resultados)

lucro_total = df_resultados["lucro"].sum()
print(f"\n💰 Lucro total no ano: {lucro_total:.2f}")

# (Opcional) Salve o resultado anual para análise posterior
df_resultados.to_csv("backtest_resultados_mensais.csv", index=False)
