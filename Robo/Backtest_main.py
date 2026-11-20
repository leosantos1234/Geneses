import os
import sys
import pandas as pd
from dotenv import load_dotenv

# ==== 1. Carregar ENV específico (multi-ativo) ====
dotenv_path = sys.argv[1] if len(sys.argv) > 1 else ".env"
if not os.path.exists(dotenv_path):
    print(f"❌ ENV não encontrado: {dotenv_path}")
    sys.exit(1)
load_dotenv(dotenv_path)

# ==== 2. Ler configs do .env ====
ATIVOS = os.getenv("ATIVOS", "BTCUSDT")
INTERVAL = os.getenv("INTERVAL", "5")
MODO_VALIDACAO = os.getenv("MODO_VALIDACAO", "agressivo")
ARQUIVO = "market_data_BTCUSDT_5min_1ano.csv"
CAPITAL_INICIAL = int(os.getenv("CAPITAL_INICIAL", "10000"))

print(f"🟢 Backtest iniciado para: {ATIVOS} | Timeframe: {INTERVAL}min | Modo: {MODO_VALIDACAO}")

# ==== 3. Carregar dados históricos ====
try:
    df = pd.read_csv(ARQUIVO)
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"]).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
except Exception as e:
    print(f"❌ Erro ao carregar {ARQUIVO}: {e}")
    sys.exit(1)

# ==== 4. Calcular indicadores ====
from utils.indicadores import calculate_indicators
df = calculate_indicators(df)
if df is None or df.empty:
    print("❌ Indicadores não calculados corretamente. Abortando.")
    sys.exit(1)

# ==== 5. Executar backtest ====
from backtesting_geral import executar_backtest_completo
melhor, resultados = executar_backtest_completo(df, capital_inicial=CAPITAL_INICIAL)
print("Chaves do resultado melhor:", melhor.keys())

# ==== 6. Exibir relatório ====
import pandas as pd
resultados_df = pd.DataFrame(resultados)
colunas_interesse = ["estrategia", "direcao", "lucro", "retorno_pct", "win_rate", "num_trades", "num_wins", "num_losses", "max_drawdown", "lucro_medio", "perda_media"]
for col in colunas_interesse:
    if col not in resultados_df.columns:
        resultados_df[col] = None
print("\n==== RELATÓRIO DO BACKTEST ====")
print(resultados_df[colunas_interesse].to_string(index=False))
print("\nMelhor estratégia:", melhor)


# ... (depois do print("\nMelhor estratégia:", melhor))

# ==== 7. Exibir trades ruins organizados ====
if "trades" in melhor:
    # Função de normalização para garantir floats
    def normaliza_trade(trade):
        return {
            "data": str(trade["data"]),
            "side": trade["side"],
            "preco_entrada": float(trade["preco_entrada"]),
            "preco_saida": float(trade["preco_saida"]),
            "lucro": float(trade["lucro"]),
            "motivo_saida": trade["motivo_saida"]
        }

    trades_norm = [normaliza_trade(t) for t in melhor["trades"]]
    df_trades = pd.DataFrame(trades_norm)
    df_ruins = df_trades[df_trades["lucro"] < 0][["data", "side", "preco_entrada", "preco_saida", "lucro", "motivo_saida"]]

    print("\n==== TRADES RUINS (PREJUÍZO) ====")
    print(df_ruins.to_string(index=False, float_format="%.1f"))
else:
    print("\n⚠️ Detalhamento de trades não está disponível nesse backtest.")
