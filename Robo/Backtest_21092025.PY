import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# === 1. Carregar dados ===
arquivo = "market_data_BTCUSDT_5min_1ano.csv"

df = pd.read_csv(
    arquivo,
    sep=",",
    parse_dates=["time"],
    infer_datetime_format=True,
    on_bad_lines="skip"
)

# Garantir ordenação e limpeza
df["time"] = pd.to_datetime(df["time"], errors="coerce")
df = df.dropna(subset=["time"]).drop_duplicates(subset=["time"])
df = df.sort_values("time").reset_index(drop=True)

# === 2. Estratégia simples (exemplo: breakout 20 candles) ===
capital_inicial = 1000
capital = capital_inicial
posicao = 0
entry_price = 0
entry_time = None

trades = []

for i in range(20, len(df)):
    high_last = df["high"].iloc[i-20:i].max()
    low_last = df["low"].iloc[i-20:i].min()
    close = df["close"].iloc[i]

    if posicao == 0 and close > high_last:
        posicao = 1
        entry_price = close
        entry_time = df["time"].iloc[i]

    elif posicao == 0 and close < low_last:
        posicao = -1
        entry_price = close
        entry_time = df["time"].iloc[i]

    elif posicao == 1:
        if close >= entry_price * 1.01 or close <= entry_price * 0.995:
            pnl = (close - entry_price) / entry_price * capital
            capital += pnl
            trades.append({
                "side": "LONG",
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": df["time"].iloc[i],
                "exit_price": close,
                "pnl": pnl,
                "capital": capital
            })
            posicao = 0

    elif posicao == -1:
        if close <= entry_price * 0.99 or close >= entry_price * 1.005:
            pnl = (entry_price - close) / entry_price * capital
            capital += pnl
            trades.append({
                "side": "SHORT",
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": df["time"].iloc[i],
                "exit_price": close,
                "pnl": pnl,
                "capital": capital
            })
            posicao = 0

# === 3. Converter trades em DataFrame ===
trades_df = pd.DataFrame(trades)

# === 4. Estatísticas ===
data_inicio = df["time"].iloc[0]
data_fim = df["time"].iloc[-1]
total_trades = len(trades_df)
wins = trades_df[trades_df["pnl"] > 0]
losses = trades_df[trades_df["pnl"] <= 0]
win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
rrr_medio = wins["pnl"].mean() / abs(losses["pnl"].mean()) if len(losses) > 0 else np.nan

# Drawdown
capital_series = trades_df["capital"]
dd_series = capital_series.cummax() - capital_series
max_drawdown = dd_series.max()

# Sharpe Ratio (simplificado: retorno médio / desvio dos retornos)
retornos = trades_df["pnl"] / capital_inicial
sharpe = (retornos.mean() / retornos.std()) * np.sqrt(252) if retornos.std() > 0 else np.nan

# Performance mensal
trades_df["month"] = trades_df["exit_time"].dt.to_period("M")
monthly_perf = trades_df.groupby("month")["pnl"].sum()

# === 5. Relatório ===
print("📊 Resultados do Backtest")
print(f"Período: {data_inicio.date()} → {data_fim.date()}")
print(f"Trades: {total_trades}")
print(f"Win Rate: {win_rate:.2f}%")
print(f"R:R Médio: {rrr_medio:.2f}")
print(f"Lucro Líquido: {capital - capital_inicial:.2f}")
print(f"Capital Final: {capital:.2f}")
print(f"Max Drawdown: {max_drawdown:.2f}")
print(f"Sharpe Ratio: {sharpe:.2f}")

print("\n📅 Performance Mensal:")
print(monthly_perf)

# === 6. Gráficos ===
plt.figure(figsize=(14,8))

# Capital acumulado
plt.subplot(3,1,1)
plt.plot(trades_df["exit_time"], trades_df["capital"], label="Capital acumulado", color="blue")
plt.title("Evolução do Capital")
plt.xlabel("Data")
plt.ylabel("Capital")
plt.grid(True)
plt.legend()

# Drawdown
plt.subplot(3,1,2)
plt.fill_between(trades_df["exit_time"], dd_series, color="red", alpha=0.4)
plt.title("Drawdown ao longo do tempo")
plt.xlabel("Data")
plt.ylabel("Drawdown")
plt.grid(True)

# Performance mensal
plt.subplot(3,1,3)
monthly_perf.plot(kind="bar", color="green")
plt.title("Performance Mensal")
plt.xlabel("Mês")
plt.ylabel("PnL")
plt.grid(True)

plt.tight_layout()
plt.show()
