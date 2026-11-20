print("=== IMPORTANDO executar_backtest_completo ===")
import os
import pandas as pd
from estrategia_entrada.backtesting_geral import executar_backtest_completo, carregar_dados_historicos
from sessao_bybit import session

# PATCH para limpar variáveis antigas
for key in [
    "SR_LOOKBACK", "SR_LOOKBACK_GRID", "ATR_MIN", "ATR_MIN_GRID",
    "RRR", "RRR_GRID", "ATR_MULT", "ATR_MULT_GRID"
]:
    os.environ.pop(key, None)

# Parâmetros apenas do grid search, sempre como string
os.environ["SCORE_MINIMO_AGRESSIVO"] = "7"
os.environ["SCORE_PCT_AGRESSIVO"] = "65"
os.environ["PESO_VOLUME_GRID"] = "1.3"
os.environ["THRESHOLD_VOLUME_FORTE_GRID"] = "1.05"
os.environ["RRR_GRID"] = "1.6"
os.environ["ATR_MIN_GRID"] = "180"
os.environ["SR_LOOKBACK_GRID"] = "12"  # INTEIRO!

SYMBOL = "BTCUSDT"
INTERVAL = "15"
DATA_INICIO = "2025-01-01"
DATA_FIM = "2025-09-17"
CAPITAL_INICIAL = 10000

print(f"\n📦 Baixando histórico do Bybit de {DATA_INICIO} até {DATA_FIM}...")
df = carregar_dados_historicos(session, SYMBOL, interval=INTERVAL, limite=2000)
df['time'] = pd.to_datetime(df['time'], unit='ms')
df = df[(df['time'] >= DATA_INICIO) & (df['time'] <= DATA_FIM)].reset_index(drop=True)

print(f"\n🚀 Rodando backtest com o setup campeão...")
melhor, relatorio, trade_log = executar_backtest_completo(df, capital_inicial=CAPITAL_INICIAL)

if trade_log and isinstance(trade_log, list) and len(trade_log) > 0:
    pd.DataFrame(trade_log).to_csv("trade_logs_combinados.csv", index=False)
    print("\n✅ Log detalhado salvo em trade_logs_combinados.csv!")
else:
    print("\n❌ Nenhum trade log gerado.")

if relatorio and isinstance(relatorio, list) and len(relatorio) > 0:
    pd.DataFrame(relatorio).to_csv("relatorio_geral_backtest.csv", index=False)
    print("✅ Relatório geral salvo em relatorio_geral_backtest.csv!")
else:
    print("❌ Nenhum relatório geral gerado.")
